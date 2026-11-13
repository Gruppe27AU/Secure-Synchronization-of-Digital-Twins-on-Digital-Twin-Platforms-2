"""
Unit tests for finding the config file.

Covers :mod:`admin.git.setup.locate`: ``$WORKSPACE_ADMIN_CONFIG`` first, then
``~/.config/workspace-admin/config.toml``.
"""

import pytest

from admin.git.setup.locate import (
    CONFIG_ENV_VAR,
    ConfigLocation,
    ConfigRule,
    discover_config,
)


@pytest.fixture(name="home", autouse=True)
def fixture_home(tmp_path, monkeypatch):
    """Make a fresh directory the home directory, on POSIX and Windows."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    return home


@pytest.fixture(name="default_config")
def fixture_default_config(home):
    """Create the config file in the default directory."""
    path = home / ".config" / "workspace-admin" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    return path


@pytest.fixture(name="env_config")
def fixture_env_config(tmp_path):
    """Create a config file to name in $WORKSPACE_ADMIN_CONFIG."""
    path = tmp_path / "elsewhere" / "config.toml"
    path.parent.mkdir()
    path.write_text("", encoding="utf-8")
    return path


def test_the_variable_names_the_config(env_config, monkeypatch):
    """Test $WORKSPACE_ADMIN_CONFIG pointing at an existing file is used."""
    monkeypatch.setenv(CONFIG_ENV_VAR, str(env_config))

    assert discover_config() == ConfigLocation(env_config, ConfigRule.ENV)


def test_without_the_variable_the_default_directory_is_used(home):
    """Test ~/.config/workspace-admin/config.toml is used when the variable is unset."""
    expected = home / ".config" / "workspace-admin" / "config.toml"

    assert discover_config() == ConfigLocation(expected, ConfigRule.DEFAULT)


def test_the_variable_wins_over_the_default_directory(
    env_config, default_config, monkeypatch
):
    """Test $WORKSPACE_ADMIN_CONFIG wins when both files exist."""
    assert default_config.is_file()
    monkeypatch.setenv(CONFIG_ENV_VAR, str(env_config))

    assert discover_config() == ConfigLocation(env_config, ConfigRule.ENV)


def test_an_empty_variable_counts_as_unset(default_config, monkeypatch):
    """Test an empty $WORKSPACE_ADMIN_CONFIG falls through to the default directory."""
    monkeypatch.setenv(CONFIG_ENV_VAR, "")

    assert discover_config() == ConfigLocation(default_config, ConfigRule.DEFAULT)


def test_a_relative_variable_is_resolved_against_the_current_directory(
    tmp_path, monkeypatch
):
    """Test a relative $WORKSPACE_ADMIN_CONFIG becomes an absolute path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(CONFIG_ENV_VAR, "relative/config.toml")

    location = discover_config()

    assert location.path == tmp_path / "relative" / "config.toml"
    assert location.path.is_absolute()
    assert location.rule is ConfigRule.ENV


def test_a_relative_variable_is_resolved_when_discovered(tmp_path, monkeypatch):
    """Test changing directory afterwards does not move the chosen file."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(CONFIG_ENV_VAR, "config.toml")
    location = discover_config()

    monkeypatch.chdir(tmp_path / "..")

    assert location.path == tmp_path / "config.toml"


def test_the_variable_is_used_even_when_its_file_is_missing(
    tmp_path, default_config, monkeypatch
):
    """Test a missing file named by the variable is not passed over for the default."""
    assert default_config.is_file()
    missing = tmp_path / "missing.toml"
    monkeypatch.setenv(CONFIG_ENV_VAR, str(missing))

    assert discover_config() == ConfigLocation(missing, ConfigRule.ENV)
