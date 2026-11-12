"""
One backup cycle for one cloned repository.

- ``sync``: commit, push to the backup branch, merge the remote, push. A
  conflict aborts the merge and ends the cycle; the work is on the backup
  branch meanwhile.
- ``pull``: fast-forward only (:func:`admin.git.pull.pull_only`).

The commit identity and the ``Authorization`` header are passed per
command, never written into the repository.
"""

import logging
import threading

from admin.git.checks import MAX_FILE_BYTES
from admin.git.commit import HeldBack, commit_local_changes
from admin.git.config import RepoConfig, SyncMode
from admin.git.errors import SyncError, SyncErrorKind
from admin.git.pull import pull_changes, pull_only
from admin.git.push import push_if_ahead, push_to_backup_branch

logger = logging.getLogger(__name__)


class SyncStopped(Exception):
    """
    Raised when a cycle ends between two steps because the service stops.

    Not a :class:`SyncError`: every step leaves the repository consistent,
    and the next start carries on.
    """


def _stop_if_requested(
    repo: RepoConfig, stop_event: threading.Event | None, next_step: str
) -> None:
    """
    Raise :class:`SyncStopped` if ``stop_event`` is set.

    Args:
        next_step: The step that would run next, for the message.
    """
    if stop_event is not None and stop_event.is_set():
        raise SyncStopped(
            f"Stopped synchronizing repository '{repo.name}' before it could "
            f"{next_step}; the service is stopping"
        )


def _pull(repo: RepoConfig, backup: str | None) -> bool:
    """
    Pull; on a conflict or lasting failure, say where the work is.

    Args:
        backup: Backup branch pushed to this cycle, or None.

    Raises:
        SyncError: As :func:`admin.git.pull.pull_changes`, with
            ``git_failed`` turned into ``pull_failed``.
    """
    try:
        return pull_changes(repo)
    except SyncError as error:
        if error.kind not in (SyncErrorKind.GIT_FAILED, SyncErrorKind.MERGE_CONFLICT):
            raise
        saved = (
            f"; the local work is on branch {backup}"
            if backup
            else "; the local work is not pushed anywhere"
        )
        kind = (
            SyncErrorKind.MERGE_CONFLICT
            if error.kind is SyncErrorKind.MERGE_CONFLICT
            else SyncErrorKind.PULL_FAILED
        )
        raise SyncError(f"{error}{saved}", kind) from error


def _blocked_message(repo: RepoConfig, marked: list[str], too_large: list[str]) -> str:
    """Name each held-back file in the way of the merge, and the fix."""
    return " ".join(
        [
            f"Repository '{repo.name}': {path} is held back (conflict markers "
            f"left in {path}), remove the conflict markers in order to merge."
            for path in marked
        ]
        + [
            f"Repository '{repo.name}': {path} is held back (larger than "
            f"{MAX_FILE_BYTES // 2**20} MiB), remove or shrink it in order to merge."
            for path in too_large
        ]
    )


def _held_back_in_the_way(
    repo: RepoConfig, error: SyncError, notices: list[SyncError]
) -> SyncError | None:
    """
    Name the held-back files that stopped the merge.

    Args:
        error: Why the pull failed.
        notices: Notices so far; a :class:`HeldBack` lists the files.

    Returns:
        The error to raise instead, or None if no held-back file was in the way.
    """
    held = next((n for n in notices if isinstance(n, HeldBack)), None)
    if held is None or error.kind is not SyncErrorKind.LOCAL_CHANGES:
        return None
    # git lists the files in the way on lines of their own, indented by a tab.
    listed = {line.strip() for line in str(error).splitlines() if line.startswith("\t")}
    marked = [path for path in held.marked if path in listed]
    too_large = [path for path in held.too_large if path in listed]
    if not marked and not too_large:
        return None
    blocked = SyncError(
        _blocked_message(repo, marked, too_large),
        SyncErrorKind.CONFLICT_MARKERS if marked else SyncErrorKind.FILE_TOO_LARGE,
    )
    logger.warning("%s", blocked)
    return blocked


def sync_once(repo: RepoConfig, stop_event: threading.Event | None = None) -> bool:
    """
    Run one cycle. Assumes the repository is cloned.

    ``stop_event`` is checked between steps; a commit left unpushed is
    pushed by the next cycle.

    Returns:
        True if a commit, merge or push to the configured branch was made.

    Raises:
        SyncError: If a step fails, or at the end if files were held back.
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

    _stop_if_requested(repo, stop_event, "back up")
    backup = push_to_backup_branch(repo)
    _stop_if_requested(repo, stop_event, "pull")
    try:
        merged = _pull(repo, backup)
    except SyncError as error:
        blocked = _held_back_in_the_way(repo, error, notices)
        if blocked:
            raise blocked from error
        raise
    _stop_if_requested(repo, stop_event, "push")
    pushed = push_if_ahead(repo)

    if notices:
        raise notices[0]
    return committed or merged or pushed
