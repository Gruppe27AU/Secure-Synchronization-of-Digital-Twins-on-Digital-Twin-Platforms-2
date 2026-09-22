"""
Unit tests for the git asset startup bootstrap.

Covers :mod:`admin.git.bootstrap`: cloning the ``common`` repository on
startup, without crashing the service when config loading or cloning
fails.
"""

from pathlib import Path

from admin.git import bootstrap
from admin.git.clone import CloneError
from admin.git.config import ConfigError, RepoConfig


def _make_repo(name):
    return RepoConfig(
        name=name,
        repo_url="https://example.com/org/repo.git",
        branch="main",
        username="",
        token="",
        git_dir=None,
        work_tree=None,
    )


def test_clone_common_repo_clones_the_common_entry(monkeypatch):
    """Test clone_common_repo loads config and clones the 'common' entry."""
    common_repo = _make_repo("common")
    private_repo = _make_repo("private")

    monkeypatch.setattr(bootstrap, "load_config", lambda path: [private_repo, common_repo])

    cloned_with = {}

    def fake_clone_asset(repo):
        cloned_with["repo"] = repo
        return True

    monkeypatch.setattr(bootstrap, "clone_asset", fake_clone_asset)

    assert bootstrap.clone_common_repo() is True
    assert cloned_with["repo"] is common_repo


def test_clone_common_repo_returns_false_when_config_load_fails(monkeypatch, caplog):
    """Test clone_common_repo logs an error and returns False on a bad config file."""

    def fake_load_config(_path):
        raise ConfigError("config.env: 'GIT_REPO_URL' is missing from [assets.common]")

    monkeypatch.setattr(bootstrap, "load_config", fake_load_config)

    with caplog.at_level("ERROR"):
        assert bootstrap.clone_common_repo() is False

    assert "Cannot clone common repository" in caplog.text


def test_clone_common_repo_returns_false_when_common_entry_missing(monkeypatch, caplog):
    """Test clone_common_repo logs an error when [assets.common] is absent."""
    monkeypatch.setattr(bootstrap, "load_config", lambda path: [_make_repo("private")])

    with caplog.at_level("ERROR"):
        assert bootstrap.clone_common_repo() is False

    assert "no [assets.common] entry" in caplog.text


def test_clone_common_repo_returns_false_when_clone_fails(monkeypatch, caplog):
    """Test clone_common_repo logs the clone failure and returns False."""
    common_repo = _make_repo("common")
    monkeypatch.setattr(bootstrap, "load_config", lambda path: [common_repo])

    def fake_clone_asset(_repo):
        raise CloneError("git clone failed for repository 'common': fatal error")

    monkeypatch.setattr(bootstrap, "clone_asset", fake_clone_asset)

    with caplog.at_level("ERROR"):
        assert bootstrap.clone_common_repo() is False

    assert "git clone failed" in caplog.text


def test_config_path_uses_workspace_app_dir(monkeypatch):
    """Test the config file is resolved from $WORKSPACE_APP_DIR/config.env."""
    monkeypatch.setenv("WORKSPACE_APP_DIR", "/some/app/dir")

    path = bootstrap._config_path()  # pylint: disable=protected-access

    assert path == Path("/some/app/dir") / "config.env"
