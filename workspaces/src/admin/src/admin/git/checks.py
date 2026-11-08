"""
What the backup checks before it commits anything.

The user works in the same repository with their own git commands, so a
cycle only starts when the repository can be left to it for a moment: on
the configured branch, with no operation half finished and no other git
process holding the index (:func:`ensure_safe_to_sync`). Anything else stops
the cycle before it changes a thing, and the next cycle tries again.

The files about to be committed are checked too. A working tree that has
lost most of its files is not committed at all (:func:`ensure_no_mass_deletion`).
A file that is too large (:func:`large_files`) or still holds conflict
markers (:func:`staged_conflict_markers`) is held back on its own, so the
rest of the work is still backed up. Virtual environments, caches and
similar folders that are rebuilt rather than kept are never staged
(:func:`ensure_default_excludes`).
"""

import logging
import time
from pathlib import Path

from admin.git.command import list_paths, run_local
from admin.git.config import RepoConfig
from admin.git.errors import SyncError, SyncErrorKind

logger = logging.getLogger(__name__)

#: Files and directories in the git directory that git leaves behind while a
#: merge, cherry-pick, revert or rebase is waiting for the user.
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

#: Age in seconds after which an ``index.lock`` is taken to be left behind
#: by a git process that was killed, and is removed. Kept long, because a
#: live ``git commit`` holds the lock for as long as its editor is open.
STALE_LOCK_SECONDS = 3600

#: A cycle that would delete more than this share of the tracked files
#: refuses to commit, since an emptied working tree is far more likely an
#: accident than a clean-up.
MASS_DELETION_FRACTION = 0.5

#: Below this many deleted files the share is not checked, so a small
#: repository can still lose its only README.
MASS_DELETION_MIN_FILES = 10

#: Text ``git diff --check`` puts at the end of each line it reports for a
#: conflict marker, as opposed to a whitespace error.
CONFLICT_MARKER_WARNING = "leftover conflict marker"

#: Starts of the lines that make a reported file a real conflict: the
#: ``<<<<<<<`` and ``>>>>>>>`` lines. A bare ``=======`` is left out,
#: because a Markdown heading underline looks exactly like one.
CONFLICT_MARKER_PREFIXES = ("<<<<<<<", ">>>>>>>")

#: Largest file the backup commits, in bytes. Larger ones are held back:
#: forges refuse files around 100 MB, and a large binary bloats every clone
#: of the repository for good.
MAX_FILE_BYTES = 50 * 1024 * 1024

#: Patterns the backup never stages, for folders that are rebuilt rather
#: than kept and would otherwise fill the repository: virtual environments,
#: caches and notebook checkpoints. Files already committed stay tracked.
DEFAULT_EXCLUDES = (
    ".venv/",
    "venv/",
    "__pycache__/",
    "*.py[cod]",
    ".ipynb_checkpoints/",
    ".pytest_cache/",
    ".mypy_cache/",
    "node_modules/",
    ".DS_Store",
    "Thumbs.db",
    "*.swp",
)

#: Lines framing the patterns the backup owns in ``info/exclude``. Anything
#: outside them is the user's and kept as it is.
EXCLUDE_BLOCK_START = "# >>> workspace-admin defaults (rewritten by the backup)"
EXCLUDE_BLOCK_END = "# <<< workspace-admin defaults"


def _operation_in_progress(git_dir: Path) -> str | None:
    """
    Name the git operation the repository is stopped in the middle of.

    Args:
        git_dir: The repository's git directory.

    Returns:
        The first entry of ``IN_PROGRESS_MARKERS`` that exists, or None
        when no operation is in progress.
    """
    for marker in IN_PROGRESS_MARKERS:
        if (git_dir / marker).exists():
            return marker
    return None


def _ensure_index_unlocked(repo: RepoConfig) -> None:
    """
    Refuse to start while another git process holds the index.

    A lock older than :data:`STALE_LOCK_SECONDS` is removed instead: it was
    left behind by a git process that was killed before it could clean up,
    and as long as it exists every command that writes the index fails, so
    the backup would never run again.

    Args:
        repo: Repository the lock belongs to.

    Raises:
        SyncError: If the lock is recent, or old but cannot be removed.
    """
    lock = repo.git_dir / INDEX_LOCK_FILE
    try:
        age = time.time() - lock.stat().st_mtime
    except FileNotFoundError:
        return
    except OSError as error:
        raise SyncError(
            f"Not synchronizing repository '{repo.name}': cannot inspect "
            f"{lock}: {error}",
            SyncErrorKind.INDEX_LOCKED,
        ) from error

    if age < STALE_LOCK_SECONDS:
        raise SyncError(
            f"Not synchronizing repository '{repo.name}': another git "
            f"process holds {lock}; trying again next cycle",
            SyncErrorKind.INDEX_LOCKED,
        )

    try:
        lock.unlink(missing_ok=True)
    except OSError as error:
        raise SyncError(
            f"Not synchronizing repository '{repo.name}': cannot remove the "
            f"stale {lock}: {error}",
            SyncErrorKind.INDEX_LOCKED,
        ) from error

    logger.warning(
        "Removed %s from repository '%s': it was %d seconds old, so the git "
        "process that created it is gone",
        lock,
        repo.name,
        age,
    )


def ensure_on_branch(repo: RepoConfig) -> None:
    """
    Refuse to touch a working tree that is not on the configured branch.

    The push always sends ``refs/heads/<branch>``, but a commit or merge
    lands on whatever ``HEAD`` is. After the user switched branches the
    backup commits would go to their branch and never be pushed, and on a
    detached ``HEAD`` - a checked-out tag or commit, ``git bisect`` - they
    would not be on any branch at all.

    Args:
        repo: Repository to inspect.

    Raises:
        SyncError: If ``HEAD`` is detached or names another branch.
    """
    result = run_local(
        repo,
        ["symbolic-ref", "--quiet", "HEAD"],
        "read the checked-out branch",
        check=False,
    )

    # With --quiet, exit status 1 and no output means a detached HEAD;
    # anything else non-zero is a real failure.
    if result.returncode == 1 and not result.stderr.strip():
        raise SyncError(
            f"Not synchronizing repository '{repo.name}': HEAD is detached; "
            f"check out branch '{repo.branch}' to resume the backup",
            SyncErrorKind.DETACHED_HEAD,
        )
    if result.returncode != 0:
        raise SyncError(
            f"Failed to read the checked-out branch for repository "
            f"'{repo.name}': {result.stderr.strip()}"
        )

    checked_out = result.stdout.strip()
    if checked_out != f"refs/heads/{repo.branch}":
        raise SyncError(
            f"Not synchronizing repository '{repo.name}': "
            f"'{checked_out.removeprefix('refs/heads/')}' is checked out, not "
            f"'{repo.branch}'; check out '{repo.branch}' to resume the backup",
            SyncErrorKind.WRONG_BRANCH,
        )


def unmerged_stages(repo: RepoConfig) -> dict[str, set[str]]:
    """
    List the files git still considers conflicted, with their stages.

    Read with ``-z``, so a path with unusual characters arrives as it is
    rather than quoted.

    Args:
        repo: Repository to inspect.

    Returns:
        Per conflicted path, in the order git reports them, the stages it
        has: ``1`` the common ancestor, ``2`` the local side, ``3`` the
        remote side.

    Raises:
        SyncError: If the list cannot be read.
    """
    result = run_local(
        repo, ["ls-files", "--unmerged", "-z"], "list the unmerged files"
    )

    stages: dict[str, set[str]] = {}
    for entry in result.stdout.split("\0"):
        if "\t" not in entry:
            continue
        info, path = entry.split("\t", 1)
        stages.setdefault(path, set()).add(info.split()[-1])
    return stages


def ensure_safe_to_sync(repo: RepoConfig) -> None:
    """
    Refuse to touch a repository the user is in the middle of using.

    This guards against every way of getting there, not just a failed
    merge of our own: the user may have started a merge or rebase by
    hand and not finished it yet, switched to another branch, or be
    running a git command right now. Committing would conclude a merge,
    conflict and all, or land on the wrong branch, and the push would hand
    it on to the remote.

    Args:
        repo: Repository to inspect.

    Raises:
        SyncError: If another git process holds the index, an operation is
            in progress, the configured branch is not checked out or files
            are unmerged.
    """
    _ensure_index_unlocked(repo)

    marker = _operation_in_progress(repo.git_dir)
    if marker:
        raise SyncError(
            f"Not synchronizing repository '{repo.name}': a git operation is "
            f"in progress ({marker}); finish or abort it first",
            SyncErrorKind.OPERATION_IN_PROGRESS,
        )

    ensure_on_branch(repo)

    unmerged = list(unmerged_stages(repo))
    if unmerged:
        raise SyncError(
            f"Not synchronizing repository '{repo.name}': unresolved "
            f"conflicts in {', '.join(unmerged)}",
            SyncErrorKind.UNRESOLVED_CONFLICTS,
        )


def ensure_no_mass_deletion(repo: RepoConfig) -> None:
    """
    Refuse to commit when most of the tracked files have disappeared.

    A working tree that has been emptied, wholly or mostly, is far more
    often an accident - a folder moved away, a restore that has not
    finished - than a clean-up, and committing it would delete those files
    on the remote too. Deleting that much on purpose takes one commit by
    hand, after which the backup carries on.

    Args:
        repo: Repository about to be committed in.

    Raises:
        SyncError: If more than :data:`MASS_DELETION_FRACTION` of the
            tracked files, and at least :data:`MASS_DELETION_MIN_FILES`, are
            missing, or the files cannot be counted.
    """
    deleted = len(list_paths(repo, ["--deleted"], "count the deleted files"))
    if deleted < MASS_DELETION_MIN_FILES:
        return

    tracked = len(list_paths(repo, ["--cached"], "count the tracked files"))
    if deleted <= tracked * MASS_DELETION_FRACTION:
        return

    raise SyncError(
        f"Not committing in repository '{repo.name}': {deleted} of {tracked} "
        f"tracked files are missing from {repo.work_tree}. If that is "
        f"intended, commit the deletion there by hand to resume the backup",
        SyncErrorKind.MASS_DELETION,
    )


def large_files(repo: RepoConfig) -> list[str]:
    """
    List the new or changed files larger than :data:`MAX_FILE_BYTES`.

    Checked before staging, because staging a very large file is itself
    what takes long enough to hit the time limit.

    Args:
        repo: Repository about to be committed in.

    Returns:
        The paths, relative to the working tree.

    Raises:
        SyncError: If the candidates cannot be listed.
    """
    candidates = list_paths(
        repo, ["--others", "--exclude-standard"], "list the new files"
    ) + list_paths(repo, ["--modified"], "list the changed files")

    too_large = []
    for path in dict.fromkeys(candidates):
        try:
            size = (repo.work_tree / path).lstat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            too_large.append(path)
    return too_large


def staged_conflict_markers(repo: RepoConfig) -> list[str]:
    """
    List the staged files that still hold conflict markers.

    ``git diff --check`` finds the candidates, but it also reports
    whitespace errors and a bare ``=======``, so only its conflict marker
    lines are kept, and only for files that contain a marker line no
    Markdown heading would. Since the candidates come from git, a
    ``conflict-marker-size`` attribute in ``.gitattributes`` is respected.

    Args:
        repo: Repository whose changes have just been staged.

    Returns:
        The affected paths, each once, in the order git reports them.

    Raises:
        SyncError: If the check or a staged file cannot be read.
    """
    result = run_local(
        repo,
        ["diff", "--cached", "--check"],
        "check the staged changes for conflict markers",
        check=False,
    )

    if result.returncode not in (0, 2):
        raise SyncError(
            f"Failed to check the staged changes for conflict markers in "
            f"repository '{repo.name}': {result.stderr.strip()}"
        )

    candidates = dict.fromkeys(
        line.rsplit(":", 2)[0]
        for line in result.stdout.splitlines()
        if line.endswith(CONFLICT_MARKER_WARNING)
    )

    return [path for path in candidates if _has_conflict_marker(repo, path)]


def _has_conflict_marker(repo: RepoConfig, path: str) -> bool:
    """
    Check whether the staged version of a file holds a conflict marker.

    Args:
        repo: Repository the file is staged in.
        path: Path of the file, relative to the working tree.

    Returns:
        True if a line starts with ``<<<<<<<`` or ``>>>>>>>``.

    Raises:
        SyncError: If the staged file cannot be read.
    """
    result = run_local(repo, ["show", f":{path}"], "read a staged file")
    return any(
        line.startswith(CONFLICT_MARKER_PREFIXES)
        for line in result.stdout.splitlines()
    )


def _exclude_block() -> str:
    """Return the backup's own block of ``info/exclude``, newline-terminated."""
    return "\n".join([EXCLUDE_BLOCK_START, *DEFAULT_EXCLUDES, EXCLUDE_BLOCK_END]) + "\n"


def _without_exclude_block(text: str) -> str:
    """Return ``info/exclude`` with the backup's own block taken out."""
    kept = []
    inside = False
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if stripped == EXCLUDE_BLOCK_START:
            inside = True
        elif stripped == EXCLUDE_BLOCK_END:
            inside = False
        elif not inside:
            kept.append(line)
    result = "".join(kept)
    return result if not result or result.endswith("\n") else result + "\n"


def ensure_default_excludes(repo: RepoConfig) -> None:
    """
    Keep :data:`DEFAULT_EXCLUDES` in the repository's ``info/exclude``.

    ``info/exclude`` lives in the git directory, so unlike a
    ``.gitignore`` it is neither committed nor visible in the working
    tree. The user's own lines in it are kept. A file that cannot be
    written is logged rather than raised: the backup still works, it only
    stages more than it should.

    Args:
        repo: Repository about to be committed in.
    """
    exclude = repo.git_dir / "info" / "exclude"
    try:
        current = exclude.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""
    except OSError as error:
        logger.warning("Cannot read %s: %s", exclude, error)
        return

    wanted = _without_exclude_block(current) + _exclude_block()
    if wanted == current:
        return

    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(wanted, encoding="utf-8")
    except OSError as error:
        logger.warning("Cannot write the default excludes to %s: %s", exclude, error)
