"""
Unit tests for the git asset startup bootstrap.

Covers :mod:`admin.git.bootstrap`: cloning the ``common`` repository on
startup and starting the periodic sync for the repositories that are
actually cloned, without crashing the service when config loading or
cloning fails.
"""

from pathlib import Path

from admin.git import bootstrap
from admin.git.clone import CloneError
from admin.git.config import ConfigError
from tests.repo_factory import make_repo


def test_clone_common_repo_clones_the_common_entry(monkeypatch):
    """Test clone_common_repo loads config and clones the 'common' entry."""
    common_repo = make_repo("common")
    private_repo = make_repo("private")

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
    monkeypatch.setattr(bootstrap, "load_config", lambda path: [make_repo("private")])

    with caplog.at_level("ERROR"):
        assert bootstrap.clone_common_repo() is False

    assert "no [assets.common] entry" in caplog.text


def test_clone_common_repo_returns_false_when_clone_fails(monkeypatch, caplog):
    """Test clone_common_repo logs the clone failure and returns False."""
    common_repo = make_repo("common")
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


def test_start_git_sync_schedules_only_the_cloned_repositories(monkeypatch):
    """Test repositories that have not been cloned yet are left out."""
    private_repo = make_repo("private")
    common_repo = make_repo("common")

    monkeypatch.setattr(
        bootstrap, "load_config", lambda path: [private_repo, common_repo]
    )
    monkeypatch.setattr(bootstrap, "is_cloned", lambda git_dir: git_dir == "cloned")

    scheduled = {}

    def fake_start_sync_scheduler(repos, interval_seconds, _stop_event):
        scheduled["repos"] = repos
        scheduled["interval_seconds"] = interval_seconds
        return "thread"

    monkeypatch.setattr(
        bootstrap, "start_sync_scheduler", fake_start_sync_scheduler
    )

    common_repo.git_dir = "cloned"
    private_repo.git_dir = "not-cloned"

    assert bootstrap.start_git_sync(60) == "thread"
    assert scheduled["repos"] == [common_repo]
    assert scheduled["interval_seconds"] == 60


def test_start_git_sync_returns_none_when_config_load_fails(monkeypatch, caplog):
    """Test a broken config file does not stop the service from starting."""

    def fake_load_config(_path):
        raise ConfigError("config.env: no [assets] section found")

    monkeypatch.setattr(bootstrap, "load_config", fake_load_config)

    with caplog.at_level("ERROR"):
        assert bootstrap.start_git_sync() is None

    assert "Cannot start git sync" in caplog.text


def test_start_git_sync_returns_none_when_nothing_is_cloned(monkeypatch, caplog):
    """Test the sync is not started when there is nothing to sync."""
    monkeypatch.setattr(bootstrap, "load_config", lambda path: [make_repo("common")])
    monkeypatch.setattr(bootstrap, "is_cloned", lambda git_dir: False)

    with caplog.at_level("WARNING"):
        assert bootstrap.start_git_sync() is None

    assert "git sync not started" in caplog.text
