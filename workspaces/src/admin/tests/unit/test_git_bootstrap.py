"""
Unit tests for the git asset startup bootstrap.

Covers :mod:`admin.git.bootstrap`: cloning every configured repository on
startup and starting the periodic sync for the repositories that are
actually cloned, both on a background thread so the caller is never held
up, without crashing the service when config loading or cloning fails.
"""

import threading
from pathlib import Path

import pytest

from admin.git import bootstrap
from admin.git.clone import CloneError
from admin.git.config import ConfigError
from admin.git.status import SyncStatus
from tests.helpers.repo_factory import make_repo
from tests.helpers.templates import EXAMPLE_CONFIG_PATH


@pytest.fixture(name="config_file", autouse=True)
def fixture_config_file(tmp_path, monkeypatch):
    """
    Point $WORKSPACE_APP_DIR at a directory holding a config.toml.

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
    """Test that without config.toml nothing is loaded or cloned."""
    config_file.unlink()
    monkeypatch.setattr(bootstrap, "load_config", _fail_if_called)
    monkeypatch.setattr(bootstrap, "clone_asset", _fail_if_called)

    with caplog.at_level("INFO"):
        assert not bootstrap.clone_configured_repos()

    assert "git backup is disabled" in caplog.text
    assert "ERROR" not in caplog.text


def test_missing_config_disables_sync(config_file, monkeypatch, caplog):
    """Test that without config.toml no sync thread is started or warned about."""
    status = SyncStatus()
    config_file.unlink()
    monkeypatch.setattr(bootstrap, "load_config", _fail_if_called)
    monkeypatch.setattr(bootstrap, "start_sync_scheduler", _fail_if_called)

    with caplog.at_level("INFO"):
        assert bootstrap.start_git_sync(status) is None

    assert "git backup is disabled" in caplog.text
    assert status.snapshot()["enabled"] is False
    assert "git sync not started" not in caplog.text


def test_unedited_template_clones_nothing(config_file, monkeypatch, caplog):
    """Test that copying config.toml.example unchanged sends nothing to a remote."""
    config_file.write_bytes(EXAMPLE_CONFIG_PATH.read_bytes())
    monkeypatch.setattr(bootstrap, "clone_asset", _fail_if_called)

    with caplog.at_level("ERROR"):
        assert not bootstrap.clone_configured_repos()

    assert "placeholder" in caplog.text


def test_clone_configured_repos_clones_every_entry(monkeypatch):
    """Test clone_configured_repos loads config and clones each entry."""
    private_repo = make_repo("private")
    common_repo = make_repo("common")

    monkeypatch.setattr(
        bootstrap, "load_config", lambda _path, _root: [private_repo, common_repo]
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
    monkeypatch.setattr(bootstrap, "load_config", lambda _path, _root: [private_repo])

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
    monkeypatch.setattr(bootstrap, "load_config", lambda _path, _root: [private_repo])
    monkeypatch.setattr(bootstrap, "clone_asset", lambda _repo: False)

    assert bootstrap.clone_configured_repos() == {"private": False}


def test_clone_configured_repos_returns_empty_when_config_load_fails(
    monkeypatch, caplog
):
    """Test a broken config file logs an error and clones nothing."""

    def fake_load_config(_path, _root):
        raise ConfigError("config.toml: no [assets] section found")

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
        bootstrap, "load_config", lambda _path, _root: [private_repo, common_repo]
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
    """Test the config file is resolved from $WORKSPACE_APP_DIR/config.toml."""
    monkeypatch.setenv("WORKSPACE_APP_DIR", "/some/app/dir")

    path = bootstrap._config_path()  # pylint: disable=protected-access

    assert path == Path("/some/app/dir") / "config.toml"


def test_root_dir_uses_persistent_dir(monkeypatch):
    """Test the repositories are placed below $PERSISTENT_DIR."""
    monkeypatch.setenv("PERSISTENT_DIR", "/some/persistent/dir")

    root = bootstrap._root_dir()  # pylint: disable=protected-access

    assert root == Path("/some/persistent/dir")


def test_load_repos_passes_the_root_to_load_config(monkeypatch):
    """Test the root from the environment reaches load_config."""
    monkeypatch.setenv("PERSISTENT_DIR", "/some/persistent/dir")
    received = {}

    def fake_load_config(_path, root):
        received["root"] = root
        return []

    monkeypatch.setattr(bootstrap, "load_config", fake_load_config)

    bootstrap.clone_configured_repos()

    assert received["root"] == Path("/some/persistent/dir")


def test_start_git_sync_schedules_only_the_cloned_repositories(monkeypatch):
    """Test repositories that have not been cloned yet are left out."""
    private_repo = make_repo("private")
    common_repo = make_repo("common")

    monkeypatch.setattr(
        bootstrap, "load_config", lambda _path, _root: [private_repo, common_repo]
    )
    monkeypatch.setattr(bootstrap, "is_cloned", lambda git_dir: git_dir == "cloned")

    scheduled = {}

    def fake_start_sync_scheduler(repos, interval_seconds, stop_event, status):
        scheduled["repos"] = repos
        scheduled["interval_seconds"] = interval_seconds
        scheduled["stop_event"] = stop_event
        scheduled["status"] = status
        return "thread"

    monkeypatch.setattr(
        bootstrap, "start_sync_scheduler", fake_start_sync_scheduler
    )

    common_repo.git_dir = "cloned"
    private_repo.git_dir = "not-cloned"

    status = SyncStatus()
    thread, stop_event = bootstrap.start_git_sync(status, 60)

    assert thread == "thread"
    assert scheduled["repos"] == [common_repo]
    assert scheduled["interval_seconds"] == 60
    # The caller gets the event back, so the loop can actually be stopped.
    assert stop_event is scheduled["stop_event"]
    assert scheduled["status"] is status


def test_start_git_sync_returns_none_when_config_load_fails(monkeypatch, caplog):
    """Test a broken config file does not stop the service from starting."""

    def fake_load_config(_path, _root):
        raise ConfigError("config.toml: no [assets] section found")

    monkeypatch.setattr(bootstrap, "load_config", fake_load_config)
    status = SyncStatus()

    with caplog.at_level("ERROR"):
        assert bootstrap.start_git_sync(status) is None

    assert "Cannot start git sync" in caplog.text
    assert status.snapshot()["enabled"] is False


def test_start_git_sync_returns_none_when_nothing_is_cloned(monkeypatch, caplog):
    """Test the sync is not started when there is nothing to sync."""
    monkeypatch.setattr(bootstrap, "load_config", lambda _path, _root: [make_repo("common")])
    monkeypatch.setattr(bootstrap, "is_cloned", lambda git_dir: False)
    status = SyncStatus()

    with caplog.at_level("WARNING"):
        assert bootstrap.start_git_sync(status) is None

    assert "git sync not started" in caplog.text
    assert status.snapshot()["enabled"] is False


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
    monkeypatch.setattr(
        bootstrap,
        "start_git_sync",
        lambda status, interval, _stop_event: steps.append((status, interval)),
    )
    status = SyncStatus()

    thread = bootstrap.start_git_backup(status, 60)

    assert clone_started.wait(timeout=30)
    # Back in the caller while the clone is still blocked.
    assert thread.is_alive()
    assert steps == ["clone"]

    release_clone.set()
    thread.join(timeout=30)

    # The sync is only started once the clone is done, with the same status.
    assert steps == ["clone", (status, 60)]


def test_start_git_backup_runs_on_a_named_daemon_thread(monkeypatch):
    """Test the thread cannot keep the process alive and is recognisable."""
    monkeypatch.setattr(bootstrap, "clone_configured_repos", lambda: {})
    monkeypatch.setattr(
        bootstrap, "start_git_sync", lambda status, interval, _stop_event: None
    )

    thread = bootstrap.start_git_backup(SyncStatus(), 60)
    thread.join(timeout=30)

    assert thread.daemon
    assert thread.name == bootstrap.BOOTSTRAP_THREAD_NAME


def test_start_git_backup_logs_an_unexpected_error(monkeypatch, caplog):
    """Test an error on the background thread is logged, not lost."""

    def broken_clone():
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(bootstrap, "clone_configured_repos", broken_clone)

    with caplog.at_level("ERROR"):
        bootstrap.start_git_backup(SyncStatus(), 60).join(timeout=30)

    assert "Unexpected error while starting the git backup" in caplog.text
    assert "disk on fire" in caplog.text


def test_template_path_is_none_without_the_variable(monkeypatch):
    """Test that outside the image no template is assumed."""
    monkeypatch.delenv("WORKSPACE_CONFIG_TEMPLATE", raising=False)

    assert bootstrap._template_path() is None  # pylint: disable=protected-access


def test_template_path_uses_the_variable(monkeypatch):
    """Test the template is taken from $WORKSPACE_CONFIG_TEMPLATE."""
    monkeypatch.setenv("WORKSPACE_CONFIG_TEMPLATE", "/some/template.toml")

    path = bootstrap._template_path()  # pylint: disable=protected-access

    assert path == Path("/some/template.toml")


def test_start_git_backup_creates_a_missing_config_and_clones_nothing(
    config_file, monkeypatch, tmp_path
):
    """Test a freshly created config, still all placeholders, is not cloned."""
    config_file.unlink()
    template = tmp_path / "template.toml"
    template.write_bytes(EXAMPLE_CONFIG_PATH.read_bytes())
    monkeypatch.setenv("WORKSPACE_CONFIG_TEMPLATE", str(template))
    monkeypatch.setattr(bootstrap, "clone_configured_repos", _fail_if_called)
    monkeypatch.setattr(bootstrap, "start_git_sync", _fail_if_called)

    bootstrap.start_git_backup(SyncStatus(), 60).join(timeout=30)

    assert config_file.read_bytes() == EXAMPLE_CONFIG_PATH.read_bytes()


def test_start_git_backup_clones_when_the_config_exists(config_file, monkeypatch):
    """Test an existing config file goes straight on to cloning."""
    assert config_file.is_file()
    steps = []
    monkeypatch.setattr(
        bootstrap, "clone_configured_repos", lambda: steps.append("clone")
    )
    monkeypatch.setattr(
        bootstrap, "start_git_sync", lambda status, interval, _stop_event: steps.append("sync")
    )

    bootstrap.start_git_backup(SyncStatus(), 60).join(timeout=30)

    assert steps == ["clone", "sync"]


def test_start_git_sync_uses_the_stop_event_it_is_given(monkeypatch):
    """Test the caller's event is the one that stops the scheduler."""
    monkeypatch.setattr(bootstrap, "load_config", lambda _path, _root: [make_repo("common")])
    monkeypatch.setattr(bootstrap, "is_cloned", lambda git_dir: True)
    scheduled = {}

    def fake_start_sync_scheduler(_repos, _interval, stop_event, _status):
        scheduled["stop_event"] = stop_event
        return "thread"

    monkeypatch.setattr(
        bootstrap, "start_sync_scheduler", fake_start_sync_scheduler
    )
    stop_event = threading.Event()

    _, returned = bootstrap.start_git_sync(SyncStatus(), 60, stop_event)

    assert scheduled["stop_event"] is stop_event
    assert returned is stop_event


def _start_a_waiting_sync(started):
    """Build a start_git_sync whose sync thread runs until it is stopped."""

    def fake_start_git_sync(_status, _interval, stop_event):
        thread = threading.Thread(target=stop_event.wait, daemon=True)
        thread.start()
        started.append(thread)
        return thread, stop_event

    return fake_start_git_sync


def test_start_git_backup_lives_as_long_as_the_sync(monkeypatch):
    """Test the returned thread is the one to wait for on shutdown."""
    started = []
    monkeypatch.setattr(bootstrap, "clone_configured_repos", lambda: {})
    monkeypatch.setattr(bootstrap, "start_git_sync", _start_a_waiting_sync(started))
    stop_event = threading.Event()

    thread = bootstrap.start_git_backup(SyncStatus(), 60, stop_event)
    thread.join(timeout=0.2)

    assert thread.is_alive()

    assert bootstrap.stop_git_backup(thread, stop_event, timeout_seconds=30)
    assert not thread.is_alive()
    assert not started[0].is_alive()


def test_start_git_backup_does_not_start_the_sync_once_stopped(monkeypatch):
    """Test a shutdown during the clone leaves the sync unstarted."""
    stop_event = threading.Event()

    def clone_during_shutdown():
        stop_event.set()
        return {}

    monkeypatch.setattr(bootstrap, "clone_configured_repos", clone_during_shutdown)
    monkeypatch.setattr(bootstrap, "start_git_sync", _fail_if_called)

    bootstrap.start_git_backup(SyncStatus(), 60, stop_event).join(timeout=30)


def test_stop_git_backup_gives_up_on_a_git_command_that_runs_on(caplog):
    """Test a shutdown is not held up for as long as a push may take."""
    release = threading.Event()
    thread = threading.Thread(target=release.wait, daemon=True)
    thread.start()
    stop_event = threading.Event()

    with caplog.at_level("WARNING"):
        assert not bootstrap.stop_git_backup(
            thread, stop_event, timeout_seconds=0.1
        )

    assert stop_event.is_set()
    assert "still running a git command" in caplog.text
    release.set()


def _schedule_into(scheduled, monkeypatch):
    """Load one cloned repository and record what the scheduler is given."""
    monkeypatch.setattr(bootstrap, "load_config", lambda _path, _root: [make_repo("common")])
    monkeypatch.setattr(bootstrap, "is_cloned", lambda git_dir: True)

    def fake_start_sync_scheduler(_repos, interval_seconds, _stop_event, _status):
        scheduled["interval_seconds"] = interval_seconds
        return "thread"

    monkeypatch.setattr(
        bootstrap, "start_sync_scheduler", fake_start_sync_scheduler
    )


def test_start_git_sync_reads_the_interval_from_the_config_file(
    config_file, monkeypatch
):
    """Test with no interval given, SYNC_INTERVAL_SECONDS decides."""
    scheduled = {}
    _schedule_into(scheduled, monkeypatch)
    read_from = []

    def fake_load_sync_interval(path):
        read_from.append(path)
        return 120

    monkeypatch.setattr(bootstrap, "load_sync_interval", fake_load_sync_interval)

    assert bootstrap.start_git_sync(SyncStatus()) is not None

    assert scheduled["interval_seconds"] == 120
    assert read_from == [config_file]


def test_start_git_sync_prefers_a_given_interval(monkeypatch):
    """Test --sync-interval overrides the config file, which is not read."""
    scheduled = {}
    _schedule_into(scheduled, monkeypatch)
    monkeypatch.setattr(bootstrap, "load_sync_interval", _fail_if_called)

    bootstrap.start_git_sync(SyncStatus(), 30)

    assert scheduled["interval_seconds"] == 30


def test_start_git_sync_is_not_started_when_the_interval_cannot_be_read(
    monkeypatch, caplog
):
    """Test a config file broken since the clone is logged, not raised."""
    scheduled = {}
    _schedule_into(scheduled, monkeypatch)

    def unreadable(_path):
        raise ConfigError("config.toml is not valid TOML: SYNC_INTERVAL_SECONDS")

    monkeypatch.setattr(bootstrap, "load_sync_interval", unreadable)
    status = SyncStatus()

    with caplog.at_level("ERROR"):
        assert bootstrap.start_git_sync(status) is None

    assert "SYNC_INTERVAL_SECONDS" in caplog.text
    assert not scheduled
    assert status.snapshot()["enabled"] is False
