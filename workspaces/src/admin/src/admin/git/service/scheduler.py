"""
Running the backup cycle on a background thread, every interval.

A repository whose clone failed is cloned again each interval, so a fixed
token or a remote back online needs no restart.

Every exception is caught and recorded: one escaping the thread would end
the backup silently while the HTTP API kept running.
"""

import logging
import threading

from admin.git.repo.clone import CloneError, clone_asset, is_cloned, restore_git_file
from admin.git.setup.config import RepoConfig
from admin.git.run.errors import SyncError, SyncErrorKind
from admin.git.service.status import SyncStatus
from admin.git.repo.sync import SyncStopped, sync_once

logger = logging.getLogger(__name__)

#: Name of the background thread, recognisable in a stack dump.
SYNC_THREAD_NAME = "git-sync"


def clone_and_record(repo: RepoConfig, status: SyncStatus) -> bool:
    """
    Clone ``repo`` and record the outcome; failures are logged, not raised.

    Returns:
        True if a clone was made; False if already cloned or it failed.
    """
    try:
        cloned = clone_asset(repo)
    except CloneError as error:
        logger.error("%s", error)
        status.record_clone_failure(repo.name, error.kind)
        return False
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Unexpected error while cloning repository '%s'", repo.name)
        status.record_clone_failure(repo.name, SyncErrorKind.UNEXPECTED)
        return False
    status.record_cloned(repo.name)
    return cloned


def _sync_one(
    repo: RepoConfig, status: SyncStatus, stop_event: threading.Event | None
) -> bool:
    """
    Run one cycle for ``repo`` and record the outcome.

    Returns:
        False if the service is stopping; nothing is recorded then.
    """
    try:
        restore_git_file(repo)
        sync_once(repo, stop_event)
    except SyncStopped as stopped:
        logger.info("%s", stopped)
        return False
    except SyncError as error:
        logger.error("%s", error)
        status.record_failure(repo.name, error.kind)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Unexpected error while syncing repository '%s'", repo.name)
        status.record_failure(repo.name, SyncErrorKind.UNEXPECTED)
    else:
        status.record_success(repo.name)
    return True


def sync_all(
    repos: list[RepoConfig],
    status: SyncStatus,
    stop_event: threading.Event | None = None,
) -> None:
    """
    Run one cycle for every repository, cloning those not cloned yet.

    A failing repository does not stop the others. Once ``stop_event`` is
    set, nothing more is started or recorded.
    """
    for repo in repos:
        if stop_event is not None and stop_event.is_set():
            return
        if not is_cloned(repo.git_dir):
            clone_and_record(repo, status)
        elif not _sync_one(repo, status, stop_event):
            return


def _sync_loop(
    repos: list[RepoConfig],
    interval_seconds: int,
    stop_event: threading.Event,
    status: SyncStatus,
) -> None:
    """Run :func:`sync_all` every interval until ``stop_event`` is set."""
    while not stop_event.wait(interval_seconds):
        sync_all(repos, status, stop_event)


def start_sync_scheduler(
    repos: list[RepoConfig],
    interval_seconds: int,
    stop_event: threading.Event,
    status: SyncStatus,
) -> threading.Thread:
    """
    Start :func:`sync_all` on a daemon thread, first after one interval.

    Returns:
        The started thread.
    """
    status.enable([repo.name for repo in repos])
    thread = threading.Thread(
        target=_sync_loop,
        args=(repos, interval_seconds, stop_event, status),
        name=SYNC_THREAD_NAME,
        daemon=True,
    )
    thread.start()
    logger.info(
        "Started git sync for %d repository/repositories every %d seconds",
        len(repos),
        interval_seconds,
    )
    return thread
