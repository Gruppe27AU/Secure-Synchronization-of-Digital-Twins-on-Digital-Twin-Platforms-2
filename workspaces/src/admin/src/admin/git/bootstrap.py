"""
Startup bootstrap for git-backed workspace assets.

Wires :mod:`admin.git.config` (reading ``config.env``) to
:mod:`admin.git.clone` (cloning a repository) and
:mod:`admin.git.scheduler` (committing and pushing the clones from then
on), so :mod:`admin.main` only has to make two calls at startup.
"""

import logging
import os
import threading
from pathlib import Path

from admin.git.clone import CloneError, clone_asset, is_cloned
from admin.git.config import ConfigError, RepoConfig, load_config
from admin.git.scheduler import DEFAULT_SYNC_INTERVAL_SECONDS, start_sync_scheduler

logger = logging.getLogger(__name__)

#: Name of the ``[assets.*]`` section cloned on startup.
COMMON_ASSET_NAME = "common"

#: Filename of the config file, resolved against ``$WORKSPACE_APP_DIR``.
DEFAULT_CONFIG_FILE = "config.env"


def _config_path() -> Path:
    """Resolve the path to ``config.env`` from the environment."""
    app_dir = Path(os.environ.get("WORKSPACE_APP_DIR", "."))
    return app_dir / DEFAULT_CONFIG_FILE


def _find_common_repo(repos: list[RepoConfig]) -> RepoConfig | None:
    """Return the ``common`` entry from a list of repository configs."""
    for repo in repos:
        if repo.name == COMMON_ASSET_NAME:
            return repo
    return None


def clone_common_repo() -> bool:
    """
    Clone the shared ``common`` repository described in ``config.env``.

    Reads the config file located at ``$WORKSPACE_APP_DIR/config.env``
    (falling back to the bundled ``config.env.example`` when that file is
    missing, per :func:`admin.git.config.load_config`), and clones the
    ``[assets.common]`` repository if it is not already present. Failures
    are logged, not raised, so a missing or broken git configuration does
    not prevent the admin service from starting.

    Returns:
        True if a clone was performed, False if it was skipped or failed.
    """
    config_path = _config_path()

    try:
        repos = load_config(config_path)
    except ConfigError as exc:
        logger.error("Cannot clone common repository: %s", exc)
        return False

    common_repo = _find_common_repo(repos)
    if common_repo is None:
        logger.error(
            "Cannot clone common repository: no [assets.common] entry in %s",
            config_path,
        )
        return False

    try:
        return clone_asset(common_repo)
    except CloneError as exc:
        logger.error("%s", exc)
        return False


def start_git_sync(
    interval_seconds: int = DEFAULT_SYNC_INTERVAL_SECONDS,
) -> tuple[threading.Thread, threading.Event] | None:
    """
    Start committing and pushing the cloned repositories periodically.

    Reads the same config file as :func:`clone_common_repo` and schedules
    every repository that has actually been cloned, so repositories added
    to the config later are picked up as soon as they exist on disk.
    Failures are logged, not raised, so a broken git configuration does
    not prevent the admin service from starting.

    Args:
        interval_seconds: Seconds to wait between synchronizations.

    Returns:
        The background thread and the event that stops it, or None when
        there is nothing to synchronize. The caller may drop both: the
        thread is a daemon, so it ends with the process either way.
    """
    config_path = _config_path()

    try:
        repos = load_config(config_path)
    except ConfigError as exc:
        logger.error("Cannot start git sync: %s", exc)
        return None

    cloned_repos = [repo for repo in repos if is_cloned(repo.git_dir)]
    if not cloned_repos:
        logger.warning(
            "No cloned repositories found for %s; git sync not started",
            config_path,
        )
        return None

    stop_event = threading.Event()
    thread = start_sync_scheduler(cloned_repos, interval_seconds, stop_event)

    return thread, stop_event
