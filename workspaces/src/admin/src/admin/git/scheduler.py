"""
Periodic scheduling of the git backup.

Decides *when* :mod:`admin.git.sync` runs, and nothing about git itself.
The synchronization happens on a background thread, so it never blocks the
admin service's HTTP server.
"""

import logging
import threading

from admin.git.config import RepoConfig
from admin.git.sync import SyncError, sync_once

logger = logging.getLogger(__name__)

#: How often the working trees are committed and pushed, in seconds.
DEFAULT_SYNC_INTERVAL_SECONDS = 300

#: Name of the background thread, so it is recognisable in a stack dump.
SYNC_THREAD_NAME = "git-sync"


def sync_all(repos: list[RepoConfig]) -> None:
    """
    Synchronize every repository once.

    A repository that fails is logged and skipped, so one broken remote
    cannot stop the others from being backed up.

    Every exception is caught, not just :class:`SyncError`. This runs on a
    background thread, where an escaping exception would end the thread and
    silently stop all further backups while the service kept serving
    requests as if nothing were wrong.

    Args:
        repos: Repositories to synchronize.
    """
    for repo in repos:
        try:
            sync_once(repo)
        except SyncError as error:
            logger.error("%s", error)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Unexpected error while syncing repository '%s'", repo.name
            )


def _sync_loop(
    repos: list[RepoConfig],
    interval_seconds: int,
    stop_event: threading.Event,
) -> None:
    """
    Synchronize the repositories until the stop event is set.

    Waiting on the event rather than sleeping means the loop can be shut
    down immediately instead of only at the end of an interval.

    Args:
        repos: Repositories to synchronize.
        interval_seconds: Seconds to wait between synchronizations.
        stop_event: Set this to end the loop.
    """
    while not stop_event.wait(interval_seconds):
        sync_all(repos)


def start_sync_scheduler(
    repos: list[RepoConfig],
    interval_seconds: int,
    stop_event: threading.Event,
) -> threading.Thread:
    """
    Start synchronizing the repositories on a background thread.

    The thread is a daemon, so it does not keep the process alive on
    shutdown. The first synchronization happens after one interval, not
    immediately, because the repositories were just cloned.

    Args:
        repos: Repositories to synchronize.
        interval_seconds: Seconds to wait between synchronizations.
        stop_event: Set this to end the loop.

    Returns:
        The started thread.
    """
    thread = threading.Thread(
        target=_sync_loop,
        args=(repos, interval_seconds, stop_event),
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
