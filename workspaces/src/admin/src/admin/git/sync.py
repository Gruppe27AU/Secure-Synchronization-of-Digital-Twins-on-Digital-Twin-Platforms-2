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
"""

import logging
import subprocess
import time
from pathlib import Path

from admin.git.auth import AUTH_FAILURE_HINT, is_auth_failure, remote_options
from admin.git.config import RepoConfig

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


class SyncError(Exception):
    """Raised when committing or pushing a repository fails."""


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


def _run_git(
    repo: RepoConfig,
    args: list[str],
    action: str,
    options: list[str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """
    Run one git command inside the repository's working tree.

    Args:
        repo: Repository to run the command for.
        args: The git subcommand and its arguments, for example
            ``["push", "origin", "main"]``.
        action: What the command is doing, used in error messages.
        options: Git options placed before the subcommand, such as the
            credentials header.
        check: Raise when git fails. Pass False for a command whose
            failure is an expected outcome rather than an error, such as
            the merge that is used to detect conflicts.

    Returns:
        The completed process. When ``check`` is False, this may describe
        a failed command.

    Raises:
        SyncError: If git exits with a non-zero status and ``check`` is
            True.
    """
    command = ["git", "-C", str(repo.work_tree), *(options or []), *args]
    result = subprocess.run(command, capture_output=True, text=True, check=False)

    if check and result.returncode != 0:
        reason = result.stderr.strip() or result.stdout.strip()
        logger.error(
            "Failed to %s for repository '%s': %s", action, repo.name, reason
        )
        message = f"Failed to {action} for repository '{repo.name}': {reason}"
        if repo.username and repo.token and is_auth_failure(reason):
            message += f" ({AUTH_FAILURE_HINT})"
        raise SyncError(message)

    return result


def has_changes(repo: RepoConfig) -> bool:
    """
    Check whether the working tree holds anything worth committing.

    Args:
        repo: Repository to inspect.

    Returns:
        True if there are modified, deleted or untracked files.

    Raises:
        SyncError: If the status command fails.
    """
    result = _run_git(repo, ["status", "--porcelain"], "read the status")
    return bool(result.stdout.strip())


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


def _conflicted_files(repo: RepoConfig) -> list[str]:
    """
    List the files a failed merge left unresolved.

    Args:
        repo: Repository in the middle of a conflicted merge.

    Returns:
        The conflicted paths, in the order git reports them.

    Raises:
        SyncError: If the list cannot be read.
    """
    result = _run_git(
        repo,
        ["diff", "--name-only", "--diff-filter=U"],
        "list the conflicted files",
    )
    return result.stdout.split()


def _operation_in_progress(repo: RepoConfig) -> str | None:
    """
    Name the git operation the repository is stopped in the middle of.

    The git directory is asked of git rather than assumed to be
    ``.git``, because :mod:`admin.git.clone` keeps it apart from the
    working tree.

    Args:
        repo: Repository to inspect.

    Returns:
        The first entry of ``IN_PROGRESS_MARKERS`` that exists, or None
        when no operation is in progress.

    Raises:
        SyncError: If the git directory cannot be located.
    """
    result = _run_git(
        repo, ["rev-parse", "--absolute-git-dir"], "locate the git directory"
    )
    git_dir = Path(result.stdout.strip())

    for marker in IN_PROGRESS_MARKERS:
        if (git_dir / marker).exists():
            return marker
    return None


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


def _ensure_nothing_in_progress(repo: RepoConfig) -> None:
    """
    Refuse to touch a repository that is in the middle of a conflict.

    This guards against every way of getting there, not just a failed
    merge of our own: the user may have started a merge or rebase by
    hand and not finished it yet. Committing would conclude it, conflict
    and all, and the push would hand it on to the remote.

    Args:
        repo: Repository to inspect.

    Raises:
        SyncError: If an operation is in progress or files are unmerged.
    """
    marker = _operation_in_progress(repo)
    if marker:
        raise SyncError(
            f"Not synchronizing repository '{repo.name}': a git operation is "
            f"in progress ({marker}); finish or abort it first"
        )

    unmerged = _unmerged_files(repo)
    if unmerged:
        raise SyncError(
            f"Not synchronizing repository '{repo.name}': unresolved "
            f"conflicts in {', '.join(unmerged)}"
        )


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

    Nothing is committed while the repository is in the middle of a
    conflict, or while a staged file still holds conflict markers. The
    first check runs before anything else, so a merge or rebase the user
    has not finished is never pulled into or pushed either.

    Args:
        repo: Repository to commit in.

    Returns:
        True if a commit was made, False if the working tree was clean.

    Raises:
        SyncError: If an unfinished conflict or conflict markers are found,
            or if staging or committing fails.
    """
    _ensure_nothing_in_progress(repo)

    if not has_changes(repo):
        logger.info("No changes in repository '%s'; nothing to commit", repo.name)
        return False

    _run_git(repo, ["add", "-A"], "stage changes")

    marked = _staged_conflict_markers(repo)
    if marked:
        # Unstage again, so the next cycle sees the working tree as the user
        # left it. Without this, deleting the refused file by hand leaves a
        # staged addition behind and that cycle fails with nothing to commit.
        _run_git(repo, ["reset", "--quiet"], "unstage the refused changes", check=False)
        raise SyncError(
            f"Not committing in repository '{repo.name}': conflict markers "
            f"left in {', '.join(marked)}"
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

    The merge is attempted twice on purpose. The first attempt is an
    ordinary merge, whose only job is to *detect* conflicts: git names
    every file it could not resolve, which a merge using the ``ours``
    strategy would silently swallow. That attempt is then thrown away and
    the merge redone preferring the local side, so the conflicted files
    keep the workspace's version while the rest of the remote's work is
    still merged in.

    Args:
        repo: Repository to pull into.

    Returns:
        The files whose local version was kept. Empty when the merge was
        clean or there was nothing to merge.

    Raises:
        SyncError: If fetching or merging fails for any reason other than
            a conflict.
    """
    _run_git(
        repo,
        ["fetch", "--", "origin", _fetch_refspec(repo)],
        "fetch from the remote",
        options=remote_options(repo),
    )

    remote_ref = _remote_ref(repo)
    incoming = _count_commits(repo, f"HEAD..{remote_ref}", "count incoming commits")
    if not incoming:
        logger.info("Repository '%s' is already up to date", repo.name)
        return []

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

    conflicted = _conflicted_files(repo)
    logger.warning(
        "Merge conflict in repository '%s' affecting %d file(s): %s",
        repo.name,
        len(conflicted),
        ", ".join(conflicted),
    )

    _run_git(repo, ["merge", "--abort"], "abort the conflicted merge")
    try:
        _run_git(
            repo,
            ["merge", "-X", "ours", remote_ref],
            "merge the remote's changes keeping local files",   
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

    Args:
        repo: Repository to push.

    Returns:
        True if a push was made, False if the remote was already level.

    Raises:
        SyncError: If the push fails.
    """
    outgoing = _count_commits(
        repo, f"{_remote_ref(repo)}..HEAD", "count outgoing commits"
    )
    if not outgoing:
        return False

    _run_git(
        repo,
        ["push", "--", "origin", _push_refspec(repo)],
        "push changes",
        options=remote_options(repo),
    )
    logger.info(
        "Pushed %d commit(s) from repository '%s' to branch %s",
        outgoing,
        repo.name,
        repo.branch,
    )

    return True


def sync_once(repo: RepoConfig) -> bool:
    """
    Run one full synchronization of the repository.

    The order is deliberate: commit the user's work first so the working
    tree is clean, then merge the remote in, then push the result. A
    conflict is resolved in favour of the workspace's own files.

    Assumes the repository has already been cloned by
    :func:`admin.git.clone.clone_asset`.

    Args:
        repo: Repository to synchronize.

    Returns:
        True if anything changed - a commit, a merge or a push - and
        False when the repository was already in sync.

    Raises:
        SyncError: If any git step fails.
    """
    committed = commit_local_changes(repo)
    merged = bool(pull_changes(repo))
    pushed = push_if_ahead(repo)

    return committed or merged or pushed
