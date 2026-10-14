"""
Git backup configuration.

Reads the git backup config file and turns it into objects,
so the other modules can use the values without parsing anything.

Each repository's paths follow from its section name: the git directory is
``WORKSPACE_DIR/<name>`` and the work tree ``WORKSPACE_APP_DIR/assets/<name>``.
The resulting directories must not overlap each other, and no work tree may
contain the config file, because every file in a work tree is committed and
pushed, and the config file holds the tokens.
"""

import itertools
import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from admin.git.runner import LOCAL_TIMEOUT_SECONDS, GitTimeoutError, run_git

logger = logging.getLogger(__name__)

#: Keys that must be present above any ``[assets]`` section.
REQUIRED_TOP_LEVEL_KEYS = ("HOME_DIR", "WORKSPACE_DIR", "WORKSPACE_APP_DIR")

#: Keys that must be present in every ``[assets.*]`` section.
REQUIRED_ASSET_KEYS = (
    "GIT_REPO_URL",
    "GIT_REPO_BRANCH",
    "GIT_REPO_TOKEN",
)

#: Values copied from ``config.toml.example`` or the README without being
#: filled in. A repository still carrying one of them is refused, so an
#: unedited copy of the template can never send a request to a remote.
PLACEHOLDER_VALUES = {
    "GIT_REPO_URL": frozenset({
        "https://gitlab.com/username/repository.git",
        "https://gitlab.com/username/private-repo.git",
        "https://gitlab.com/username/common-repo.git",
    }),
    "GIT_REPO_TOKEN": frozenset({"gitlab-api-token", "gitlab-access-token"}),
}

#: Sections recognised under ``[assets]``. Each one is optional on its own,
#: but at least one of them has to be configured. A name becomes part of
#: the repository's paths, so only fixed names are accepted.
ASSET_NAMES = ("private", "common")

#: Directory below ``WORKSPACE_APP_DIR`` that holds every work tree.
WORK_TREE_PARENT = "assets"

#: Optional top-level key holding the seconds between two backups.
SYNC_INTERVAL_KEY = "SYNC_INTERVAL_SECONDS"

#: Seconds between two backups when ``SYNC_INTERVAL_SECONDS`` is not set.
DEFAULT_SYNC_INTERVAL_SECONDS = 300

#: Shortest and longest interval accepted. Below one second the backup
#: would never wait; above a day it would hardly be a backup, and far
#: above it the wait itself fails (``threading.TIMEOUT_MAX`` is about
#: 49 days on Windows), which would end the sync thread.
MIN_SYNC_INTERVAL_SECONDS = 1
MAX_SYNC_INTERVAL_SECONDS = 86400

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
        result = run_git(
            ["git", "check-ref-format", "--branch", branch], LOCAL_TIMEOUT_SECONDS
        )
    except (OSError, GitTimeoutError) as error:
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
    effect, and ``ext::`` runs a command by design. The token belongs in
    ``GIT_REPO_TOKEN``: a ``user:token@`` in the URL would be written into
    the clone's ``.git/config`` and into the log.

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
            "or token; set GIT_REPO_TOKEN instead"
        )
    if not parts.hostname:
        raise ConfigError(f"{source}: 'GIT_REPO_URL' in {where} has no host")

    return url


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
        paths.append((f"git directory of [assets.{repo.name}]", repo.git_dir))
        paths.append((f"work tree of [assets.{repo.name}]", repo.work_tree))

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
                f"{source}: the work tree of [assets.{repo.name}] "
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
        workspace_dir: Directory that holds the git directory.
        app_dir: Directory whose ``assets`` subdirectory holds the work tree.
        source: File the section came from, used in error messages.

    Returns:
        The repository configuration, with both paths resolved.

    Raises:
        ConfigError: If any required key is missing or empty, if a value
            still holds a placeholder from the template, if the URL or
            branch is unsafe to hand to git.
    """
    where = f"[assets.{name}]"

    repo = RepoConfig(
        name=name,
        repo_url=_validate_url(
            _require(asset, "GIT_REPO_URL", where, source), where, source
        ),
        branch=_validate_branch(
            _require(asset, "GIT_REPO_BRANCH", where, source), where, source
        ),
        token=_require(asset, "GIT_REPO_TOKEN", where, source),
        git_dir=workspace_dir / name,
        work_tree=app_dir / WORK_TREE_PARENT / name,
    )

    # Checked only now, once _require() has proven every value a string.
    for key, placeholders in PLACEHOLDER_VALUES.items():
        if asset[key] in placeholders:
            raise ConfigError(
                f"{source}: '{key}' in {where} is still the placeholder "
                f"from config.toml.example; fill in your own value"
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


def sync_interval_problem(seconds: object) -> str | None:
    """
    Explain what is wrong with a sync interval, if anything.

    Shared by the config file and the ``--sync-interval`` option, so both
    accept exactly the same values.

    Args:
        seconds: The value given.

    Returns:
        Why the value cannot be used, or None when it can.
    """
    # bool is a subclass of int, and TOML's true would otherwise mean 1.
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        return f"must be a whole number of seconds, got {type(seconds).__name__}"
    if not MIN_SYNC_INTERVAL_SECONDS <= seconds <= MAX_SYNC_INTERVAL_SECONDS:
        return (
            f"must be between {MIN_SYNC_INTERVAL_SECONDS} and "
            f"{MAX_SYNC_INTERVAL_SECONDS} seconds, got {seconds}"
        )
    return None


def _sync_interval(config: dict[str, Any], source: Path) -> int:
    """
    Read ``SYNC_INTERVAL_SECONDS`` from a parsed config file.

    A bad value never stops the backup: it is reported as a warning and
    :data:`DEFAULT_SYNC_INTERVAL_SECONDS` is used instead.

    Args:
        config: The parsed file.
        source: File it came from, used in the warnings.

    Returns:
        The interval in seconds, or :data:`DEFAULT_SYNC_INTERVAL_SECONDS`
        when the key is not set or its value cannot be used.
    """
    fallback = f"using the default of {DEFAULT_SYNC_INTERVAL_SECONDS} seconds"

    # Appended at the end of the file, the key lands in the last [assets]
    # section, where it would otherwise be ignored without a word.
    assets = config.get("assets")
    if isinstance(assets, dict):
        for name, asset in assets.items():
            if isinstance(asset, dict) and SYNC_INTERVAL_KEY in asset:
                logger.warning(
                    "%s: '%s' is in [assets.%s], where it has no effect; move "
                    "it to the top level, above every [assets] section",
                    source, SYNC_INTERVAL_KEY, name,
                )

    if SYNC_INTERVAL_KEY not in config:
        return DEFAULT_SYNC_INTERVAL_SECONDS

    seconds = config[SYNC_INTERVAL_KEY]
    problem = sync_interval_problem(seconds)
    if problem:
        logger.warning(
            "%s: '%s' %s; %s", source, SYNC_INTERVAL_KEY, problem, fallback
        )
        return DEFAULT_SYNC_INTERVAL_SECONDS
    return seconds


def load_sync_interval(config_path: Path) -> int:
    """
    Load the seconds between two backups from the config file.

    Args:
        config_path: The user's config file, normally
            ``$WORKSPACE_APP_DIR/config.toml``.

    Returns:
        ``SYNC_INTERVAL_SECONDS``, or :data:`DEFAULT_SYNC_INTERVAL_SECONDS`
        when the file does not set it or sets it to something unusable,
        which is logged as a warning.

    Raises:
        ConfigError: If the file cannot be read or is not valid TOML.
    """
    return _sync_interval(_read_toml(config_path), config_path)


def load_config(config_path: Path) -> list[RepoConfig]:
    """
    Load the git backup configuration.

    There is no fallback: a missing file is an error here, and it is up to
    the caller to treat an absent config as "git backup disabled" (see
    :mod:`admin.git.bootstrap`).

    Args:
        config_path: The user's config file, normally
            ``$WORKSPACE_APP_DIR/config.toml``.

    Returns:
        One entry per configured repository, in ``ASSET_NAMES`` order, with
        ``git_dir`` and ``work_tree`` resolved to absolute paths.

    Raises:
        ConfigError: If the file cannot be read, if a required key is
            missing, empty or a placeholder, if a URL or branch is unsafe
            to hand to git, if no repository is configured at all, if two
            repositories share a remote branch, if two directories overlap,
            or if a work tree contains the config file.
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
    _check_disjoint(repos, source)
    _check_config_outside_work_trees(repos, source)

    return repos
