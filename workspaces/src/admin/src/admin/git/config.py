"""
Git backup configuration.

Reads the git backup config file and turns it into objects,
so the other modules can use the values without parsing anything.

The file is TOML despite its ``.env`` name. Every path in it is a fragment:
``GIT_DIR`` is resolved against ``WORKSPACE_DIR`` and ``GIT_WORK_TREE``
against ``WORKSPACE_APP_DIR``, so callers receive absolute paths and never
have to know which fragment belongs to which root.

Each fragment must name a subdirectory of its root: absolute fragments,
``..`` and fragments that collapse to the root itself are rejected. The
resulting directories must not overlap each other, and no work tree may
contain the config file, because every file in a work tree is committed and
pushed, and the config file holds the tokens.
"""

import itertools
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

#: Template shipped with the package, used when the user has no config file.
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


def _resolve_inside(root: Path, key: str, value: str, where: str, source: Path) -> Path:
    """
    Join a path fragment onto its root and require the result to stay inside.

    The check is lexical rather than :meth:`Path.resolve`: the paths describe
    the container filesystem and may not exist yet, so asking the host
    filesystem about them would give the wrong answer (and on Windows would
    prepend a drive letter).

    Args:
        root: Directory the fragment is relative to.
        key: Name of the key, used in the error message.
        value: The fragment, for example ``assets/private``.
        where: Human-readable location, used in the error message.
        source: File the value came from, used in the error message.

    Returns:
        ``root`` joined with ``value``, strictly below ``root``.

    Raises:
        ConfigError: If the fragment contains ``..``, is absolute, or names
            ``root`` itself.
    """
    if ".." in PurePosixPath(value).parts:
        raise ConfigError(f"{source}: '{key}' in {where} must not contain '..'")

    # pathlib discards ``root`` when ``value`` is absolute and drops "."
    # components, so both cases show up in the joined path.
    path = root / value
    if not path.is_relative_to(root) or path == root:
        raise ConfigError(
            f"{source}: '{key}' in {where} must name a subdirectory of {root}, "
            f"got '{value}'"
        )

    return path


def _check_disjoint(repos: list[RepoConfig], source: Path) -> None:
    """
    Require every git directory and work tree to be separate from the others.

    A work tree nested in another one would be committed to both remotes,
    and a git directory inside a work tree would be committed as content.

    Args:
        repos: The configured repositories.
        source: File the repositories came from, used in the error message.

    Raises:
        ConfigError: If two of the directories are equal or nested.
    """
    paths = []
    for repo in repos:
        paths.append((f"GIT_DIR in [assets.{repo.name}]", repo.git_dir))
        paths.append((f"GIT_WORK_TREE in [assets.{repo.name}]", repo.work_tree))

    for (label_a, path_a), (label_b, path_b) in itertools.combinations(paths, 2):
        if path_a.is_relative_to(path_b) or path_b.is_relative_to(path_a):
            raise ConfigError(
                f"{source}: {label_a} ({path_a}) and {label_b} ({path_b}) "
                "overlap; every git directory and work tree must be separate"
            )


def _check_config_outside_work_trees(repos: list[RepoConfig], source: Path) -> None:
    """
    Require the config file to lie outside every work tree.

    Unlike the other checks this one resolves against the real filesystem,
    because ``source`` comes from ``$WORKSPACE_APP_DIR`` rather than from the
    file itself, and may be relative or reached through a symlink.

    Args:
        repos: The configured repositories.
        source: The config file that was read.

    Raises:
        ConfigError: If a work tree contains the config file, which would
            commit and push the tokens it holds.
    """
    config_file = source.resolve()
    for repo in repos:
        if config_file.is_relative_to(repo.work_tree.resolve()):
            raise ConfigError(
                f"{source}: GIT_WORK_TREE in [assets.{repo.name}] "
                f"({repo.work_tree}) contains the config file, which would "
                "commit and push its tokens"
            )


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
        ConfigError: If any required key is missing or empty, or a path
            does not name a subdirectory of its root.
    """
    where = f"[assets.{name}]"

    return RepoConfig(
        name=name,
        repo_url=_require(asset, "GIT_REPO_URL", where, source),
        branch=_require(asset, "GIT_REPO_BRANCH", where, source),
        username=_require(asset, "GIT_REPO_USERNAME", where, source),
        token=_require(asset, "GIT_REPO_TOKEN", where, source),
        git_dir=_resolve_inside(
            workspace_dir,
            "GIT_DIR",
            _require(asset, "GIT_DIR", where, source),
            where,
            source,
        ),
        work_tree=_resolve_inside(
            app_dir,
            "GIT_WORK_TREE",
            _require(asset, "GIT_WORK_TREE", where, source),
            where,
            source,
        ),
    )


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


def load_config(config_path: Path) -> list[RepoConfig]:
    """
    Load the git backup configuration.

    Falls back to the bundled ``config.env.example`` when ``config_path``
    does not exist, so a workspace without a user config still starts.

    Args:
        config_path: The user's config file, normally
            ``$WORKSPACE_APP_DIR/config.env``.

    Returns:
        One entry per configured repository, in ``ASSET_NAMES`` order, with
        ``git_dir`` and ``work_tree`` resolved to absolute paths.

    Raises:
        ConfigError: If neither the config nor the template can be read, if
            a required key is missing or empty, if no repository is
            configured at all, if a path escapes its root, if two
            directories overlap, or if a work tree contains the config file.
    """
    source = config_path
    if not source.exists():
        if not EXAMPLE_CONFIG_PATH.exists():
            raise ConfigError(
                f"{config_path} does not exist and the bundled template "
                f"{EXAMPLE_CONFIG_PATH} is missing as well"
            )
        source = EXAMPLE_CONFIG_PATH

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

    _check_disjoint(repos, source)
    _check_config_outside_work_trees(repos, source)

    return repos
