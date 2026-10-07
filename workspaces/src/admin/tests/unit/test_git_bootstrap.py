"""
Unit tests for the git asset startup bootstrap.

Covers :mod:`admin.git.bootstrap`: cloning every configured repository on
startup and starting the periodic sync for the repositories that are
actually cloned, without crashing the service when config loading or
cloning fails.
"""

from pathlib import Path

import pytest

from admin.git import bootstrap
from admin.git import config as config_module
from admin.git.clone import CloneError
from admin.git.config import ConfigError
from tests.helpers.repo_factory import make_repo


@pytest.fixture(autouse=True)
def config_file(tmp_path, monkeypatch):
    """
    Point $WORKSPACE_APP_DIR at a directory holding a config.env.

    The file's contents do not matter, since the tests replace
    ``load_config``; it only has to exist for the bootstrap to get that far.
    """
    config_path = tmp_path / bootstrap.DEFAULT_CONFIG_FILE
    config_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_APP_DIR", str(tmp_path))
    return config_path


def _fail_if_called(*_args, **_kwargs):
    """Stand in for a function the disabled git backup must never reach."""
    raise AssertionError("must not be called when git backup is disabled")


def test_missing_config_disables_cloning(config_file, monkeypatch, caplog):
    """Test that without config.env nothing is loaded or cloned."""
    config_file.unlink()
    monkeypatch.setattr(bootstrap, "load_config", _fail_if_called)
    monkeypatch.setattr(bootstrap, "clone_asset", _fail_if_called)

    with caplog.at_level("INFO"):
        assert not bootstrap.clone_configured_repos()

    assert "git backup is disabled" in caplog.text
    assert "ERROR" not in caplog.text


def test_missing_config_disables_sync(config_file, monkeypatch, caplog):
    """Test that without config.env no sync thread is started or warned about."""
    config_file.unlink()
    monkeypatch.setattr(bootstrap, "load_config", _fail_if_called)
    monkeypatch.setattr(bootstrap, "start_sync_scheduler", _fail_if_called)

    with caplog.at_level("INFO"):
        assert bootstrap.start_git_sync() is None

    assert "git backup is disabled" in caplog.text
    assert "git sync not started" not in caplog.text


def test_unedited_template_clones_nothing(config_file, monkeypatch, caplog):
    """Test that copying config.env.example unchanged sends nothing to a remote."""
    config_file.write_bytes(config_module.EXAMPLE_CONFIG_PATH.read_bytes())
    monkeypatch.setattr(bootstrap, "clone_asset", _fail_if_called)

    with caplog.at_level("ERROR"):
        assert not bootstrap.clone_configured_repos()

    assert "placeholder" in caplog.text


def test_clone_configured_repos_clones_every_entry(monkeypatch):
    """Test clone_configured_repos loads config and clones each entry."""
    private_repo = make_repo("private")
    common_repo = make_repo("common")

    monkeypatch.setattr(
        bootstrap, "load_config", lambda path: [private_repo, common_repo]
    )

    cloned_with = []

    def fake_clone_asset(repo):
        cloned_with.append(repo)
        return True

    monkeypatch.setattr(bootstrap, "clone_asset", fake_clone_asset)

    assert bootstrap.clone_configured_repos() == {"private": True, "common": True}
    assert cloned_with == [private_repo, common_repo]


def test_clone_configured_repos_clones_private_only(monkeypatch):
    """Test a config with only [assets.private] clones just that repository."""
    private_repo = make_repo("private")
    monkeypatch.setattr(bootstrap, "load_config", lambda path: [private_repo])

    cloned_with = {}

    def fake_clone_asset(repo):
        cloned_with["repo"] = repo
        return True

    monkeypatch.setattr(bootstrap, "clone_asset", fake_clone_asset)

    assert bootstrap.clone_configured_repos() == {"private": True}
    assert cloned_with["repo"] is private_repo


def test_clone_configured_repos_skips_already_cloned_repos(monkeypatch):
    """Test a repository clone_asset reports as skipped is reflected as False."""
    private_repo = make_repo("private")
    monkeypatch.setattr(bootstrap, "load_config", lambda path: [private_repo])
    monkeypatch.setattr(bootstrap, "clone_asset", lambda _repo: False)

    assert bootstrap.clone_configured_repos() == {"private": False}


def test_clone_configured_repos_returns_empty_when_config_load_fails(
    monkeypatch, caplog
):
    """Test a broken config file logs an error and clones nothing."""

    def fake_load_config(_path):
        raise ConfigError("config.env: no [assets] section found")

    monkeypatch.setattr(bootstrap, "load_config", fake_load_config)

    with caplog.at_level("ERROR"):
        assert not bootstrap.clone_configured_repos()

    assert "Cannot clone git assets" in caplog.text


def test_clone_configured_repos_one_failure_does_not_block_the_other(
    monkeypatch, caplog
):
    """Test a failing private clone is logged and skipped, common still clones."""
    private_repo = make_repo("private")
    common_repo = make_repo("common")
    monkeypatch.setattr(
        bootstrap, "load_config", lambda path: [private_repo, common_repo]
    )

    def fake_clone_asset(repo):
        if repo.name == "private":
            raise CloneError("git clone failed for repository 'private': denied")
        return True

    monkeypatch.setattr(bootstrap, "clone_asset", fake_clone_asset)

    with caplog.at_level("ERROR"):
        result = bootstrap.clone_configured_repos()

    assert result == {"private": False, "common": True}
    assert "git clone failed for repository 'private'" in caplog.text


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

    def fake_start_sync_scheduler(repos, interval_seconds, stop_event):
        scheduled["repos"] = repos
        scheduled["interval_seconds"] = interval_seconds
        scheduled["stop_event"] = stop_event
        return "thread"

    monkeypatch.setattr(
        bootstrap, "start_sync_scheduler", fake_start_sync_scheduler
    )

    common_repo.git_dir = "cloned"
    private_repo.git_dir = "not-cloned"

    thread, stop_event = bootstrap.start_git_sync(60)

    assert thread == "thread"
    assert scheduled["repos"] == [common_repo]
    assert scheduled["interval_seconds"] == 60
    # The caller gets the event back, so the loop can actually be stopped.
    assert stop_event is scheduled["stop_event"]


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
