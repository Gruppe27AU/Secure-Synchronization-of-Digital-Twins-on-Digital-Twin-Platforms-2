"""
Git backup configuration.

Reads the git backup config file and turns it into objects,
so the other modules can use the values without parsing anything.

The file is TOML despite its ``.env`` name. Every path in it is a fragment:
``GIT_DIR`` is resolved against ``WORKSPACE_DIR`` and ``GIT_WORK_TREE``
against ``WORKSPACE_APP_DIR``, so callers receive absolute paths and never
have to know which fragment belongs to which root.
"""

import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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


def _validate_branch(branch: str, where: str, source: Path) -> str:
    """
    Accept only a literal, valid branch name.

    The branch ends up on git's command line, so a value starting with ``-``
    would be read as an option (``--upload-pack=<command>`` runs a command).
    ``git check-ref-format --branch`` applies git's own naming rules. Inside
    a repository it also expands shorthands such as ``@{-1}``, so the name
    is only accepted when git hands it back unchanged.

    Args:
        branch: Value of ``GIT_REPO_BRANCH``.
        where: Human-readable location, used in the error message.
        source: File the value came from, used in the error message.

    Returns:
        The branch, unchanged.

    Raises:
        ConfigError: If the branch is not a valid branch name, or git
            cannot be run to check it.
    """
    invalid = ConfigError(
        f"{source}: 'GIT_REPO_BRANCH' in {where} is not a valid branch "
        f"name: {branch!r}"
    )
    # Never hand a dash-led value to git, not even to the checker.
    if branch.startswith("-"):
        raise invalid

    try:
        result = subprocess.run(
            ["git", "check-ref-format", "--branch", branch],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise ConfigError(
            f"{source}: could not run git to check 'GIT_REPO_BRANCH' in "
            f"{where}: {error}"
        ) from error

    if result.returncode != 0 or result.stdout.strip() != branch:
        raise invalid

    return branch


def _validate_url(url: str, where: str, source: Path) -> str:
    """
    Accept only an ``https://`` URL that carries no credentials.

    Other transports are where git options such as ``--upload-pack`` take
    effect, and ``ext::`` runs a command by design. Credentials belong in
    ``GIT_REPO_USERNAME``/``GIT_REPO_TOKEN``: a ``user:token@`` in the URL
    would be written into the clone's ``.git/config`` and into the log.

    Args:
        url: Value of ``GIT_REPO_URL``.
        where: Human-readable location, used in the error message.
        source: File the value came from, used in the error message.

    Returns:
        The URL, unchanged.

    Raises:
        ConfigError: If the URL is not https, has no host, contains
            whitespace or control characters, or contains a username or
            token.
    """
    # The URL is deliberately left out of these messages: it may hold a token.
    if not url.startswith("https://") or any(
        char.isspace() or not char.isprintable() for char in url
    ):
        raise ConfigError(
            f"{source}: 'GIT_REPO_URL' in {where} must be an https:// URL "
            "without spaces or control characters"
        )

    parts = urlsplit(url)
    if "@" in parts.netloc:
        raise ConfigError(
            f"{source}: 'GIT_REPO_URL' in {where} must not contain a username "
            "or token; set GIT_REPO_USERNAME and GIT_REPO_TOKEN instead"
        )
    if not parts.hostname:
        raise ConfigError(f"{source}: 'GIT_REPO_URL' in {where} has no host")

    return url


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
        ConfigError: If any required key is missing or empty, or the URL
            or branch is unsafe to hand to git.
    """
    where = f"[assets.{name}]"

    return RepoConfig(
        name=name,
        repo_url=_validate_url(
            _require(asset, "GIT_REPO_URL", where, source), where, source
        ),
        branch=_validate_branch(
            _require(asset, "GIT_REPO_BRANCH", where, source), where, source
        ),
        username=_require(asset, "GIT_REPO_USERNAME", where, source),
        token=_require(asset, "GIT_REPO_TOKEN", where, source),
        git_dir=workspace_dir / _require(asset, "GIT_DIR", where, source),
        work_tree=app_dir / _require(asset, "GIT_WORK_TREE", where, source),
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
            a required key is missing or empty, if a URL or branch is
            unsafe to hand to git, or if no repository is configured at all.
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

    return repos
