"""
Reading ``config.toml`` into :class:`admin.git.setup.config.RepoConfig` objects.

A missing file is an error here; :mod:`admin.git.service.bootstrap` treats it as
"backup disabled".
"""

import logging
import tomllib
from pathlib import Path
from typing import Any

from admin.git.setup.config import (
    ASSET_NAMES,
    DEFAULT_SYNC_INTERVAL_SECONDS,
    GIT_DIR_PARENT,
    KNOWN_ASSET_KEYS,
    KNOWN_TOP_LEVEL_KEYS,
    SYNC_INTERVAL_KEY,
    WORK_TREE_PARENT,
    ConfigError,
    RepoConfig,
)
from admin.git.setup.validate import (
    check_distinct_remotes,
    check_outside_work_trees,
    mode,
    reject_placeholders,
    require,
    sync_interval_problem,
    token,
    validate_branch,
    validate_url,
    warn_shared_permissions,
    warn_unknown_keys,
)

logger = logging.getLogger(__name__)


def _read_toml(config_path: Path) -> dict[str, Any]:
    """
    Parse ``config_path``.

    Raises:
        ConfigError: If it cannot be read or is not valid TOML.
    """
    try:
        with open(config_path, "rb") as config_file:
            return tomllib.load(config_file)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{config_path} is not valid TOML: {error}") from error
    except OSError as error:
        raise ConfigError(f"{config_path} could not be read: {error}") from error


def _build_repo(name: str, asset: dict[str, Any], root: Path, source: Path) -> RepoConfig:
    """
    Build the :class:`RepoConfig` of section ``[assets.<name>]``.

    Raises:
        ConfigError: If a value is missing, unsafe or a placeholder.
    """
    where = f"[assets.{name}]"
    # A misplaced SYNC_INTERVAL_SECONDS has a warning of its own.
    warn_unknown_keys(asset, KNOWN_ASSET_KEYS | {SYNC_INTERVAL_KEY}, where, source)
    sync_mode = mode(name, asset, where, source)
    repo = RepoConfig(
        name=name,
        repo_url=validate_url(require(asset, "GIT_REPO_URL", where, source), where, source),
        branch=validate_branch(
            require(asset, "GIT_REPO_BRANCH", where, source), where, source
        ),
        token=token(asset, sync_mode, where, source),
        git_dir=root / GIT_DIR_PARENT / name,
        work_tree=root / WORK_TREE_PARENT / name,
        mode=sync_mode,
    )
    reject_placeholders(asset, where, source)
    return repo


def _warn_misplaced_interval(config: dict[str, Any], source: Path) -> None:
    """Warn about ``SYNC_INTERVAL_SECONDS`` inside an ``[assets]`` section."""
    assets = config.get("assets")
    if not isinstance(assets, dict):
        return
    for name, asset in assets.items():
        if isinstance(asset, dict) and SYNC_INTERVAL_KEY in asset:
            logger.warning(
                "%s: '%s' is in [assets.%s], where it has no effect; move "
                "it to the top level, above every [assets] section",
                source, SYNC_INTERVAL_KEY, name,
            )


def load_sync_interval(config_path: Path) -> int:
    """
    Return ``SYNC_INTERVAL_SECONDS``, or the default if unset or unusable
    (logged as a warning).

    Raises:
        ConfigError: If the file cannot be read or is not valid TOML.
    """
    config = _read_toml(config_path)
    _warn_misplaced_interval(config, config_path)
    if SYNC_INTERVAL_KEY not in config:
        return DEFAULT_SYNC_INTERVAL_SECONDS
    seconds = config[SYNC_INTERVAL_KEY]
    problem = sync_interval_problem(seconds)
    if problem:
        logger.warning(
            "%s: '%s' %s; using the default of %d seconds",
            config_path, SYNC_INTERVAL_KEY, problem, DEFAULT_SYNC_INTERVAL_SECONDS,
        )
        return DEFAULT_SYNC_INTERVAL_SECONDS
    return seconds


def _assets(config: dict[str, Any], source: Path) -> dict[str, Any]:
    """
    Return the ``[assets]`` table.

    Raises:
        ConfigError: If there is none.
    """
    assets = config.get("assets")
    if not isinstance(assets, dict):
        raise ConfigError(f"{source}: no [assets] section found")
    warn_unknown_keys(assets, frozenset(ASSET_NAMES), "[assets]", source)
    return assets


def load_config(config_path: Path, root: Path) -> list[RepoConfig]:
    """
    Read every configured repository, in :data:`ASSET_NAMES` order.

    Args:
        root: Directory the repositories go below, normally ``$PERSISTENT_DIR``.

    Raises:
        ConfigError: If the file or a value is unusable, no repository is
            configured, two share a remote branch, or a work tree holds the file.
    """
    config = _read_toml(config_path)
    warn_shared_permissions(config_path)
    warn_unknown_keys(config, KNOWN_TOP_LEVEL_KEYS, "the top level", config_path)
    assets = _assets(config, config_path)
    repos = []
    for name in ASSET_NAMES:
        if name not in assets:
            continue
        if not isinstance(assets[name], dict):
            raise ConfigError(f"{config_path}: [assets.{name}] is not a section")
        repos.append(_build_repo(name, assets[name], root, config_path))
    if not repos:
        raise ConfigError(
            f"{config_path}: no repositories configured, expected at least one of "
            + " or ".join(f"[assets.{name}]" for name in ASSET_NAMES)
        )
    check_distinct_remotes(repos, config_path)
    check_outside_work_trees(repos, config_path)
    return repos
