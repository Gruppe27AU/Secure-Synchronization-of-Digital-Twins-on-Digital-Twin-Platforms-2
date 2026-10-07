"""
Git backup configuration.

Reads the git backup config file and turns it into objects,
so the other modules can use the values without parsing anything.

The file is TOML despite its ``.env`` name. Every path in it is a fragment:
``GIT_DIR`` is resolved against ``WORKSPACE_DIR`` and ``GIT_WORK_TREE``
against ``WORKSPACE_APP_DIR``, so callers receive absolute paths and never
have to know which fragment belongs to which root.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Template shipped with the package for the user to copy. Never read at
#: runtime: its values are placeholders, and loading them would clone
#: whatever happens to live at the placeholder URL.
EXAMPLE_CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.env.example"

#: Keys that must be present above any ``[assets]`` section.
REQUIRED_TOP_LEVEL_KEYS = ("HOME_DIR", "WORKSPACE_DIR", "WORKSPACE_APP_DIR")

#: Keys that must be present in every ``[assets.*]`` section.
REQUIRED_ASSET_KEYS = (
    "GIT_REPO_URL",
    "GIT_REPO_BRANCH",
    "GIT_REPO_USERNAME",
    "GIT_REPO_TOKEN",
    "GIT_DIR",
    "GIT_WORK_TREE",
)

#: Values copied from ``config.env.example`` or the README without being
#: filled in. A repository still carrying one of them is refused, so an
#: unedited copy of the template can never send a request to a remote.
PLACEHOLDER_VALUES = {
    "GIT_REPO_URL": frozenset({
        "https://gitlab.com/username/repository.git",
        "https://gitlab.com/username/private-repo.git",
        "https://gitlab.com/username/common-repo.git",
    }),
    "GIT_REPO_USERNAME": frozenset({"gitlab-username"}),
    "GIT_REPO_TOKEN": frozenset({"gitlab-api-token", "gitlab-access-token"}),
}

#: Sections recognised under ``[assets]``. Each one is optional on its own,
#: but at least one of them has to be configured.
ASSET_NAMES = ("private", "common")

_TOP_LEVEL = "the top level of the file"


class ConfigError(Exception):
    """
    Raised when the git backup configuration cannot be used.

    The message names the file, the section and the key at fault, so the
    user knows what to edit without reading any code.
    """


@dataclass
class RepoConfig:
    """
    Configuration for a single repository.

    Both paths are absolute and ready to use. ``token`` is kept out of the
    generated ``__repr__`` so printing or logging a config cannot leak it.
    """

    name: str
    repo_url: str
    branch: str
    username: str
    token: str = field(repr=False)
    git_dir: Path
    work_tree: Path


def _read_toml(config_path: Path) -> dict[str, Any]:
    """
    Read and parse a TOML file.

    Args:
        config_path: File to read.

    Returns:
        The parsed contents of the file.

    Raises:
        ConfigError: If the file cannot be read or is not valid TOML.
    """
    try:
        with open(config_path, "rb") as config_file:
            return tomllib.load(config_file)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{config_path} is not valid TOML: {error}") from error
    except OSError as error:
        raise ConfigError(f"{config_path} could not be read: {error}") from error


def _require(values: dict[str, Any], key: str, where: str, source: Path) -> str:
    """
    Return a non-empty string value, or explain why it is unusable.

    Args:
        values: Mapping the key is expected in.
        key: Key to look up.
        where: Human-readable location, used in the error message.
        source: File the values came from, used in the error message.

    Returns:
        The value belonging to ``key``.

    Raises:
        ConfigError: If the key is absent, not a string, or blank.
    """
    if key not in values:
        raise ConfigError(f"{source}: '{key}' is missing from {where}")

    value = values[key]
    if not isinstance(value, str):
        raise ConfigError(
            f"{source}: '{key}' in {where} must be a quoted string, "
            f"got {type(value).__name__}"
        )
    if not value.strip():
        raise ConfigError(f"{source}: '{key}' in {where} is empty")

    return value


def _build_repo(
    name: str,
    asset: dict[str, Any],
    workspace_dir: Path,
    app_dir: Path,
    source: Path,
) -> RepoConfig:
    """
    Build one :class:`RepoConfig` from an ``[assets.*]`` section.

    Args:
        name: Section name, for example ``private``.
        asset: The section's contents.
        workspace_dir: Root that ``GIT_DIR`` is relative to.
        app_dir: Root that ``GIT_WORK_TREE`` is relative to.
        source: File the section came from, used in error messages.

    Returns:
        The repository configuration, with both paths resolved.

    Raises:
        ConfigError: If any required key is missing or empty, or still
            holds a placeholder from the template.
    """
    where = f"[assets.{name}]"

    repo = RepoConfig(
        name=name,
        repo_url=_require(asset, "GIT_REPO_URL", where, source),
        branch=_require(asset, "GIT_REPO_BRANCH", where, source),
        username=_require(asset, "GIT_REPO_USERNAME", where, source),
        token=_require(asset, "GIT_REPO_TOKEN", where, source),
        git_dir=workspace_dir / _require(asset, "GIT_DIR", where, source),
        work_tree=app_dir / _require(asset, "GIT_WORK_TREE", where, source),
    )

    # Checked only now, once _require() has proven every value a string.
    for key, placeholders in PLACEHOLDER_VALUES.items():
        if asset[key] in placeholders:
            raise ConfigError(
                f"{source}: '{key}' in {where} is still the placeholder "
                f"from config.env.example; fill in your own value"
            )

    return repo


def _resolve_app_dir(home_dir: Path, app_dir_value: str) -> Path:
    """
    Resolve ``WORKSPACE_APP_DIR``, which may be absolute or relative to home.

    Paths in the config file describe the container filesystem, so a leading
    ``/`` marks an absolute path no matter which platform this runs on.

    Args:
        home_dir: Value of ``HOME_DIR``.
        app_dir_value: Value of ``WORKSPACE_APP_DIR``.

    Returns:
        The absolute application directory.
    """
    if app_dir_value.startswith("/"):
        return Path(app_dir_value)
    return home_dir / app_dir_value


def _check_distinct_remotes(repos: list[RepoConfig], source: Path) -> None:
    """
    Refuse two repositories that back up to the same remote branch.

    Both working trees would push to that branch, each winning every
    conflict against the other, so neither would hold the user's files.

    Args:
        repos: The configured repositories.
        source: File they came from, used in the error message.

    Raises:
        ConfigError: If two repositories share a URL and branch.
    """
    seen: dict[tuple[str, str], str] = {}
    for repo in repos:
        remote = (repo.repo_url, repo.branch)
        if remote in seen:
            raise ConfigError(
                f"{source}: [assets.{seen[remote]}] and [assets.{repo.name}] "
                f"both use {repo.repo_url} (branch {repo.branch}); each "
                f"needs a remote of its own"
            )
        seen[remote] = repo.name


def load_config(config_path: Path) -> list[RepoConfig]:
    """
    Load the git backup configuration.

    There is no fallback: a missing file is an error here, and it is up to
    the caller to treat an absent config as "git backup disabled" (see
    :mod:`admin.git.bootstrap`).

    Args:
        config_path: The user's config file, normally
            ``$WORKSPACE_APP_DIR/config.env``.

    Returns:
        One entry per configured repository, in ``ASSET_NAMES`` order, with
        ``git_dir`` and ``work_tree`` resolved to absolute paths.

    Raises:
        ConfigError: If the file cannot be read, if a required key is
            missing, empty or a placeholder, if no repository is configured
            at all, or if two repositories share a remote branch.
    """
    source = config_path
    config = _read_toml(source)

    home_dir = Path(_require(config, "HOME_DIR", _TOP_LEVEL, source))
    workspace_dir = Path(_require(config, "WORKSPACE_DIR", _TOP_LEVEL, source))
    app_dir = _resolve_app_dir(
        home_dir,
        _require(config, "WORKSPACE_APP_DIR", _TOP_LEVEL, source),
    )

    assets = config.get("assets")
    if not isinstance(assets, dict):
        raise ConfigError(f"{source}: no [assets] section found")

    repos = []
    for name in ASSET_NAMES:
        if name not in assets:
            continue

        asset = assets[name]
        if not isinstance(asset, dict):
            raise ConfigError(f"{source}: [assets.{name}] is not a section")

        repos.append(_build_repo(name, asset, workspace_dir, app_dir, source))

    if not repos:
        raise ConfigError(
            f"{source}: no repositories configured, expected at least one of "
            + " or ".join(f"[assets.{name}]" for name in ASSET_NAMES)
        )

    _check_distinct_remotes(repos, source)

    return repos
