"""
Startup bootstrap for git-backed workspace assets.

Wires :mod:`admin.git.config` (reading ``config.toml``) to
:mod:`admin.git.clone` (cloning a repository) and
:mod:`admin.git.scheduler` (committing and pushing the clones from then
on), so :mod:`admin.main` only has to make one call at startup:
:func:`start_git_backup`, which does both on a background thread, and one
on shutdown: :func:`stop_git_backup`.
"""

import logging
import os
import threading
from pathlib import Path

from admin.git.clone import CloneError, clone_asset, is_cloned
from admin.git.config import (
    ConfigError,
    RepoConfig,
    load_config,
    load_sync_interval,
)
from admin.git.provision import provision_config
from admin.git.scheduler import start_sync_scheduler
from admin.git.status import SyncStatus

logger = logging.getLogger(__name__)

#: Filename of the config file.
DEFAULT_CONFIG_FILE = "config.toml"

#: Directory holding the config file, below the root.
CONFIG_DIR = Path(".config", "workspace-admin")

#: Name of the thread that clones on startup, recognisable in a stack dump.
BOOTSTRAP_THREAD_NAME = "git-bootstrap"

#: Longest a stopping service waits for the git command it is in. Kept
#: under the 10 seconds ``docker stop`` allows before it kills the process.
SHUTDOWN_TIMEOUT_SECONDS = 8


def _root_dir() -> Path:
    """Resolve ``$PERSISTENT_DIR``, or the home directory when it is unset."""
    persistent_dir = os.environ.get("PERSISTENT_DIR")
    return Path(persistent_dir) if persistent_dir else Path.home()


def _config_path() -> Path:
    """Resolve the path to ``config.toml`` below the root."""
    return _root_dir() / CONFIG_DIR / DEFAULT_CONFIG_FILE


def _template_path() -> Path | None:
    """Resolve the config template, or None when none is installed."""
    template = os.environ.get("WORKSPACE_CONFIG_TEMPLATE")
    return Path(template) if template else None


def _log_missing_config(config_path: Path) -> None:
    """Report that the git backup is off because ``config_path`` is absent."""
    logger.info("%s not found; git backup is disabled", config_path)


def _load_repos(failure_message: str) -> list[RepoConfig]:
    """
    Load the configured repositories, treating a missing file as "disabled".

    A workspace nobody has configured for git backup has no ``config.toml``,
    and that is a normal state rather than an error: nothing is cloned and
    nothing is synchronized.

    Args:
        failure_message: Prefix for the error logged when the file exists
            but cannot be used.

    Returns:
        The configured repositories, or an empty list when the file is
        missing or unusable.
    """
    config_path = _config_path()

    if not config_path.is_file():
        _log_missing_config(config_path)
        return []

    try:
        return load_config(config_path, _root_dir())
    except ConfigError as exc:
        logger.error("%s: %s", failure_message, exc)
        return []


def clone_configured_repos() -> dict[str, bool]:
    """
    Clone every repository described in ``config.toml``.

    Reads the config file at ``<root>/.config/workspace-admin/config.toml`` and
    clones each configured repository (``private`` and/or ``common``) that
    is not already present. When the file does not exist the git backup is
    disabled and nothing is cloned. Each repository is cloned
    independently: one that fails to clone (bad credentials, unreachable
    remote, ...) is logged and skipped rather than stopping the others, so
    a single broken remote does not prevent the admin service from
    starting or the rest of the workspace assets from being cloned.

    Returns:
        One entry per configured repository, mapping its name to whether
        a clone was actually performed (False when skipped because it was
        already cloned, or because cloning failed). Empty when the config
        file is missing or cannot be used at all.
    """
    results = {}
    for repo in _load_repos("Cannot clone git assets"):
        try:
            results[repo.name] = clone_asset(repo)
        except CloneError as exc:
            logger.error("%s", exc)
            results[repo.name] = False

    return results


def start_git_sync(
    status: SyncStatus,
    interval_seconds: int | None = None,
    stop_event: threading.Event | None = None,
) -> tuple[threading.Thread, threading.Event] | None:
    """
    Start committing and pushing the cloned repositories periodically.

    Reads the same config file as :func:`clone_configured_repos` and
    schedules every repository that has actually been cloned, so
    repositories added to the config later are picked up as soon as they
    exist on disk.
    Failures are logged, not raised, so a broken git configuration does
    not prevent the admin service from starting.

    Args:
        status: Where the outcome of every synchronization is recorded.
            Left disabled when nothing is scheduled.
        interval_seconds: Seconds to wait between synchronizations. None
            reads ``SYNC_INTERVAL_SECONDS`` from the config file.
        stop_event: Set this to stop the sync. A new one is made when
            None is given.

    Returns:
        The background thread and the event that stops it, or None when
        there is nothing to synchronize.
    """
    repos = _load_repos("Cannot start git sync")
    if not repos:
        return None

    cloned_repos = [repo for repo in repos if is_cloned(repo.git_dir)]
    if not cloned_repos:
        logger.warning(
            "No cloned repositories found for %s; git sync not started",
            _config_path(),
        )
        return None

    if interval_seconds is None:
        try:
            interval_seconds = load_sync_interval(_config_path())
        except ConfigError as exc:
            logger.error("Cannot start git sync: %s", exc)
            return None

    if stop_event is None:
        stop_event = threading.Event()
    thread = start_sync_scheduler(
        cloned_repos, interval_seconds, stop_event, status
    )

    return thread, stop_event


def _clone_then_sync(
    status: SyncStatus, interval_seconds: int | None, stop_event: threading.Event
) -> None:
    """
    Create a missing config file, or clone and start syncing.

    Runs on the thread started by :func:`start_git_backup`. A config file
    created now holds only placeholders, so nothing is cloned until the
    user has filled it in and restarted. Every step logs its own failures;
    anything unexpected is caught here too, because an exception escaping
    a thread is easy to miss.

    Args:
        status: Where the outcome of every synchronization is recorded.
        interval_seconds: Seconds to wait between synchronizations, or None
            for the config file's ``SYNC_INTERVAL_SECONDS``.
        stop_event: Set when the service is stopping.
    """
    try:
        config_path = _config_path()
        if provision_config(config_path, _template_path()):
            return
        if not config_path.is_file():
            _log_missing_config(config_path)
            return
        clone_configured_repos()
        if stop_event.is_set():
            return
        started = start_git_sync(status, interval_seconds, stop_event)
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
    Start the git backup without holding up the caller.

    Cloning talks to the network and can take minutes, so it runs on a
    background daemon thread, followed by :func:`start_git_sync` once it is
    done. The HTTP API therefore comes up straight away, whatever the
    remotes are doing. Until the clones are done, ``status`` still reports
    the backup as disabled.

    Args:
        status: Where the outcome of every synchronization is recorded.
        interval_seconds: Seconds to wait between synchronizations. None,
            the default, uses ``SYNC_INTERVAL_SECONDS`` from the config
            file; a number overrides it.
        stop_event: Set this, or pass it to :func:`stop_git_backup`, to
            stop the backup. Without one the backup only ends with the
            process.

    Returns:
        The started thread. It ends once the backup has stopped, or right
        after cloning when there is nothing to synchronize.
    """
    logger.info("Git backup config file: %s", _config_path().absolute())
    if stop_event is None:
        stop_event = threading.Event()
    thread = threading.Thread(
        target=_clone_then_sync,
        args=(status, interval_seconds, stop_event),
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
    Stop the git backup and wait, for a limited time, until it has.

    No further git command is started once ``stop_event`` is set. The one
    running when this is called is waited for, up to ``timeout_seconds``:
    a push or clone can take many minutes, longer than any shutdown should.

    Args:
        thread: The thread returned by :func:`start_git_backup`.
        stop_event: The event passed to :func:`start_git_backup`.
        timeout_seconds: Longest time to wait.

    Returns:
        True if the backup stopped in time, False if a git command was
        still running when the wait ran out.
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
