"""
Starting and stopping the git backup.

:mod:`admin.main` calls :func:`start_git_backup` on startup, which clones and
then schedules the backup on a background thread, and :func:`stop_git_backup`
on shutdown. The config file is chosen once, so every step reads the same
file. Failures are logged, never raised: the HTTP API must start regardless.
"""

import logging
import os
import threading
from pathlib import Path

from admin.git.clone import is_cloned
from admin.git.config import (
    ConfigError,
    PlaceholderError,
    RepoConfig,
    load_config,
    load_sync_interval,
)
from admin.git.locate import discover_config
from admin.git.provision import provision_config
from admin.git.scheduler import clone_and_record, start_sync_scheduler
from admin.git.status import BackupState, SyncStatus

logger = logging.getLogger(__name__)

#: Name of the thread that clones on startup, recognisable in a stack dump.
BOOTSTRAP_THREAD_NAME = "git-bootstrap"

#: Longest a stopping service waits for the running git command; under the
#: 10 seconds ``docker stop`` allows.
SHUTDOWN_TIMEOUT_SECONDS = 8


def _root_dir() -> Path:
    """Return ``$PERSISTENT_DIR``, or the home directory when it is unset."""
    persistent_dir = os.environ.get("PERSISTENT_DIR")
    return Path(persistent_dir) if persistent_dir else Path.home()


def _template_path() -> Path | None:
    """Return the config template, or None when none is installed."""
    template = os.environ.get("WORKSPACE_CONFIG_TEMPLATE")
    return Path(template) if template else None


def _log_missing_config(config_path: Path) -> None:
    """Log that the backup is off because ``config_path`` is absent."""
    logger.info("%s not found; git backup is disabled", config_path)


def _load_repos(
    config_path: Path, failure_message: str, status: SyncStatus
) -> list[RepoConfig]:
    """
    Load the configured repositories; a missing file means "disabled".

    Args:
        failure_message: Prefix of the error logged for an unusable file.
        status: Set to ``needs_setup`` or ``config_error`` on failure.

    Returns:
        The repositories, or an empty list.
    """
    if not config_path.is_file():
        _log_missing_config(config_path)
        return []
    try:
        return load_config(config_path, _root_dir())
    except PlaceholderError as exc:
        logger.error("%s: %s", failure_message, exc)
        status.set_state(BackupState.NEEDS_SETUP)
    except ConfigError as exc:
        logger.error("%s: %s", failure_message, exc)
        status.set_state(BackupState.CONFIG_ERROR)
    return []


def clone_configured_repos(status: SyncStatus, config_path: Path) -> dict[str, bool]:
    """
    Clone every configured repository not cloned yet.

    A failed clone does not stop the others; the scheduler retries it.

    Returns:
        Each repository's name, and whether it was cloned now.
    """
    repos = _load_repos(config_path, "Cannot clone git assets", status)
    if repos:
        status.configure(
            [repo.name for repo in repos],
            cloned={repo.name for repo in repos if is_cloned(repo.git_dir)},
        )
    return {repo.name: clone_and_record(repo, status) for repo in repos}


def start_git_sync(
    status: SyncStatus,
    config_path: Path,
    interval_seconds: int | None = None,
    stop_event: threading.Event | None = None,
) -> tuple[threading.Thread, threading.Event] | None:
    """
    Schedule the backup of every configured repository.

    Args:
        interval_seconds: Seconds between cycles; None reads
            ``SYNC_INTERVAL_SECONDS`` from the config file.
        stop_event: Stops the backup; a new one is made if None.

    Returns:
        The thread and its stop event, or None if nothing is scheduled.
    """
    repos = _load_repos(config_path, "Cannot start git sync", status)
    if not repos:
        return None
    if interval_seconds is None:
        try:
            interval_seconds = load_sync_interval(config_path)
        except ConfigError as exc:
            logger.error("Cannot start git sync: %s", exc)
            status.set_state(BackupState.CONFIG_ERROR)
            return None
    if stop_event is None:
        stop_event = threading.Event()
    thread = start_sync_scheduler(repos, interval_seconds, stop_event, status)
    return thread, stop_event


def _clone_then_sync(
    status: SyncStatus,
    config_path: Path,
    interval_seconds: int | None,
    stop_event: threading.Event,
) -> None:
    """
    Create a missing config file, or clone and start the backup.

    A config file created now holds placeholders, so nothing is cloned
    until the user fills it in and restarts.
    """
    try:
        if provision_config(config_path, _template_path()):
            status.set_state(BackupState.NEEDS_SETUP)
            return
        if not config_path.is_file():
            _log_missing_config(config_path)
            return
        clone_configured_repos(status, config_path)
        if stop_event.is_set():
            return
        started = start_git_sync(status, config_path, interval_seconds, stop_event)
        if started:
            sync_thread, _ = started
            sync_thread.join()
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Unexpected error while starting the git backup")


def start_git_backup(
    status: SyncStatus,
    interval_seconds: int | None = None,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """
    Choose the config file, then clone and back up on a daemon thread.

    Args:
        interval_seconds: Overrides ``SYNC_INTERVAL_SECONDS`` when given.
        stop_event: Stops the backup (see :func:`stop_git_backup`); without
            one it ends with the process.

    Returns:
        The started thread.
    """
    config_path = discover_config().path
    logger.info("Git backup config file: %s", config_path)
    if stop_event is None:
        stop_event = threading.Event()
    thread = threading.Thread(
        target=_clone_then_sync,
        args=(status, config_path, interval_seconds, stop_event),
        name=BOOTSTRAP_THREAD_NAME,
        daemon=True,
    )
    thread.start()
    return thread


def stop_git_backup(
    thread: threading.Thread,
    stop_event: threading.Event,
    timeout_seconds: float = SHUTDOWN_TIMEOUT_SECONDS,
) -> bool:
    """
    Stop the backup and wait for the running git command, up to a limit.

    Returns:
        False if a git command was still running when the wait ran out.
    """
    stop_event.set()
    thread.join(timeout_seconds)
    if thread.is_alive():
        logger.warning(
            "The git backup was still running a git command after %g seconds; "
            "not waiting for it any longer",
            timeout_seconds,
        )
        return False
    logger.info("Stopped the git backup")
    return True
