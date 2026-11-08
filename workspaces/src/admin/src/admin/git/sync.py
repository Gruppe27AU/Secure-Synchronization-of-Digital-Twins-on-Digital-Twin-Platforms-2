"""
Committing and pushing workspace changes back to the remote.

Consumes :class:`admin.git.config.RepoConfig` and keeps one already-cloned
repository in step with its remote. How depends on the repository's mode:

- ``sync`` commits what the user changed, merges in what the remote
  changed, and pushes the result. Local work always wins: when both sides
  changed the same file, the merge keeps the workspace's version, because
  the user is sitting in front of that file and did not ask for it to be
  replaced. Remote changes to *other* files are still merged in as normal.
- ``pull`` only fast-forwards to the remote. Nothing is committed or
  pushed, so a repository shared between users can never have one user's
  files overwrite another's.

Two settings that :mod:`admin.git.clone` intentionally leaves out of the
repository's own config are supplied per command instead, so neither is
ever written to disk in the workspace:

- the commit identity, without which ``git commit`` refuses to run,
- the ``Authorization`` header, without which ``git push`` is anonymous.

Every command that names the branch puts ``--`` before its positional
arguments and spells the branch out as a full refspec, so a branch value can
never be read as a git option such as ``--upload-pack``.

What is checked before anything is committed lives in
:mod:`admin.git.checks`; how each command is run, in
:mod:`admin.git.command`.
"""

import logging
import secrets
import threading
import time

from admin.git.checks import (
    MAX_FILE_BYTES,
    ensure_default_excludes,
    ensure_no_mass_deletion,
    ensure_on_branch,
    ensure_safe_to_sync,
    large_files,
    staged_conflict_markers,
    unmerged_stages,
)
from admin.git.command import count_commits, ref_exists, run_local, run_remote
from admin.git.config import RepoConfig, SyncMode
from admin.git.errors import SyncError, SyncErrorKind

__all__ = [
    "HeldBack",
    "SyncError",
    "SyncErrorKind",
    "SyncStopped",
    "build_commit_message",
    "commit_local_changes",
    "has_changes",
    "pull_changes",
    "pull_only",
    "push_if_ahead",
    "sync_once",
]

logger = logging.getLogger(__name__)

#: Author and committer name of every commit the backup makes.
COMMIT_AUTHOR_NAME = "workspace-admin"

#: Author and committer email of every commit the backup makes.
COMMIT_AUTHOR_EMAIL = "workspace-admin@localhost"

#: Start of every backup commit's message, followed by a UTC timestamp.
COMMIT_MESSAGE_PREFIX = "workspace backup"

#: Branch below which a workspace pushes its work when the configured branch
#: cannot be pulled, followed by the workspace's own id.
BACKUP_BRANCH_PREFIX = "backup/"

#: File in the git directory holding the workspace's id, which names its
#: backup branch.
WORKSPACE_ID_FILE = "workspace-admin-id"

#: Text of git's error when the branch asked for does not exist on the remote.
MISSING_REMOTE_REF = "couldn't find remote ref"

#: Texts of git's errors when a fast-forward would overwrite local changes
#: or untracked files.
LOCAL_CHANGES_MARKERS = ("would be overwritten", "local changes")


class SyncStopped(Exception):
    """
    Raised when a cycle is given up between two steps because the service
    is stopping.

    Not a :class:`SyncError`: nothing went wrong, and every step leaves the
    repository consistent, so the next start carries on from there.
    """


class HeldBack(SyncError):
    """
    Raised when some files were left out of a commit and the rest went in.

    :func:`sync_once` still pulls and pushes what was committed, and
    raises this only at the end, so its kind is what the status reports.

    Attributes:
        committed: Whether the files that were not held back were
            committed.
    """

    def __init__(self, message: str, kind: SyncErrorKind, committed: bool):
        super().__init__(message, kind)
        self.committed = committed


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
    result = run_local(repo, ["status", "--porcelain"], "read the status")
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


def _local_branch(repo: RepoConfig) -> str:
    """Return the full name of the configured branch, ``refs/heads/<branch>``."""
    return f"refs/heads/{repo.branch}"


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
    return f"{_local_branch(repo)}:{_local_branch(repo)}"


def _stage(repo: RepoConfig, held_back: list[str]) -> None:
    """
    Stage every change in the working tree except the held-back files.

    Args:
        repo: Repository to stage in.
        held_back: Paths to leave out, relative to the working tree.

    Raises:
        SyncError: If staging fails.
    """
    args = ["add", "-A"]
    if held_back:
        args += ["--", ":/", *(f":(exclude,top,literal){path}" for path in held_back)]
    run_local(repo, args, "stage changes")


def _has_staged_changes(repo: RepoConfig) -> bool:
    """
    Check whether anything is staged for the next commit.

    Args:
        repo: Repository to inspect.

    Returns:
        True if the staged tree differs from ``HEAD``.

    Raises:
        SyncError: If the check fails.
    """
    result = run_local(
        repo,
        ["diff", "--cached", "--quiet"],
        "check for staged changes",
        check=False,
    )
    if result.returncode not in (0, 1):
        raise SyncError(
            f"Failed to check for staged changes in repository '{repo.name}': "
            f"{result.stderr.strip()}"
        )
    return result.returncode == 1


def _held_back_error(
    repo: RepoConfig, marked: list[str], too_large: list[str], committed: bool
) -> HeldBack:
    """
    Describe the files left out of a commit.

    Args:
        repo: Repository committed in.
        marked: Files left out for their conflict markers.
        too_large: Files left out for their size.
        committed: Whether the rest was committed.

    Returns:
        The error to raise once the cycle is done, with the kind for the
        conflict markers when there are both, since those need the user.
    """
    parts = []
    if marked:
        parts.append(f"conflict markers left in {', '.join(marked)}")
    if too_large:
        parts.append(
            f"larger than {MAX_FILE_BYTES // 2**20} MiB: {', '.join(too_large)}"
        )
    kind = SyncErrorKind.CONFLICT_MARKERS if marked else SyncErrorKind.FILE_TOO_LARGE
    return HeldBack(
        f"Not committing some files in repository '{repo.name}': "
        + "; ".join(parts),
        kind,
        committed,
    )


def commit_local_changes(repo: RepoConfig) -> bool:
    """
    Commit whatever the user changed in the working tree.

    Committing before merging matters: git refuses to merge over modified
    files, so an uncommitted working tree would make every pull fail as
    soon as the remote had anything to deliver.

    Nothing is committed while the user is in the middle of something -
    see :func:`admin.git.checks.ensure_safe_to_sync` - or while most of the
    tracked files are missing. The first check runs before anything else,
    so a repository the user is busy with is never pulled into or pushed
    either. A file that is too large or still holds conflict markers is
    left out, and everything else is committed.

    Args:
        repo: Repository to commit in.

    Returns:
        True if a commit was made, False if the working tree was clean.

    Raises:
        HeldBack: If files were left out; the rest has been committed.
        SyncError: If the repository is not safe to touch, most tracked
            files are missing, or staging or committing fails.
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
    if marked:
        # Unstage them again, so the next cycle sees them as the user left
        # them. Without this, deleting a refused file by hand leaves a staged
        # addition behind.
        run_local(
            repo, ["reset", "--quiet", "--", *marked], "unstage the refused files"
        )

    committed = False
    if _has_staged_changes(repo):
        run_local(
            repo,
            ["commit", "-m", build_commit_message()],
            "commit changes",
            options=_identity_options(),
        )
        committed = True
        logger.info("Committed changes in repository '%s'", repo.name)

    if marked or too_large:
        error = _held_back_error(repo, marked, too_large, committed)
        logger.warning("%s", error)
        raise error

    return committed


def _fetch(repo: RepoConfig) -> bool:
    """
    Fetch the configured branch into its remote-tracking ref.

    A branch the remote does not have yet is not an error if this
    workspace has never seen it either: the remote is empty, or the branch
    is new, and the next push creates it. A branch that has disappeared
    since it was last fetched - renamed or deleted on the forge - is.

    Args:
        repo: Repository to fetch for.

    Returns:
        True if the branch was fetched, False if the remote does not have
        it yet.

    Raises:
        SyncError: If the fetch fails for any other reason.
    """
    try:
        run_remote(
            repo,
            ["fetch", "--", "origin", _fetch_refspec(repo)],
            "fetch from the remote",
        )
    except SyncError as error:
        if MISSING_REMOTE_REF in error.git_output.lower() and not ref_exists(
            repo, f"refs/remotes/{_remote_ref(repo)}"
        ):
            logger.info(
                "The remote of repository '%s' has no branch %s yet; the next "
                "push creates it",
                repo.name,
                repo.branch,
            )
            return False
        raise
    return True


def _incoming(repo: RepoConfig) -> int:
    """
    Count the fetched commits the configured branch does not have yet.

    Args:
        repo: Repository just fetched.

    Returns:
        The number of commits to merge.

    Raises:
        SyncError: If they cannot be counted.
    """
    # An unborn branch, in a repository started on an empty remote, has
    # no HEAD to count from: everything fetched is incoming.
    if not ref_exists(repo, _local_branch(repo)):
        return count_commits(repo, _remote_ref(repo), "count incoming commits")
    return count_commits(repo, f"HEAD..{_remote_ref(repo)}", "count incoming commits")


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
    stages = unmerged_stages(repo)
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
        run_local(
            repo,
            ["checkout", "--ours", "--", *kept],
            "take the local version of the conflicted files",
        )
        run_local(
            repo, ["add", "--", *kept], "mark the conflicted files resolved"
        )
    if removed:
        run_local(
            repo,
            ["rm", "--quiet", "--", *removed],
            "keep the local deletion of the conflicted files",
        )


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
    if not _fetch(repo):
        return []

    incoming = _incoming(repo)
    if not incoming:
        logger.info("Repository '%s' is already up to date", repo.name)
        return []

    # The fetch can take minutes, long enough for the user to switch
    # branches, and the merge goes into whatever is checked out.
    ensure_on_branch(repo)

    attempt = run_local(
        repo,
        ["merge", _remote_ref(repo)],
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
        run_local(
            repo,
            ["commit", "--no-edit"],
            "conclude the merge keeping local files",
            options=_identity_options(),
        )
    except SyncError:
        run_local(
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
    counted is exactly what the push sends, whatever is checked out. When
    the remote does not have the branch yet, every commit on it counts,
    and the push creates it there.

    Args:
        repo: Repository to push.

    Returns:
        True if a push was made, False if the remote was already level or
        there is nothing on the branch to push.

    Raises:
        SyncError: If the push fails.
    """
    local = _local_branch(repo)
    if not ref_exists(repo, local):
        return False

    revision_range = local
    if ref_exists(repo, f"refs/remotes/{_remote_ref(repo)}"):
        revision_range = f"{_remote_ref(repo)}..{local}"

    outgoing = count_commits(repo, revision_range, "count outgoing commits")
    if not outgoing:
        return False

    run_remote(
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


def workspace_id(repo: RepoConfig) -> str:
    """
    Return the id that names this workspace's backup branch.

    Made up once and kept in the git directory, so it stays the same across
    restarts and differs between workspaces backing up to one remote.

    Args:
        repo: Repository the id belongs to.

    Returns:
        Twelve hexadecimal characters.

    Raises:
        OSError: If a new id cannot be stored.
    """
    id_file = repo.git_dir / WORKSPACE_ID_FILE
    try:
        stored = id_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        stored = ""
    if len(stored) == 12 and all(char in "0123456789abcdef" for char in stored):
        return stored

    new_id = secrets.token_hex(6)
    id_file.write_text(f"{new_id}\n", encoding="utf-8")
    return new_id


def backup_branch(repo: RepoConfig) -> str:
    """
    Return the remote branch this workspace pushes to when it cannot pull.

    Args:
        repo: Repository to name the branch for.

    Returns:
        The branch name, for example ``backup/3f2a9c01d4e5``.
    """
    return f"{BACKUP_BRANCH_PREFIX}{workspace_id(repo)}"


def push_to_backup_branch(repo: RepoConfig) -> str | None:
    """
    Push the local branch to this workspace's own backup branch.

    Only this workspace writes that branch, so the push is forced and
    cannot be refused for being behind, whatever happened to the
    configured branch on the remote.

    Args:
        repo: Repository to push.

    Returns:
        The backup branch pushed to, or None when there was nothing to push
        or the push failed, which is logged.
    """
    if not ref_exists(repo, _local_branch(repo)):
        return None
    try:
        branch = backup_branch(repo)
        run_remote(
            repo,
            ["push", "--", "origin", f"+{_local_branch(repo)}:refs/heads/{branch}"],
            "push to the backup branch",
        )
    except (SyncError, OSError) as error:
        logger.error(
            "Could not push repository '%s' to its backup branch either: %s",
            repo.name,
            error,
        )
        return None

    logger.warning(
        "Pushed repository '%s' to branch %s instead of %s, which could not be "
        "pulled",
        repo.name,
        branch,
        repo.branch,
    )
    return branch


def _pull_or_back_up(repo: RepoConfig) -> list[str]:
    """
    Pull, and when that fails for good, push to the backup branch instead.

    A failure that will not go away by itself - the remote's history
    rewritten, the branch renamed or deleted - would otherwise stop every
    push, and the user's work would pile up unpushed. A rejected token, an
    unreachable remote, a time-out or a branch switch is passed on as it
    is: pushing would fail too, or the next cycle simply tries again.

    Args:
        repo: Repository to pull into.

    Returns:
        The conflicted files, as :func:`pull_changes` returns them.

    Raises:
        SyncError: With :attr:`SyncErrorKind.PULL_FAILED` if the pull
            failed for good, or as raised by :func:`pull_changes`.
    """
    try:
        return pull_changes(repo)
    except SyncError as error:
        if error.kind is not SyncErrorKind.GIT_FAILED:
            raise
        branch = push_to_backup_branch(repo)
        saved = (
            f"; the local work was pushed to branch {branch}"
            if branch
            else "; the local work is not pushed anywhere"
        )
        raise SyncError(f"{error}{saved}", SyncErrorKind.PULL_FAILED) from error


def _renamed_on_conflict(repo: RepoConfig, conflicted: list[str]) -> list[str]:
    """
    Pick out the files git renamed to settle a file/directory conflict.

    When one side has a file where the other has a directory, git moves the
    file aside as ``<name>~<side>`` and the user's file changes name.

    Args:
        repo: Repository just merged into.
        conflicted: The conflicted files.

    Returns:
        The renamed paths. A path that already had that name before the
        merge, such as a file the user named ``notes~HEAD`` themselves, is
        not one of them.

    Raises:
        SyncError: If the files before the merge cannot be listed.
    """
    suffixes = ("~HEAD", "~" + _remote_ref(repo).replace("/", "_"))
    candidates = [path for path in conflicted if path.endswith(suffixes)]
    if not candidates:
        return []

    # HEAD is the merge commit; its first parent is the workspace before it.
    result = run_local(
        repo,
        ["ls-tree", "-z", "--name-only", "HEAD^1", "--", *candidates],
        "list the files from before the merge",
    )
    existed = set(result.stdout.split("\0"))
    return [path for path in candidates if path not in existed]


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


def pull_only(repo: RepoConfig) -> bool:
    """
    Fast-forward a ``pull`` mode repository to its remote.

    Nothing is committed or pushed. Local changes stay as they are; when
    one is in the way of the remote's, nothing is changed and the status
    says so.

    Args:
        repo: Repository to update.

    Returns:
        True if the working tree moved forward, False if it was up to date.

    Raises:
        SyncError: With :attr:`SyncErrorKind.LOCAL_CHANGES` if a local
            change is in the way, :attr:`SyncErrorKind.PULL_FAILED` if the
            workspace has commits of its own, or as for any other step.
    """
    ensure_safe_to_sync(repo)
    if not _fetch(repo):
        return False

    incoming = _incoming(repo)
    if not incoming:
        logger.info("Repository '%s' is already up to date", repo.name)
        return False

    ensure_on_branch(repo)
    attempt = run_local(
        repo,
        ["merge", "--ff-only", _remote_ref(repo)],
        "fast-forward to the remote",
        check=False,
    )
    if attempt.returncode != 0:
        reason = attempt.stderr.strip() or attempt.stdout.strip()
        kind = (
            SyncErrorKind.LOCAL_CHANGES
            if any(marker in reason.lower() for marker in LOCAL_CHANGES_MARKERS)
            else SyncErrorKind.PULL_FAILED
        )
        raise SyncError(
            f"Not updating pull-only repository '{repo.name}': {reason}", kind
        )

    logger.info(
        "Updated pull-only repository '%s' with %d incoming commit(s)",
        repo.name,
        incoming,
    )
    return True


def sync_once(repo: RepoConfig, stop_event: threading.Event | None = None) -> bool:
    """
    Run one full synchronization of the repository.

    For a ``sync`` repository the order is deliberate: commit the user's
    work first so the working tree is clean, then merge the remote in, then
    push the result. A conflict is resolved in favour of the workspace's
    own files. A ``pull`` repository is only fast-forwarded
    (:func:`pull_only`).

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
        SyncError: If any git step fails, or, once the cycle is done, if
            files were held back or renamed on the way.
        SyncStopped: If ``stop_event`` was set before the cycle finished.
    """
    if repo.mode is SyncMode.PULL:
        return pull_only(repo)

    notices: list[SyncError] = []
    try:
        committed = commit_local_changes(repo)
    except HeldBack as held_back:
        committed = held_back.committed
        notices.append(held_back)

    _stop_if_requested(repo, stop_event, "pull")
    conflicted = _pull_or_back_up(repo)
    _stop_if_requested(repo, stop_event, "push")
    pushed = push_if_ahead(repo)

    renamed = _renamed_on_conflict(repo, conflicted)
    if renamed:
        notices.append(SyncError(
            f"Repository '{repo.name}': git renamed {', '.join(renamed)} to "
            "settle a conflict between a file and a directory; rename it back "
            "or move it",
            SyncErrorKind.RENAMED_ON_CONFLICT,
        ))
    if notices:
        raise notices[0]

    return committed or bool(conflicted) or pushed
