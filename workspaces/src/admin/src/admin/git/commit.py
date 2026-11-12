"""
Committing the user's changes, minus the files that must not be committed.

What is checked lives in :mod:`admin.git.checks`.
"""

import logging
import time

from admin.git.checks import (
    MAX_FILE_BYTES,
    ensure_default_excludes,
    ensure_no_mass_deletion,
    ensure_safe_to_sync,
    large_files,
    staged_conflict_markers,
)
from admin.git.command import run_local
from admin.git.config import RepoConfig
from admin.git.errors import SyncError, SyncErrorKind

logger = logging.getLogger(__name__)

#: Author and committer name of every backup commit.
COMMIT_AUTHOR_NAME = "workspace-admin"

#: Author and committer email of every backup commit.
COMMIT_AUTHOR_EMAIL = "workspace-admin@localhost"

#: Start of every backup commit's message, followed by a UTC timestamp.
COMMIT_MESSAGE_PREFIX = "workspace backup"


class HeldBack(SyncError):
    """
    Raised when some files were left out of a commit.

    Attributes:
        committed: Whether the other files were committed.
        marked: Files held back for conflict markers.
        too_large: Files held back for their size.
    """

    def __init__(
        self,
        message: str,
        kind: SyncErrorKind,
        committed: bool,
        held: tuple[list[str], list[str]] = ([], []),
    ):
        super().__init__(message, kind)
        self.committed = committed
        self.marked, self.too_large = held


def identity_options() -> list[str]:
    """Return the ``-c user.name=...`` and ``-c user.email=...`` options."""
    return [
        "-c",
        f"user.name={COMMIT_AUTHOR_NAME}",
        "-c",
        f"user.email={COMMIT_AUTHOR_EMAIL}",
    ]


def has_changes(repo: RepoConfig) -> bool:
    """
    Check for modified, deleted or untracked files.

    Raises:
        SyncError: If ``git status`` fails.
    """
    result = run_local(repo, ["status", "--porcelain"], "read the status")
    return bool(result.stdout.strip())


def build_commit_message() -> str:
    """Return the message, for example ``workspace backup 2026-09-22T12:30:00Z``."""
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return f"{COMMIT_MESSAGE_PREFIX} {timestamp}"


def _stage(repo: RepoConfig, held_back: list[str]) -> None:
    """Stage every change except ``held_back``, paths relative to the tree."""
    args = ["add", "-A"]
    if held_back:
        args += ["--", ":/", *(f":(exclude,top,literal){path}" for path in held_back)]
    run_local(repo, args, "stage changes")


def _unstage(repo: RepoConfig, paths: list[str]) -> None:
    """Unstage ``paths``, so a file deleted by hand leaves no staged addition."""
    if paths:
        run_local(repo, ["reset", "--quiet", "--", *paths], "unstage the refused files")


def _has_staged_changes(repo: RepoConfig) -> bool:
    """
    Check whether the index differs from ``HEAD``.

    Raises:
        SyncError: If the check fails.
    """
    result = run_local(
        repo, ["diff", "--cached", "--quiet"], "check for staged changes", check=False
    )
    if result.returncode not in (0, 1):
        raise SyncError(
            f"Failed to check for staged changes in repository '{repo.name}': "
            f"{result.stderr.strip()}"
        )
    return result.returncode == 1


def _commit_staged(repo: RepoConfig) -> bool:
    """Commit the index if it holds anything; return whether it did."""
    if not _has_staged_changes(repo):
        return False
    run_local(
        repo,
        ["commit", "-m", build_commit_message()],
        "commit changes",
        options=identity_options(),
    )
    logger.info("Committed changes in repository '%s'", repo.name)
    return True


def _held_back_error(
    repo: RepoConfig, marked: list[str], too_large: list[str], committed: bool
) -> HeldBack:
    """Describe the held-back files; conflict markers decide the kind."""
    parts = []
    if marked:
        parts.append(f"conflict markers left in {', '.join(marked)}")
    if too_large:
        parts.append(
            f"larger than {MAX_FILE_BYTES // 2**20} MiB: {', '.join(too_large)}"
        )
    kind = SyncErrorKind.CONFLICT_MARKERS if marked else SyncErrorKind.FILE_TOO_LARGE
    return HeldBack(
        f"Not committing some files in repository '{repo.name}': " + "; ".join(parts),
        kind,
        committed,
        (marked, too_large),
    )


def commit_local_changes(repo: RepoConfig) -> bool:
    """
    Commit the user's changes, holding back large and marked files.

    Nothing is committed while the user is busy with the repository
    (:func:`admin.git.checks.ensure_safe_to_sync`) or most tracked files
    are missing.

    Returns:
        True if a commit was made.

    Raises:
        HeldBack: If files were left out; the rest is committed.
        SyncError: If a check or git command fails.
    """
    ensure_safe_to_sync(repo)
    ensure_default_excludes(repo)
    if not has_changes(repo):
        logger.info("No changes in repository '%s'; nothing to commit", repo.name)
        return False

    ensure_no_mass_deletion(repo)
    too_large = large_files(repo)
    _stage(repo, too_large)
    marked = staged_conflict_markers(repo)
    _unstage(repo, marked)
    committed = _commit_staged(repo)

    if marked or too_large:
        error = _held_back_error(repo, marked, too_large, committed)
        logger.warning("%s", error)
        raise error
    return committed
