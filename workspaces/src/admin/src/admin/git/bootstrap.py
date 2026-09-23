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
from admin.git.config import ConfigError, load_config
from admin.git.scheduler import DEFAULT_SYNC_INTERVAL_SECONDS, start_sync_scheduler

logger = logging.getLogger(__name__)

#: Filename of the config file, resolved against ``$WORKSPACE_APP_DIR``.
DEFAULT_CONFIG_FILE = "config.env"


def _config_path() -> Path:
    """Resolve the path to ``config.env`` from the environment."""
    app_dir = Path(os.environ.get("WORKSPACE_APP_DIR", "."))
    return app_dir / DEFAULT_CONFIG_FILE


def clone_configured_repos() -> dict[str, bool]:
    """
    Clone every repository described in ``config.env``.

    Reads the config file located at ``$WORKSPACE_APP_DIR/config.env``
    (falling back to the bundled ``config.env.example`` when that file is
    missing, per :func:`admin.git.config.load_config`), and clones each
    configured repository (``private`` and/or ``common``) that is not
    already present. Each repository is cloned independently: one that
    fails to clone (bad credentials, unreachable remote, ...) is logged
    and skipped rather than stopping the others, so a single broken
    remote does not prevent the admin service from starting or the rest
    of the workspace assets from being cloned.

    Returns:
        One entry per configured repository, mapping its name to whether
        a clone was actually performed (False when skipped because it was
        already cloned, or because cloning failed). Empty when the config
        file cannot be read at all.
    """
    config_path = _config_path()

    try:
        repos = load_config(config_path)
    except ConfigError as exc:
        logger.error("Cannot clone git assets: %s", exc)
        return {}

    results = {}
    for repo in repos:
        try:
            results[repo.name] = clone_asset(repo)
        except CloneError as exc:
            logger.error("%s", exc)
            results[repo.name] = False

    return results


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
