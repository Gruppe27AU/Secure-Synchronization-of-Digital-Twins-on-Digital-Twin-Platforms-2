"""
Whether the user is busy with a repository, so a cycle must not start.

A cycle starts only on the configured branch, with no merge, rebase,
cherry-pick or revert unfinished, no unresolved file, and no other git
process holding the index. Otherwise it stops before changing anything.
"""

import logging
import time
from pathlib import Path

from admin.git.run.command import run_local
from admin.git.setup.config import RepoConfig
from admin.git.run.errors import SyncError, SyncErrorKind

logger = logging.getLogger(__name__)

#: What git leaves in the git directory while an operation waits for the user.
IN_PROGRESS_MARKERS = (
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "REBASE_HEAD",
    "rebase-merge",
    "rebase-apply",
)

#: Lock file every git command that writes the index creates first.
INDEX_LOCK_FILE = "index.lock"

#: Age in seconds after which an ``index.lock`` is taken as left by a killed
#: process and removed. Long, because ``git commit`` holds it while its
#: editor is open.
STALE_LOCK_SECONDS = 3600


def _not_syncing(repo: RepoConfig, reason: str, kind: SyncErrorKind) -> SyncError:
    """Return the error for a repository left alone this cycle."""
    return SyncError(f"Not synchronizing repository '{repo.name}': {reason}", kind)


def _operation_in_progress(git_dir: Path) -> str | None:
    """Return the first :data:`IN_PROGRESS_MARKERS` entry present, or None."""
    for marker in IN_PROGRESS_MARKERS:
        if (git_dir / marker).exists():
            return marker
    return None


def _remove_stale_lock(repo: RepoConfig, lock: Path, age: float) -> None:
    """
    Remove a lock left by a killed git process.

    Raises:
        SyncError: If it cannot be removed.
    """
    try:
        lock.unlink(missing_ok=True)
    except OSError as error:
        raise _not_syncing(
            repo, f"cannot remove the stale {lock}: {error}", SyncErrorKind.INDEX_LOCKED
        ) from error
    logger.warning(
        "Removed %s from repository '%s': it was %d seconds old, so the git "
        "process that created it is gone",
        lock,
        repo.name,
        age,
    )


def _ensure_index_unlocked(repo: RepoConfig) -> None:
    """
    Refuse while another git process holds the index; remove a stale lock.

    Raises:
        SyncError: If the lock is recent, or cannot be inspected or removed.
    """
    lock = repo.git_dir / INDEX_LOCK_FILE
    try:
        age = time.time() - lock.stat().st_mtime
    except FileNotFoundError:
        return
    except OSError as error:
        raise _not_syncing(
            repo, f"cannot inspect {lock}: {error}", SyncErrorKind.INDEX_LOCKED
        ) from error
    if age < STALE_LOCK_SECONDS:
        raise _not_syncing(
            repo,
            f"another git process holds {lock}; trying again next cycle",
            SyncErrorKind.INDEX_LOCKED,
        )
    _remove_stale_lock(repo, lock, age)


def _checked_out_ref(repo: RepoConfig) -> str:
    """
    Return the ref ``HEAD`` points at.

    Raises:
        SyncError: ``detached_head`` if ``HEAD`` is detached, or if it
            cannot be read.
    """
    result = run_local(
        repo, ["symbolic-ref", "--quiet", "HEAD"], "read the checked-out branch", check=False
    )
    # With --quiet, status 1 and no output means a detached HEAD.
    if result.returncode == 1 and not result.stderr.strip():
        raise _not_syncing(
            repo,
            f"HEAD is detached; check out branch '{repo.branch}' to resume the backup",
            SyncErrorKind.DETACHED_HEAD,
        )
    if result.returncode != 0:
        raise SyncError(
            f"Failed to read the checked-out branch for repository "
            f"'{repo.name}': {result.stderr.strip()}"
        )
    return result.stdout.strip()


def ensure_on_branch(repo: RepoConfig) -> None:
    """
    Refuse unless the configured branch is checked out.

    A commit lands on ``HEAD`` but the push sends the configured branch, so
    work on another branch would never be pushed.

    Raises:
        SyncError: ``detached_head`` or ``wrong_branch``.
    """
    checked_out = _checked_out_ref(repo)
    if checked_out != f"refs/heads/{repo.branch}":
        raise _not_syncing(
            repo,
            f"'{checked_out.removeprefix('refs/heads/')}' is checked out, not "
            f"'{repo.branch}'; check out '{repo.branch}' to resume the backup",
            SyncErrorKind.WRONG_BRANCH,
        )


def unmerged_stages(repo: RepoConfig) -> dict[str, set[str]]:
    """
    List the conflicted files and the stages git holds of each.

    Returns:
        Per path, in git's order: ``1`` ancestor, ``2`` local, ``3`` remote.

    Raises:
        SyncError: If the list cannot be read.
    """
    result = run_local(repo, ["ls-files", "--unmerged", "-z"], "list the unmerged files")
    stages: dict[str, set[str]] = {}
    for entry in result.stdout.split("\0"):
        if "\t" not in entry:
            continue
        info, path = entry.split("\t", 1)
        stages.setdefault(path, set()).add(info.split()[-1])
    return stages


def ensure_safe_to_sync(repo: RepoConfig) -> None:
    """
    Refuse while the user is busy with the repository.

    Raises:
        SyncError: ``index_locked``, ``operation_in_progress``,
            ``detached_head``, ``wrong_branch`` or ``unresolved_conflicts``.
    """
    _ensure_index_unlocked(repo)
    marker = _operation_in_progress(repo.git_dir)
    if marker:
        raise _not_syncing(
            repo,
            f"a git operation is in progress ({marker}); finish or abort it first",
            SyncErrorKind.OPERATION_IN_PROGRESS,
        )
    ensure_on_branch(repo)
    unmerged = list(unmerged_stages(repo))
    if unmerged:
        raise _not_syncing(
            repo,
            f"unresolved conflicts in {', '.join(unmerged)}",
            SyncErrorKind.UNRESOLVED_CONFLICTS,
        )
