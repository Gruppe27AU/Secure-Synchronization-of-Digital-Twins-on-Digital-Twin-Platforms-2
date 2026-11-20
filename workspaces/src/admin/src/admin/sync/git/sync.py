"""
One backup cycle for one cloned repository.

- ``sync``: commit, merge the remote, push. A conflict aborts the merge,
  and the workspace's branch replaces the remote's
  (:func:`admin.sync.git.push.overwrite_remote`); the remote's version is
  kept on a ``remote-backup/`` branch.
- ``pull``: fast-forward only (:func:`admin.sync.git.pull.pull_only`).

The commit identity and the ``Authorization`` header are passed per
command, never written into the repository.
"""

import logging
import threading
from collections.abc import Callable

from admin.config.config import RepoConfig, SyncMode
from admin.sync.git.checks import MAX_FILE_BYTES
from admin.sync.git.commit import HeldBack, commit_local_changes
from admin.sync.git.errors import SyncError, SyncErrorKind
from admin.sync.git.pull import pull_changes, pull_only, remote_changed_paths
from admin.sync.git.push import overwrite_remote, push_if_ahead

logger = logging.getLogger(__name__)

#: Called after the workspace's branch replaced the remote's.
OnOverwrite = Callable[[], None]


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


def _pull(repo: RepoConfig) -> bool:
    """
    Pull, reporting a lasting failure as ``pull_failed``.

    Raises:
        SyncError: As :func:`admin.sync.git.pull.pull_changes`, with
            ``git_failed`` turned into ``pull_failed``.
    """
    try:
        return pull_changes(repo)
    except SyncError as error:
        if error.kind is not SyncErrorKind.GIT_FAILED:
            raise
        raise SyncError(str(error), SyncErrorKind.PULL_FAILED) from error


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
    repo: RepoConfig, error: SyncError, held: HeldBack | None
) -> SyncError | None:
    """
    Name the held-back files that stopped the merge: those the remote changed.

    Args:
        error: Why the pull failed.
        held: The files held back from this cycle's commit, if any.

    Returns:
        The error to raise instead, or None if no held-back file was in the way
        or the remote's files cannot be listed.
    """
    if held is None or error.kind is not SyncErrorKind.LOCAL_CHANGES:
        return None
    try:
        changed = remote_changed_paths(repo)
    except SyncError:
        return None
    marked = [path for path in held.marked if path in changed]
    too_large = [path for path in held.too_large if path in changed]
    if not marked and not too_large:
        return None
    blocked = SyncError(
        _blocked_message(repo, marked, too_large),
        SyncErrorKind.CONFLICT_MARKERS if marked else SyncErrorKind.FILE_TOO_LARGE,
    )
    logger.warning("%s", blocked)
    return blocked


def _merge(repo: RepoConfig, held: HeldBack | None) -> bool:
    """Pull; a held-back file in the way is named instead of git's error."""
    try:
        return _pull(repo)
    except SyncError as error:
        blocked = _held_back_in_the_way(repo, error, held)
        if blocked:
            raise blocked from error
        raise


def _overwrite(
    repo: RepoConfig, conflict: SyncError, on_overwrite: OnOverwrite | None
) -> None:
    """
    Replace the remote's branch after ``conflict``; a failure names both.

    Raises:
        SyncError: As :func:`admin.sync.git.push.overwrite_remote`.
    """
    try:
        overwrite_remote(repo)
    except SyncError as error:
        raise SyncError(f"{conflict}; {error}", error.kind) from error
    if on_overwrite is not None:
        on_overwrite()


def _merge_or_overwrite(
    repo: RepoConfig,
    stop_event: threading.Event | None,
    held: HeldBack | None,
    on_overwrite: OnOverwrite | None,
) -> bool:
    """
    Merge the remote, then push; on a conflict, replace the remote's branch.

    Returns:
        True if anything was merged or pushed.
    """
    try:
        merged = _merge(repo, held)
    except SyncError as error:
        if error.kind is not SyncErrorKind.MERGE_CONFLICT:
            raise
        _stop_if_requested(repo, stop_event, "replace the remote's branch")
        _overwrite(repo, error, on_overwrite)
        return True
    _stop_if_requested(repo, stop_event, "push")
    return push_if_ahead(repo) or merged


def sync_once(
    repo: RepoConfig,
    stop_event: threading.Event | None = None,
    on_overwrite: OnOverwrite | None = None,
) -> bool:
    """
    Run one cycle. Assumes the repository is cloned.

    ``stop_event`` is checked between steps; a commit left unpushed is
    pushed by the next cycle. ``on_overwrite`` is called when a conflict
    made the workspace's branch replace the remote's.

    Returns:
        True if a commit, merge or push to the configured branch was made.

    Raises:
        SyncError: If a step fails, or at the end if files were held back.
        SyncStopped: If ``stop_event`` was set before the cycle finished.
    """
    if repo.mode is SyncMode.PULL:
        return pull_only(repo)
    held = None
    try:
        committed = commit_local_changes(repo)
    except HeldBack as error:
        committed, held = error.committed, error
    _stop_if_requested(repo, stop_event, "pull")
    changed = _merge_or_overwrite(repo, stop_event, held, on_overwrite)
    if held:
        raise held
    return committed or changed
