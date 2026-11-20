"""
Unit tests for the types and settings of the git backup configuration.

Covers :mod:`admin.config.config`: the facts the rest of the package relies
on, so that changing one setting cannot quietly break another.
"""

import re
from pathlib import Path

import pytest

from admin.config.config import (
    ASSET_NAMES,
    DEFAULT_MODES,
    DEFAULT_SYNC_INTERVAL_SECONDS,
    KNOWN_ASSET_KEYS,
    MAX_SYNC_INTERVAL_SECONDS,
    MIN_SYNC_INTERVAL_SECONDS,
    MODE_KEY,
    PLACEHOLDER_VALUES,
    REQUIRED_ASSET_KEYS,
    ConfigError,
    PlaceholderError,
    RepoConfig,
)
from tests.helpers.templates import EXAMPLE_CONFIG_PATH

TOKEN = "secret-token-value"

#: A ``KEY = "value"`` line of the template, commented out or not.
TEMPLATE_VALUE = re.compile(r'^[#\s]*(GIT_REPO_URL|GIT_REPO_TOKEN)\s*=\s*"([^"]*)"')


@pytest.mark.parametrize("show", [repr, str], ids=["repr", "str"])
def test_a_repository_never_shows_its_token(show):
    """Test the token stays out of every log line that formats a repository."""
    repo = RepoConfig(
        name="private",
        repo_url="https://gitlab.com/group/repo.git",
        branch="main",
        token=TOKEN,
        git_dir=Path("git"),
        work_tree=Path("tree"),
    )

    assert TOKEN not in show(repo)
    assert "private" in show(repo)


def test_a_placeholder_is_a_config_error():
    """Test bootstrap can catch a placeholder first and other errors after."""
    assert issubclass(PlaceholderError, ConfigError)


def test_every_section_has_a_default_mode():
    """Test a new section cannot fall back to pushing without a decision."""
    assert set(DEFAULT_MODES) == set(ASSET_NAMES)


def test_the_default_interval_is_within_its_bounds():
    """Test a config without SYNC_INTERVAL_SECONDS is valid."""
    assert (
        MIN_SYNC_INTERVAL_SECONDS
        <= DEFAULT_SYNC_INTERVAL_SECONDS
        <= MAX_SYNC_INTERVAL_SECONDS
    )


def test_known_keys_are_the_required_keys_and_mode():
    """Test a required key is never warned about as having no effect."""
    assert KNOWN_ASSET_KEYS == {*REQUIRED_ASSET_KEYS, MODE_KEY}


def test_every_template_value_is_a_placeholder():
    """Test no URL or token of the template, commented out or not, is accepted."""
    lines = EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8").splitlines()
    values = [match.groups() for line in lines if (match := TEMPLATE_VALUE.match(line))]

    assert {key for key, _ in values} == set(PLACEHOLDER_VALUES)
    assert [(k, v) for k, v in values if v not in PLACEHOLDER_VALUES[k]] == []
