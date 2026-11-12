"""
Checking the values of the git backup configuration.

Each check raises :class:`admin.git.setup.config.ConfigError` naming the file, the
section and the key, or logs a warning for something that has no effect.
URLs are left out of the messages: they may hold a token.
"""

import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from admin.git.setup.config import (
    DEFAULT_MODES,
    MAX_SYNC_INTERVAL_SECONDS,
    MIN_SYNC_INTERVAL_SECONDS,
    MODE_KEY,
    PLACEHOLDER_VALUES,
    ConfigError,
    PlaceholderError,
    RepoConfig,
    SyncMode,
)
from admin.git.run.runner import LOCAL_TIMEOUT_SECONDS, GitTimeoutError, run_git

logger = logging.getLogger(__name__)

#: Permission bits letting anyone but the owner read or change a file.
SHARED_PERMISSION_BITS = 0o077


def require(values: dict[str, Any], key: str, where: str, source: Path) -> str:
    """
    Return ``values[key]``, a non-empty string.

    Raises:
        ConfigError: If it is missing, not a string, or blank.
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


def validate_branch(branch: str, where: str, source: Path) -> str:
    """
    Accept only a literal branch name, as ``git check-ref-format`` returns it.

    A dash-led value would be read as a git option, and a shorthand such as
    ``@{-1}`` would be expanded.

    Raises:
        ConfigError: If the name is invalid, or git cannot be run.
    """
    invalid = ConfigError(
        f"{source}: 'GIT_REPO_BRANCH' in {where} is not a valid branch "
        f"name: {branch!r}"
    )
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


def validate_url(url: str, where: str, source: Path) -> str:
    """
    Accept only an ``https://`` URL with a host and no credentials.

    Other transports can run commands; a token in the URL would be written
    to ``.git/config`` and the log.

    Raises:
        ConfigError: If the URL is not such a URL.
    """
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


def mode(name: str, asset: dict[str, Any], where: str, source: Path) -> SyncMode:
    """
    Return the section's ``MODE``, or the default for ``name``.

    Raises:
        ConfigError: If ``MODE`` is neither ``pull`` nor ``sync``.
    """
    if MODE_KEY not in asset:
        return DEFAULT_MODES.get(name, SyncMode.SYNC)
    value = asset[MODE_KEY]
    if value not in tuple(SyncMode):
        raise ConfigError(
            f"{source}: '{MODE_KEY}' in {where} must be "
            + " or ".join(f'"{each}"' for each in SyncMode)
            + f", got {value!r}"
        )
    return SyncMode(value)


def token(asset: dict[str, Any], sync_mode: SyncMode, where: str, source: Path) -> str:
    """
    Return ``GIT_REPO_TOKEN``. A ``pull`` repository may leave it out or
    empty, for a public remote; ``sync`` needs it to push.

    Raises:
        ConfigError: If a ``sync`` repository has none, or it is not a string.
    """
    if sync_mode is SyncMode.PULL and asset.get("GIT_REPO_TOKEN", "") == "":
        return ""
    return require(asset, "GIT_REPO_TOKEN", where, source)


def reject_placeholders(asset: dict[str, Any], where: str, source: Path) -> None:
    """
    Refuse a template value never filled in. Call after :func:`require`.

    Raises:
        PlaceholderError: If a value is a placeholder.
    """
    for key, placeholders in PLACEHOLDER_VALUES.items():
        if asset.get(key) in placeholders:
            raise PlaceholderError(
                f"{source}: '{key}' in {where} is still the placeholder "
                f"from config.toml.example; fill in your own value"
            )


def warn_unknown_keys(
    values: dict[str, Any], known: frozenset[str], where: str, source: Path
) -> None:
    """Warn about every key in ``values`` that is not in ``known``."""
    for key in values:
        if key not in known:
            logger.warning(
                "%s: '%s' in %s is not a known setting and has no effect; "
                "expected only %s",
                source, key, where, ", ".join(sorted(known)),
            )


def warn_shared_permissions(source: Path) -> None:
    """Warn if others may read or change the file; it holds the tokens."""
    if os.name != "posix":
        return
    try:
        file_mode = source.stat().st_mode
    except OSError:
        return
    if file_mode & SHARED_PERMISSION_BITS:
        logger.warning(
            "%s holds tokens but can be read or changed by other users "
            "(mode %o); restrict it with: chmod 600 %s",
            source, file_mode & 0o777, source,
        )


def normalize_url(url: str) -> str:
    """
    Return ``url`` with scheme and host lowercased, the default port ``:443``
    and a trailing ``/`` or ``.git`` removed, so two spellings of one remote
    compare equal.
    """
    parts = urlsplit(url)
    host = parts.netloc.lower().removesuffix(":443")
    path = parts.path.rstrip("/").removesuffix(".git").rstrip("/")
    return f"{parts.scheme.lower()}://{host}{path}"


def check_distinct_remotes(repos: list[RepoConfig], source: Path) -> None:
    """
    Refuse two repositories on the same remote branch.

    Raises:
        ConfigError: If two share a URL and branch.
    """
    seen: dict[tuple[str, str], str] = {}
    for repo in repos:
        remote = (normalize_url(repo.repo_url), repo.branch)
        if remote in seen:
            raise ConfigError(
                f"{source}: [assets.{seen[remote]}] and [assets.{repo.name}] "
                f"both use {repo.repo_url} (branch {repo.branch}); each "
                f"needs a remote of its own"
            )
        seen[remote] = repo.name


def check_outside_work_trees(repos: list[RepoConfig], source: Path) -> None:
    """
    Refuse a work tree containing the config file: its tokens would be pushed.

    Raises:
        ConfigError: If one does.
    """
    config_file = source.resolve()
    for repo in repos:
        if config_file.is_relative_to(repo.work_tree.resolve()):
            raise ConfigError(
                f"{source}: the work tree of [assets.{repo.name}] "
                f"({repo.work_tree}) contains the config file, which would "
                "commit and push its tokens"
            )


def sync_interval_problem(seconds: object) -> str | None:
    """Return why ``seconds`` is no valid interval, or None if it is."""
    # bool is an int, and TOML's true would otherwise mean 1.
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        return f"must be a whole number of seconds, got {type(seconds).__name__}"
    if not MIN_SYNC_INTERVAL_SECONDS <= seconds <= MAX_SYNC_INTERVAL_SECONDS:
        return (
            f"must be between {MIN_SYNC_INTERVAL_SECONDS} and "
            f"{MAX_SYNC_INTERVAL_SECONDS} seconds, got {seconds}"
        )
    return None
