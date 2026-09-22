"""
Startup bootstrap for git-backed workspace assets.

Wires :mod:`admin.git.config` (reading ``config.env``) to
:mod:`admin.git.clone` (cloning a repository), for the shared ``common``
asset that every user in the workspace should have access to.
"""

import logging
import os
from pathlib import Path

from admin.git.clone import CloneError, clone_asset
from admin.git.config import ConfigError, RepoConfig, load_config

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
