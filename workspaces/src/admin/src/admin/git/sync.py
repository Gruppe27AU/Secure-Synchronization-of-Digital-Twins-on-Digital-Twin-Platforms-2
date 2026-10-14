"""
Committing and pushing workspace changes back to the remote.

Consumes :class:`admin.git.config.RepoConfig` and keeps one already-cloned
repository in sync with its remote: commit what the user changed, merge in
what the remote changed, and push the result.

Local work always wins. When both sides changed the same file, the merge
keeps the workspace's version, because the user is sitting in front of
that file and did not ask for it to be replaced. Remote changes to *other*
files are still merged in as normal.

Two settings that :mod:`admin.git.clone` intentionally leaves out of the
repository's own config are supplied per command instead, so neither is
ever written to disk in the workspace:

- the commit identity, without which ``git commit`` refuses to run,
- the ``Authorization`` header, without which ``git push`` is anonymous.

Every command that names the branch puts ``--`` before its positional
arguments and spells the branch out as a full refspec, so a branch value can
never be read as a git option such as ``--upload-pack``.

The user works in the same repository with their own git commands, so a
cycle only starts when the repository can be left to it for a moment: on
the configured branch, with no operation half finished and no other git
process holding the index. Anything else stops the cycle before it changes
a thing, and the next cycle tries again.

Not everything in a working tree belongs in its history: see
:mod:`admin.git.staging` for what the backup leaves out of its commits.
"""

import logging
import subprocess
import threading
import time
from enum import StrEnum
from pathlib import Path

from admin.git.auth import AUTH_FAILURE_HINT, is_auth_failure, remote_options
from admin.git.config import RepoConfig
from admin.git.staging import (
    MAX_FILE_SIZE_BYTES,
    literal_excludes,
    oversized_files,
    parse_status,
    write_default_excludes,
)
from admin.git.runner import (
    LOCAL_TIMEOUT_SECONDS,
    REMOTE_TIMEOUT_SECONDS,
    GitTimeoutError,
    run_git,
)

logger = logging.getLogger(__name__)

COMMIT_AUTHOR_NAME = "workspace-admin"
COMMIT_AUTHOR_EMAIL = "workspace-admin@localhost"

COMMIT_MESSAGE_PREFIX = "workspace backup"

IN_PROGRESS_MARKERS = (
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "REBASE_HEAD",
    "rebase-merge",
    "rebase-apply",
)

CONFLICT_MARKER_WARNING = "leftover conflict marker"

CONFLICT_MARKER_PREFIXES = ("<<<<<<<", ">>>>>>>")

#: Lock file every git command that writes the index creates first.
INDEX_LOCK_FILE = "index.lock"

#: Age in seconds after which an ``index.lock`` is taken to be left behind
#: by a git process that was killed, and is removed. Kept long, because a
#: live ``git commit`` holds the lock for as long as its editor is open.
STALE_LOCK_SECONDS = 3600

class SyncErrorKind(StrEnum):
    """
    Why a synchronization failed, coarse enough to show to anyone.

    The message of a :class:`SyncError` can hold git's own output and the
    names of the user's files. The kind holds neither, so it is the only
    part of a failure that may leave the process through the HTTP API.
    """

    AUTH_FAILED = "auth_failed"
    GIT_FAILED = "git_failed"
    OPERATION_IN_PROGRESS = "operation_in_progress"
    WRONG_BRANCH = "wrong_branch"
    DETACHED_HEAD = "detached_head"
    INDEX_LOCKED = "index_locked"
    UNRESOLVED_CONFLICTS = "unresolved_conflicts"
    CONFLICT_MARKERS = "conflict_markers"
    UNEXPECTED = "unexpected"


class SyncError(Exception):
    """
    Raised when committing or pushing a repository fails.

    Attributes:
        kind: Why it failed, safe to expose where the message is not.
    """

    def __init__(self, message: str, kind: SyncErrorKind = SyncErrorKind.GIT_FAILED):
        super().__init__(message)
        self.kind = kind


class SyncStopped(Exception):
    """
    Raised when a cycle is given up between two steps because the service
    is stopping.

    Not a :class:`SyncError`: nothing went wrong, and every step leaves the
    repository consistent, so the next start carries on from there.
    """


def _identity_options() -> list[str]:
    """
    Build the git options that give the backup commits an author.

    Returns:
        The ``-c user.name=...`` and ``-c user.email=...`` pairs.
    """
    return [
        "-c",
        f"user.name={COMMIT_AUTHOR_NAME}",
        "-c",
        f"user.email={COMMIT_AUTHOR_EMAIL}",
    ]


def _execute(
    repo: RepoConfig,
    command: list[str],
    action: str,
    check: bool,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """
    Run a built git command and turn its failures into :class:`SyncError`.

    Args:
        repo: Repository the command is for.
        command: The full command, starting with ``git``.
        action: What the command is doing, used in error messages.
        check: Raise when git exits with a non-zero status.
        timeout: Seconds the command may take.

    Returns:
        The completed process. When ``check`` is False, this may describe
        a failed command.

    Raises:
        SyncError: If git exits with a non-zero status and ``check`` is
            True, or does not finish in time whatever ``check`` is.
    """
    try:
        result = run_git(command, timeout)
    except GitTimeoutError as error:
        logger.error(
            "Failed to %s for repository '%s': %s", action, repo.name, error
        )
        raise SyncError(
            f"Failed to {action} for repository '{repo.name}': {error}"
        ) from error

    if check and result.returncode != 0:
        reason = result.stderr.strip() or result.stdout.strip()
        logger.error(
            "Failed to %s for repository '%s': %s", action, repo.name, reason
        )
        message = f"Failed to {action} for repository '{repo.name}': {reason}"
        kind = SyncErrorKind.GIT_FAILED
        if is_auth_failure(reason):
            kind = SyncErrorKind.AUTH_FAILED
            if repo.token:
                message += f" ({AUTH_FAILURE_HINT})"
        raise SyncError(message, kind)

    return result


def _run_git(
    repo: RepoConfig,
    args: list[str],
    action: str,
    options: list[str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """
    Run one local git command inside the repository's working tree.

    Args:
        repo: Repository to run the command for.
        args: The git subcommand and its arguments, for example
            ``["status", "--porcelain"]``.
        action: What the command is doing, used in error messages.
        options: Git options placed before the subcommand, such as the
            commit identity.
        check: Raise when git fails. Pass False for a command whose
            failure is an expected outcome rather than an error, such as
            the merge that is used to detect conflicts.

    Returns:
        The completed process. When ``check`` is False, this may describe
        a failed command.

    Raises:
        SyncError: If git exits with a non-zero status and ``check`` is
            True, or does not finish within
            :data:`admin.git.runner.LOCAL_TIMEOUT_SECONDS` whatever
            ``check`` is.
    """
    command = ["git", "-C", str(repo.work_tree), *(options or []), *args]
    return _execute(repo, command, action, check, LOCAL_TIMEOUT_SECONDS)


def _run_remote_git(
    repo: RepoConfig,
    args: list[str],
    action: str,
) -> subprocess.CompletedProcess[str]:
    """
    Run one git command that talks to the remote.

    Always adds :func:`admin.git.auth.remote_options` - https only, no
    credential helpers, stalled transfers abandoned, the credentials
    header - and allows :data:`admin.git.runner.REMOTE_TIMEOUT_SECONDS`.

    Args:
        repo: Repository to run the command for.
        args: The git subcommand and its arguments, for example
            ``["push", "--", "origin", "refs/heads/main:refs/heads/main"]``.
        action: What the command is doing, used in error messages.

    Returns:
        The completed, successful process.

    Raises:
        SyncError: If git fails or does not finish in time.
    """
    command = [
        "git",
        "-C",
        str(repo.work_tree),
        *remote_options(repo),
        *args,
    ]
    return _execute(repo, command, action, True, REMOTE_TIMEOUT_SECONDS)


def _changed_files(repo: RepoConfig) -> list[tuple[str, str]]:
    """
    List every changed, deleted or untracked file in the working tree.

    ``--untracked-files=all`` names each file inside a new directory, not
    just the directory, so every file can be size-checked; ``-z`` gives the
    names exactly, unquoted.

    Args:
        repo: Repository to inspect.

    Returns:
        One ``(code, path)`` pair per file, see
        :func:`admin.git.staging.parse_status`.

    Raises:
        SyncError: If the status command fails.
    """
    result = _run_git(
        repo,
        ["status", "--porcelain", "-z", "--untracked-files=all"],
        "read the status",
    )
    return parse_status(result.stdout)


def _write_default_excludes(repo: RepoConfig, git_dir: Path) -> None:
    """
    Refresh the backup's block in ``info/exclude``, see :mod:`admin.git.staging`.

    Args:
        repo: Repository the file belongs to.
        git_dir: The repository's git directory.

    Raises:
        SyncError: If the file cannot be read or written.
    """
    try:
        write_default_excludes(git_dir)
    except (OSError, UnicodeDecodeError) as error:
        raise SyncError(
            f"Failed to update the default excludes for repository "
            f"'{repo.name}': {error}"
        ) from error


def build_commit_message() -> str:
    """
    Build the commit message for a backup commit.

    Returns:
        The message, ending in a UTC timestamp so every commit is
        distinguishable, for example ``workspace backup
        2026-09-22T12:30:00Z``.
    """
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return f"{COMMIT_MESSAGE_PREFIX} {timestamp}"


def _remote_ref(repo: RepoConfig) -> str:
    """
    Return the remote-tracking ref the working tree is synchronized with.

    Args:
        repo: Repository to build the ref for.

    Returns:
        The ref name, for example ``origin/main``.
    """
    return f"origin/{repo.branch}"


def _fetch_refspec(repo: RepoConfig) -> str:
    """
    Return the refspec that fetches the branch into its remote-tracking ref.

    The leading ``+`` matches what ``git clone --single-branch`` writes into
    the repository's config, so a force-push on the remote does not make
    every fetch fail.

    Args:
        repo: Repository to build the refspec for.

    Returns:
        The refspec, for example
        ``+refs/heads/main:refs/remotes/origin/main``.
    """
    return f"+refs/heads/{repo.branch}:refs/remotes/origin/{repo.branch}"


def _push_refspec(repo: RepoConfig) -> str:
    """
    Return the refspec that pushes the local branch to the same remote branch.

    Args:
        repo: Repository to build the refspec for.

    Returns:
        The refspec, for example ``refs/heads/main:refs/heads/main``.
    """
    return f"refs/heads/{repo.branch}:refs/heads/{repo.branch}"


def _count_commits(repo: RepoConfig, revision_range: str, action: str) -> int:
    """
    Count the commits in a revision range.

    Args:
        repo: Repository to count in.
        revision_range: Range to count, for example ``origin/main..HEAD``.
        action: What the count is for, used in error messages.

    Returns:
        The number of commits in the range.

    Raises:
        SyncError: If the count cannot be read.
    """
    result = _run_git(repo, ["rev-list", "--count", revision_range], action)
    return int(result.stdout.strip() or "0")


def _local_side_of_conflicts(repo: RepoConfig) -> tuple[list[str], list[str]]:
    """
    Split the conflicted files by whether the workspace still has them.

    Git keeps the workspace's version of a conflicted file as stage 2.
    Without one, the workspace deleted the file.

    Args:
        repo: Repository in the middle of a conflicted merge.

    Returns:
        The paths to keep, and the paths to delete.

    Raises:
        SyncError: If the list cannot be read.
    """
    result = _run_git(
        repo, ["ls-files", "--unmerged", "-z"], "list the conflicted files"
    )

    stages: dict[str, set[str]] = {}
    for entry in result.stdout.split("\0"):
        if "\t" not in entry:
            continue
        info, path = entry.split("\t", 1)
        stages.setdefault(path, set()).add(info.split()[-1])

    kept = [path for path, found in stages.items() if "2" in found]
    removed = [path for path, found in stages.items() if "2" not in found]
    return kept, removed


def _take_local_side(
    repo: RepoConfig, kept: list[str], removed: list[str]
) -> None:
    """
    Resolve the conflicted files by taking the whole local version.

    Args:
        repo: Repository in the middle of a conflicted merge.
        kept: Paths whose local version is kept.
        removed: Paths the workspace deleted.

    Raises:
        SyncError: If a file cannot be resolved.
    """
    if kept:
        _run_git(
            repo,
            ["checkout", "--ours", "--", *kept],
            "take the local version of the conflicted files",
        )
        _run_git(
            repo, ["add", "--", *kept], "mark the conflicted files resolved"
        )
    if removed:
        _run_git(
            repo,
            ["rm", "--quiet", "--", *removed],
            "keep the local deletion of the conflicted files",
        )


def _git_dir(repo: RepoConfig) -> Path:
    """
    Locate the repository's git directory.

    Asked of git rather than assumed to be ``.git``, because
    :mod:`admin.git.clone` keeps it apart from the working tree.

    Args:
        repo: Repository to inspect.

    Returns:
        The absolute path of the git directory.

    Raises:
        SyncError: If the git directory cannot be located.
    """
    result = _run_git(
        repo, ["rev-parse", "--absolute-git-dir"], "locate the git directory"
    )
    return Path(result.stdout.strip())


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


def _ensure_index_unlocked(repo: RepoConfig, git_dir: Path) -> None:
    """
    Refuse to start while another git process holds the index.

    A lock older than :data:`STALE_LOCK_SECONDS` is removed instead: it was
    left behind by a git process that was killed before it could clean up,
    and as long as it exists every command that writes the index fails, so
    the backup would never run again.

    Args:
        repo: Repository the lock belongs to.
        git_dir: The repository's git directory.

    Raises:
        SyncError: If the lock is recent, or old but cannot be removed.
    """
    lock = git_dir / INDEX_LOCK_FILE
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


def _ensure_on_branch(repo: RepoConfig) -> None:
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
    result = _run_git(
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


def _unmerged_files(repo: RepoConfig) -> list[str]:
    """
    List the files whose conflict git still considers unresolved.

    Args:
        repo: Repository to inspect.

    Returns:
        The unmerged paths, each once, in the order git reports them.

    Raises:
        SyncError: If the list cannot be read.
    """
    result = _run_git(
        repo, ["ls-files", "--unmerged"], "list the unmerged files"
    )

    return list(
        dict.fromkeys(
            line.split("\t", 1)[1]
            for line in result.stdout.splitlines()
            if "\t" in line
        )
    )


def _ensure_safe_to_sync(repo: RepoConfig) -> Path:
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

    Returns:
        The repository's git directory, found on the way.

    Raises:
        SyncError: If another git process holds the index, an operation is
            in progress, the configured branch is not checked out or files
            are unmerged.
    """
    git_dir = _git_dir(repo)
    _ensure_index_unlocked(repo, git_dir)

    marker = _operation_in_progress(git_dir)
    if marker:
        raise SyncError(
            f"Not synchronizing repository '{repo.name}': a git operation is "
            f"in progress ({marker}); finish or abort it first",
            SyncErrorKind.OPERATION_IN_PROGRESS,
        )

    _ensure_on_branch(repo)

    unmerged = _unmerged_files(repo)
    if unmerged:
        raise SyncError(
            f"Not synchronizing repository '{repo.name}': unresolved "
            f"conflicts in {', '.join(unmerged)}",
            SyncErrorKind.UNRESOLVED_CONFLICTS,
        )

    return git_dir


def _staged_conflict_markers(repo: RepoConfig) -> list[str]:
    """
    List the staged files that still hold conflict markers.

    ``git diff --check`` finds the candidates, but it also reports
    whitespace errors and a bare ``=======``, so only its conflict marker
    lines are kept, and only for files that contain a marker line no
    Markdown heading would.

    Args:
        repo: Repository whose changes have just been staged.

    Returns:
        The affected paths, each once, in the order git reports them.

    Raises:
        SyncError: If the check or a staged file cannot be read.
    """
    result = _run_git(
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
    result = _run_git(repo, ["show", f":{path}"], "read a staged file")
    return any(
        line.startswith(CONFLICT_MARKER_PREFIXES)
        for line in result.stdout.splitlines()
    )


def commit_local_changes(repo: RepoConfig) -> bool:
    """
    Commit whatever the user changed in the working tree.

    Committing before merging matters: git refuses to merge over modified
    files, so an uncommitted working tree would make every pull fail as
    soon as the remote had anything to deliver.

    Nothing is committed while the user is in the middle of something -
    see :func:`_ensure_safe_to_sync` - or while a staged file still holds
    conflict markers. The first check runs before anything else, so a
    repository the user is busy with is never pulled into or pushed either.

    Files matching the default excludes are not seen at all, and oversized
    files are left out and named in the log, see :mod:`admin.git.staging`.
    One the user staged by hand is unstaged, as the commit takes everything
    staged.

    Args:
        repo: Repository to commit in.

    Returns:
        True if a commit was made, False if there was nothing to commit.

    Raises:
        SyncError: If the repository is not safe to touch, conflict
            markers are found, or staging or committing fails.
    """
    git_dir = _ensure_safe_to_sync(repo)
    _write_default_excludes(repo, git_dir)

    changed = _changed_files(repo)
    if not changed:
        logger.info("No changes in repository '%s'; nothing to commit", repo.name)
        return False

    paths = [path for _, path in changed]
    oversized = oversized_files(Path(repo.work_tree), paths, MAX_FILE_SIZE_BYTES)
    if oversized:
        logger.warning(
            "Not committing %d file(s) larger than %d bytes in repository "
            "'%s': %s",
            len(oversized),
            MAX_FILE_SIZE_BYTES,
            repo.name,
            ", ".join(oversized),
        )
        staged_by_hand = [
            path for code, path in changed
            if path in oversized and code[0] not in " ?"
        ]
        if staged_by_hand:
            _run_git(
                repo,
                ["reset", "--quiet", "--",
                 *(f":(literal){path}" for path in staged_by_hand)],
                "unstage the oversized files",
            )
        if len(oversized) == len(changed):
            return False

    _run_git(
        repo,
        ["add", "-A", "--", ".", *literal_excludes(oversized)],
        "stage changes",
    )

    marked = _staged_conflict_markers(repo)
    if marked:
        # Unstage again, so the next cycle sees the working tree as the user
        # left it. Without this, deleting the refused file by hand leaves a
        # staged addition behind and that cycle fails with nothing to commit.
        _run_git(repo, ["reset", "--quiet"], "unstage the refused changes", check=False)
        raise SyncError(
            f"Not committing in repository '{repo.name}': conflict markers "
            f"left in {', '.join(marked)}",
            SyncErrorKind.CONFLICT_MARKERS,
        )

    _run_git(
        repo,
        ["commit", "-m", build_commit_message()],
        "commit changes",
        options=_identity_options(),
    )
    logger.info("Committed changes in repository '%s'", repo.name)

    return True


def pull_changes(repo: RepoConfig) -> list[str]:
    """
    Merge the remote's changes in, keeping local files on conflict.

    A conflicted file keeps its whole local version, or stays deleted if
    the workspace deleted it. Files without a conflict get the remote's
    changes.

    Args:
        repo: Repository to pull into.

    Returns:
        The conflicted files. Empty when the merge was clean or there was
        nothing to merge.

    Raises:
        SyncError: If fetching or merging fails for any reason other than
            a conflict, or the configured branch is no longer checked out
            once the fetch is done.
    """
    _run_remote_git(
        repo,
        ["fetch", "--", "origin", _fetch_refspec(repo)],
        "fetch from the remote",
    )

    remote_ref = _remote_ref(repo)
    incoming = _count_commits(repo, f"HEAD..{remote_ref}", "count incoming commits")
    if not incoming:
        logger.info("Repository '%s' is already up to date", repo.name)
        return []

    # The fetch can take minutes, long enough for the user to switch
    # branches, and the merge goes into whatever is checked out.
    _ensure_on_branch(repo)

    attempt = _run_git(
        repo,
        ["merge", remote_ref],
        "merge the remote's changes",
        options=_identity_options(),
        check=False,
    )

    if attempt.returncode == 0:
        logger.info(
            "Merged %d incoming commit(s) into repository '%s' without conflicts",
            incoming,
            repo.name,
        )
        return []

    try:
        kept, removed = _local_side_of_conflicts(repo)
        if not kept and not removed:
            # Stopped without a conflict, e.g. an untracked file in the way.
            reason = attempt.stderr.strip() or attempt.stdout.strip()
            raise SyncError(
                f"Failed to merge the remote's changes for repository "
                f"'{repo.name}': {reason}"
            )

        conflicted = kept + removed
        logger.warning(
            "Merge conflict in repository '%s' affecting %d file(s): %s",
            repo.name,
            len(conflicted),
            ", ".join(conflicted),
        )

        _take_local_side(repo, kept, removed)
        _run_git(
            repo,
            ["commit", "--no-edit"],
            "conclude the merge keeping local files",
            options=_identity_options(),
        )
    except SyncError:
        _run_git(
            repo,
            ["merge", "--abort"],
            "abort the failed merge",
            check=False)
        raise

    logger.info(
        "Resolved the conflict in repository '%s' by keeping the local "
        "version of: %s",
        repo.name,
        ", ".join(conflicted),
    )

    return conflicted


def push_if_ahead(repo: RepoConfig) -> bool:
    """
    Push the local branch when it has commits the remote does not.

    The branch is counted by name rather than through ``HEAD``, so what is
    counted is exactly what the push sends, whatever is checked out.

    Args:
        repo: Repository to push.

    Returns:
        True if a push was made, False if the remote was already level.

    Raises:
        SyncError: If the push fails.
    """
    outgoing = _count_commits(
        repo,
        f"{_remote_ref(repo)}..refs/heads/{repo.branch}",
        "count outgoing commits",
    )
    if not outgoing:
        return False

    _run_remote_git(
        repo,
        ["push", "--", "origin", _push_refspec(repo)],
        "push changes",
    )
    logger.info(
        "Pushed %d commit(s) from repository '%s' to branch %s",
        outgoing,
        repo.name,
        repo.branch,
    )

    return True


def _stop_if_requested(
    repo: RepoConfig, stop_event: threading.Event | None, next_step: str
) -> None:
    """
    End the cycle here when the service is stopping.

    Args:
        repo: Repository being synchronized.
        stop_event: Set when the service is stopping; None never stops.
        next_step: The step that would have run next, for the message.

    Raises:
        SyncStopped: If ``stop_event`` is set.
    """
    if stop_event is not None and stop_event.is_set():
        raise SyncStopped(
            f"Stopped synchronizing repository '{repo.name}' before it could "
            f"{next_step}; the service is stopping"
        )


def sync_once(repo: RepoConfig, stop_event: threading.Event | None = None) -> bool:
    """
    Run one full synchronization of the repository.

    The order is deliberate: commit the user's work first so the working
    tree is clean, then merge the remote in, then push the result. A
    conflict is resolved in favour of the workspace's own files.

    ``stop_event`` is checked between the steps, so a stopping service
    waits for at most the git command already running. A commit left
    unpushed that way is pushed by the first cycle after the restart.

    Assumes the repository has already been cloned by
    :func:`admin.git.clone.clone_asset`.

    Args:
        repo: Repository to synchronize.
        stop_event: Set when the service is stopping. None runs the whole
            cycle.

    Returns:
        True if anything changed - a commit, a merge or a push - and
        False when the repository was already in sync.

    Raises:
        SyncError: If any git step fails.
        SyncStopped: If ``stop_event`` was set before the cycle finished.
    """
    committed = commit_local_changes(repo)
    _stop_if_requested(repo, stop_event, "pull")
    merged = bool(pull_changes(repo))
    _stop_if_requested(repo, stop_event, "push")
    pushed = push_if_ahead(repo)

    return committed or merged or pushed
