"""
Unit tests for the git asset startup bootstrap.

Covers :mod:`admin.git.bootstrap`: cloning every configured repository on
startup and starting the periodic sync for the repositories that are
actually cloned, both on a background thread so the caller is never held
up, without crashing the service when config loading or cloning fails.
"""

import threading
from pathlib import Path

from admin.git import bootstrap
from admin.git.clone import CloneError
from admin.git.config import ConfigError
from tests.helpers.repo_factory import make_repo


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


def test_start_git_backup_returns_while_the_clone_is_still_running(monkeypatch):
    """Test a slow clone does not hold up the caller, i.e. the HTTP API."""
    clone_started = threading.Event()
    release_clone = threading.Event()
    steps = []

    def slow_clone():
        steps.append("clone")
        clone_started.set()
        release_clone.wait(timeout=30)

    monkeypatch.setattr(bootstrap, "clone_configured_repos", slow_clone)
    monkeypatch.setattr(bootstrap, "start_git_sync", steps.append)

    thread = bootstrap.start_git_backup(60)

    assert clone_started.wait(timeout=30)
    # Back in the caller while the clone is still blocked.
    assert thread.is_alive()
    assert steps == ["clone"]

    release_clone.set()
    thread.join(timeout=30)

    # The sync is only started once the clone is done.
    assert steps == ["clone", 60]


def test_start_git_backup_runs_on_a_named_daemon_thread(monkeypatch):
    """Test the thread cannot keep the process alive and is recognisable."""
    monkeypatch.setattr(bootstrap, "clone_configured_repos", lambda: {})
    monkeypatch.setattr(bootstrap, "start_git_sync", lambda interval: None)

    thread = bootstrap.start_git_backup(60)
    thread.join(timeout=30)

    assert thread.daemon
    assert thread.name == bootstrap.BOOTSTRAP_THREAD_NAME


def test_start_git_backup_logs_an_unexpected_error(monkeypatch, caplog):
    """Test an error on the background thread is logged, not lost."""

    def broken_clone():
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(bootstrap, "clone_configured_repos", broken_clone)

    with caplog.at_level("ERROR"):
        bootstrap.start_git_backup(60).join(timeout=30)

    assert "Unexpected error while starting the git backup" in caplog.text
    assert "disk on fire" in caplog.text
