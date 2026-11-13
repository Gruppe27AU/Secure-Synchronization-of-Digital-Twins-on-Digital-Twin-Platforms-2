"""
Unit tests for the git asset startup bootstrap.

Covers :mod:`admin.git.bootstrap`: cloning every configured repository on
startup and starting the periodic sync for the repositories that are
actually cloned, both on a background thread so the caller is never held
up, without crashing the service when config loading or cloning fails.
"""

import os
import threading
from pathlib import Path

import pytest

from admin.git import bootstrap
from admin.git.clone import CloneError
from admin.git.config import ConfigError
from admin.git.errors import SyncErrorKind
from admin.git.locate import CONFIG_ENV_VAR
from admin.git.status import SyncStatus
from tests.helpers.repo_factory import make_repo
from tests.helpers.templates import EXAMPLE_CONFIG_PATH


@pytest.fixture(name="persistent_dir", autouse=True)
def fixture_persistent_dir(tmp_path, monkeypatch):
    """Point $PERSISTENT_DIR at a directory of its own, apart from the config."""
    persistent_dir = tmp_path / "persistent"
    persistent_dir.mkdir()
    monkeypatch.setenv("PERSISTENT_DIR", str(persistent_dir))
    return persistent_dir


@pytest.fixture(name="config_file", autouse=True)
def fixture_config_file(tmp_path, monkeypatch):
    """
    Point $WORKSPACE_ADMIN_CONFIG at a config.toml outside $PERSISTENT_DIR.

    The file's contents do not matter, since the tests replace
    ``load_config``; it only has to exist for the bootstrap to get that far.
    """
    config_path = tmp_path / "config" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("", encoding="utf-8")
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    return config_path


@pytest.fixture(name="nothing_cloned", autouse=True)
def fixture_nothing_cloned(monkeypatch):
    """Treat every repository as not cloned yet, unless a test says otherwise."""
    monkeypatch.setattr(bootstrap, "is_cloned", lambda _git_dir: False)


def _fail_if_called(*_args, **_kwargs):
    """Stand in for a function the disabled git backup must never reach."""
    raise AssertionError("must not be called when git backup is disabled")


def test_missing_config_disables_cloning(config_file, monkeypatch, caplog):
    """Test that without config.toml nothing is loaded or cloned."""
    config_file.unlink()
    monkeypatch.setattr(bootstrap, "load_config", _fail_if_called)
    monkeypatch.setattr(bootstrap, "clone_asset", _fail_if_called)

    with caplog.at_level("INFO"):
        assert not bootstrap.clone_configured_repos(SyncStatus(), config_file)

    assert "git backup is disabled" in caplog.text
    assert "ERROR" not in caplog.text


def test_missing_config_disables_sync(config_file, monkeypatch, caplog):
    """Test that without config.toml no sync thread is started or warned about."""
    status = SyncStatus()
    config_file.unlink()
    monkeypatch.setattr(bootstrap, "load_config", _fail_if_called)
    monkeypatch.setattr(bootstrap, "start_sync_scheduler", _fail_if_called)

    with caplog.at_level("INFO"):
        assert bootstrap.start_git_sync(status, config_file) is None

    assert "git backup is disabled" in caplog.text
    assert status.snapshot()["enabled"] is False
    assert "git sync not started" not in caplog.text


def test_unedited_template_clones_nothing(config_file, monkeypatch, caplog):
    """Test that copying config.toml.example unchanged sends nothing to a remote."""
    config_file.write_bytes(EXAMPLE_CONFIG_PATH.read_bytes())
    monkeypatch.setattr(bootstrap, "clone_asset", _fail_if_called)

    with caplog.at_level("ERROR"):
        assert not bootstrap.clone_configured_repos(SyncStatus(), config_file)

    assert "placeholder" in caplog.text


def test_clone_configured_repos_clones_every_entry(config_file, monkeypatch):
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

    assert bootstrap.clone_configured_repos(SyncStatus(), config_file) == {
        "private": True,
        "common": True,
    }
    assert cloned_with == [private_repo, common_repo]


def test_clone_configured_repos_clones_private_only(config_file, monkeypatch):
    """Test a config with only [assets.private] clones just that repository."""
    private_repo = make_repo("private")
    monkeypatch.setattr(bootstrap, "load_config", lambda _path, _root: [private_repo])

    cloned_with = {}

    def fake_clone_asset(repo):
        cloned_with["repo"] = repo
        return True

    monkeypatch.setattr(bootstrap, "clone_asset", fake_clone_asset)

    assert bootstrap.clone_configured_repos(SyncStatus(), config_file) == {"private": True}
    assert cloned_with["repo"] is private_repo


def test_clone_configured_repos_skips_already_cloned_repos(config_file, monkeypatch):
    """Test a repository clone_asset reports as skipped is reflected as False."""
    private_repo = make_repo("private")
    monkeypatch.setattr(bootstrap, "load_config", lambda _path, _root: [private_repo])
    monkeypatch.setattr(bootstrap, "clone_asset", lambda _repo: False)

    assert bootstrap.clone_configured_repos(SyncStatus(), config_file) == {"private": False}


def test_clone_configured_repos_returns_empty_when_config_load_fails(
    config_file, monkeypatch, caplog
):
    """Test a broken config file logs an error and clones nothing."""

    def fake_load_config(_path, _root):
        raise ConfigError("config.toml: no [assets] section found")

    monkeypatch.setattr(bootstrap, "load_config", fake_load_config)

    with caplog.at_level("ERROR"):
        assert not bootstrap.clone_configured_repos(SyncStatus(), config_file)

    assert "Cannot clone git assets" in caplog.text


def test_clone_configured_repos_one_failure_does_not_block_the_other(
    config_file, monkeypatch, caplog
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
        result = bootstrap.clone_configured_repos(SyncStatus(), config_file)

    assert result == {"private": False, "common": True}
    assert "git clone failed for repository 'private'" in caplog.text


def test_root_dir_uses_persistent_dir(monkeypatch):
    """Test the repositories are placed below $PERSISTENT_DIR."""
    monkeypatch.setenv("PERSISTENT_DIR", "/some/persistent/dir")

    root = bootstrap._root_dir()  # pylint: disable=protected-access

    assert root == Path("/some/persistent/dir")


def test_root_dir_defaults_to_home(monkeypatch):
    """Test the repositories are placed below the home directory by default."""
    monkeypatch.delenv("PERSISTENT_DIR", raising=False)

    root = bootstrap._root_dir()  # pylint: disable=protected-access

    assert root == Path.home()


def test_load_repos_keeps_the_root_on_persistent_dir(
    config_file, persistent_dir, monkeypatch
):
    """Test a config file outside $PERSISTENT_DIR leaves the repositories there."""
    assert not config_file.is_relative_to(persistent_dir)
    received = {}

    def fake_load_config(path, root):
        received["path"] = path
        received["root"] = root
        return []

    monkeypatch.setattr(bootstrap, "load_config", fake_load_config)

    bootstrap.clone_configured_repos(SyncStatus(), config_file)

    assert received == {"path": config_file, "root": persistent_dir}


#: A config the real ``load_config`` accepts: no placeholder values left.
REAL_CONFIG = """\
[assets.private]
GIT_REPO_URL = "https://gitlab.example.org/team/private.git"
GIT_REPO_BRANCH = "main"
GIT_REPO_TOKEN = "a-real-looking-token"
"""


def _cloned_repo(config_file, monkeypatch):
    """Clone from ``config_file`` with the real ``load_config``; return the repo seen."""
    config_file.write_text(REAL_CONFIG, encoding="utf-8")
    cloned = []
    monkeypatch.setattr(bootstrap, "clone_asset", lambda repo: cloned.append(repo) or True)

    bootstrap.clone_configured_repos(SyncStatus(), config_file)

    assert len(cloned) == 1
    return cloned[0]


def test_repositories_stay_on_persistent_dir_wherever_the_config_is(
    config_file, persistent_dir, monkeypatch
):
    """Test a config outside $PERSISTENT_DIR still puts the repositories there."""
    repo = _cloned_repo(config_file, monkeypatch)

    assert repo.git_dir == persistent_dir / ".local" / "share" / "workspace-admin" / "private"
    assert repo.work_tree == persistent_dir / "assets" / "private"
    assert not repo.git_dir.is_relative_to(config_file.parent)
    assert not repo.work_tree.is_relative_to(config_file.parent)


def test_repositories_go_to_home_without_persistent_dir(
    config_file, monkeypatch, tmp_path
):
    """Test that without $PERSISTENT_DIR the repositories go below home, not the config."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("PERSISTENT_DIR")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert not config_file.is_relative_to(home)

    repo = _cloned_repo(config_file, monkeypatch)

    assert repo.git_dir == home / ".local" / "share" / "workspace-admin" / "private"
    assert repo.work_tree == home / "assets" / "private"


def test_start_git_sync_schedules_every_configured_repository(config_file, monkeypatch):
    """Test a repository whose clone failed is scheduled too, to be retried."""
    private_repo = make_repo("private")
    common_repo = make_repo("common")

    monkeypatch.setattr(
        bootstrap, "load_config", lambda _path, _root: [private_repo, common_repo]
    )

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

    status = SyncStatus()
    thread, stop_event = bootstrap.start_git_sync(status, config_file, 60)

    assert thread == "thread"
    assert scheduled["repos"] == [private_repo, common_repo]
    assert scheduled["interval_seconds"] == 60
    # The caller gets the event back, so the loop can actually be stopped.
    assert stop_event is scheduled["stop_event"]
    assert scheduled["status"] is status


def test_start_git_sync_returns_none_when_config_load_fails(config_file, monkeypatch, caplog):
    """Test a broken config file does not stop the service from starting."""

    def fake_load_config(_path, _root):
        raise ConfigError("config.toml: no [assets] section found")

    monkeypatch.setattr(bootstrap, "load_config", fake_load_config)
    status = SyncStatus()

    with caplog.at_level("ERROR"):
        assert bootstrap.start_git_sync(status, config_file) is None

    assert "Cannot start git sync" in caplog.text
    assert status.snapshot()["enabled"] is False
    assert status.snapshot()["state"] == "config_error"


def test_start_git_backup_returns_while_the_clone_is_still_running(monkeypatch):
    """Test a slow clone does not hold up the caller, i.e. the HTTP API."""
    clone_started = threading.Event()
    release_clone = threading.Event()
    steps = []

    def slow_clone(_status, _config_path):
        steps.append("clone")
        clone_started.set()
        release_clone.wait(timeout=30)

    monkeypatch.setattr(bootstrap, "clone_configured_repos", slow_clone)
    monkeypatch.setattr(
        bootstrap,
        "start_git_sync",
        lambda status, _config_path, interval, _stop_event: steps.append((status, interval)),
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
    monkeypatch.setattr(bootstrap, "clone_configured_repos", lambda _status, _config_path: {})
    monkeypatch.setattr(
        bootstrap, "start_git_sync", lambda status, _config_path, interval, _stop_event: None
    )

    thread = bootstrap.start_git_backup(SyncStatus(), 60)
    thread.join(timeout=30)

    assert thread.daemon
    assert thread.name == bootstrap.BOOTSTRAP_THREAD_NAME


@pytest.mark.parametrize("exists", [True, False])
def test_start_git_backup_logs_the_config_path(config_file, monkeypatch, caplog, exists):
    """Test the absolute config path is logged, whether the file exists or not."""
    if not exists:
        config_file.unlink()
    monkeypatch.setattr(bootstrap, "_clone_then_sync", lambda *_args: None)

    with caplog.at_level("INFO"):
        bootstrap.start_git_backup(SyncStatus(), 60).join(timeout=30)

    assert f"Using config {config_file} (from {CONFIG_ENV_VAR})" in caplog.text


def test_start_git_backup_logs_a_relative_config_as_absolute(
    monkeypatch, caplog, tmp_path
):
    """Test a relative $WORKSPACE_ADMIN_CONFIG is logged as a full path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(CONFIG_ENV_VAR, "relative/config.toml")
    monkeypatch.setattr(bootstrap, "_clone_then_sync", lambda *_args: None)

    with caplog.at_level("INFO"):
        bootstrap.start_git_backup(SyncStatus(), 60).join(timeout=30)

    expected = tmp_path / "relative" / "config.toml"
    assert f"Using config {expected} (from {CONFIG_ENV_VAR})" in caplog.text


def test_start_git_backup_logs_the_default_config_with_its_rule(
    monkeypatch, caplog, tmp_path
):
    """Test the default config is logged as the default, naming the unset variable."""
    monkeypatch.delenv(CONFIG_ENV_VAR)
    home = tmp_path / "home"
    _point_home_at(home, monkeypatch)
    monkeypatch.setattr(bootstrap, "_clone_then_sync", lambda *_args: None)

    with caplog.at_level("INFO"):
        bootstrap.start_git_backup(SyncStatus(), 60).join(timeout=30)

    expected = home / ".config" / "workspace-admin" / "config.toml"
    assert (
        f"Using config {expected} (default location; {CONFIG_ENV_VAR} is not set)"
        in caplog.text
    )


def test_start_git_backup_discovers_the_config_once(config_file, monkeypatch, tmp_path):
    """Test every step reads the file chosen on startup, even once the variable changes."""
    discovered = []
    real_discover_config = bootstrap.discover_config

    def counting_discover_config():
        discovered.append(None)
        return real_discover_config()

    read_from = []

    def fake_load_config(path, _root):
        read_from.append(path)
        return [make_repo("private")]

    def clone_then_change_the_variable(_repo):
        monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "elsewhere.toml"))
        return True

    def fake_load_sync_interval(path):
        read_from.append(path)
        return 60

    finished = threading.Thread(target=lambda: None)
    finished.start()
    monkeypatch.setattr(bootstrap, "discover_config", counting_discover_config)
    monkeypatch.setattr(bootstrap, "load_config", fake_load_config)
    monkeypatch.setattr(bootstrap, "clone_asset", clone_then_change_the_variable)
    monkeypatch.setattr(bootstrap, "load_sync_interval", fake_load_sync_interval)
    monkeypatch.setattr(bootstrap, "start_sync_scheduler", lambda *_args: finished)

    bootstrap.start_git_backup(SyncStatus()).join(timeout=30)

    assert len(discovered) == 1
    # Loaded for the clone, loaded again for the sync, and read for the interval.
    assert read_from == [config_file, config_file, config_file]


def test_start_git_backup_logs_an_unexpected_error(monkeypatch, caplog):
    """Test an error on the background thread is logged, not lost."""

    def broken_clone(_status, _config_path):
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


def _point_home_at(home, monkeypatch):
    """Make ``home`` the home directory, on every platform."""
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


def test_start_git_backup_reports_a_missing_default_config_once(
    monkeypatch, caplog, tmp_path
):
    """Test a missing default config is logged once, not as an error, and nothing runs."""
    monkeypatch.delenv(CONFIG_ENV_VAR)
    _point_home_at(tmp_path / "home", monkeypatch)
    monkeypatch.delenv("WORKSPACE_CONFIG_TEMPLATE", raising=False)
    monkeypatch.setattr(bootstrap, "clone_configured_repos", _fail_if_called)
    monkeypatch.setattr(bootstrap, "start_git_sync", _fail_if_called)
    status = SyncStatus()

    with caplog.at_level("INFO"):
        bootstrap.start_git_backup(status, 60).join(timeout=30)

    assert caplog.text.count("git backup is disabled") == 1
    assert "ERROR" not in caplog.text
    assert status.snapshot()["state"] == "disabled"
    missing = [r.getMessage() for r in caplog.records if "disabled" in r.getMessage()]
    default_config = tmp_path / "home" / ".config" / "workspace-admin" / "config.toml"
    assert f"{CONFIG_ENV_VAR} is not set" in missing[0]
    assert str(default_config) in missing[0]


def test_start_git_backup_survives_an_unknown_home_directory(monkeypatch, caplog):
    """Test no home directory leaves the backup off instead of stopping the service."""

    def no_home():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.delenv(CONFIG_ENV_VAR)
    monkeypatch.setattr(Path, "home", staticmethod(no_home))
    monkeypatch.setattr(bootstrap, "provision_config", _fail_if_called)
    monkeypatch.setattr(bootstrap, "clone_configured_repos", _fail_if_called)
    monkeypatch.setattr(bootstrap, "start_git_sync", _fail_if_called)
    status = SyncStatus()

    with caplog.at_level("INFO"):
        thread = bootstrap.start_git_backup(status, 60)
        thread.join(timeout=30)

    assert not thread.is_alive()
    errors = [record for record in caplog.records if record.levelname == "ERROR"]
    assert len(errors) == 1
    assert "Could not determine home directory" in errors[0].getMessage()
    assert "git backup is disabled" in errors[0].getMessage()
    assert f"{CONFIG_ENV_VAR} is not set" in errors[0].getMessage()
    assert str(Path("~", ".config", "workspace-admin", "config.toml")) in errors[0].getMessage()
    assert "Unexpected error" not in caplog.text
    assert status.snapshot()["state"] == "config_error"


def test_start_git_backup_never_logs_the_config_contents(config_file, monkeypatch, caplog):
    """Test nothing read from the config file reaches the log, only its path (NFR-3)."""
    sentinel = "sentinel-token-3f9a1c"
    config_file.write_text(f'GIT_REPO_TOKEN = "{sentinel}"\n', encoding="utf-8")
    monkeypatch.setattr(bootstrap, "clone_asset", _fail_if_called)

    with caplog.at_level("DEBUG"):
        bootstrap.start_git_backup(SyncStatus(), 60).join(timeout=30)

    assert str(config_file) in caplog.text
    assert sentinel not in caplog.text


def _start_without_a_config_file(status, monkeypatch, caplog):
    """Run the backup to its end with no template, failing on any clone or sync."""
    monkeypatch.delenv("WORKSPACE_CONFIG_TEMPLATE", raising=False)
    monkeypatch.setattr(bootstrap, "clone_configured_repos", _fail_if_called)
    monkeypatch.setattr(bootstrap, "start_git_sync", _fail_if_called)

    with caplog.at_level("INFO"):
        thread = bootstrap.start_git_backup(status, 60)
        thread.join(timeout=30)

    assert not thread.is_alive()
    assert "Unexpected error" not in caplog.text


def _assert_reported_missing(path, status, caplog):
    """Assert the missing file at $WORKSPACE_ADMIN_CONFIG was logged as an error."""
    errors = [record for record in caplog.records if record.levelname == "ERROR"]
    assert len(errors) == 1
    assert CONFIG_ENV_VAR in errors[0].getMessage()
    assert str(path) in errors[0].getMessage()
    assert status.snapshot()["state"] == "config_error"
    assert status.snapshot()["enabled"] is False


def test_start_git_backup_reports_a_missing_variable_config_as_an_error(
    config_file, monkeypatch, caplog
):
    """Test a missing file at $WORKSPACE_ADMIN_CONFIG is an error naming the path."""
    config_file.unlink()
    status = SyncStatus()

    _start_without_a_config_file(status, monkeypatch, caplog)

    _assert_reported_missing(config_file, status, caplog)


def test_start_git_backup_does_not_fall_back_to_the_default_config(
    config_file, monkeypatch, caplog, tmp_path
):
    """Test a config in the default directory is ignored when the variable's file is missing."""
    config_file.unlink()
    home = tmp_path / "home"
    _point_home_at(home, monkeypatch)
    default_config = home / ".config" / "workspace-admin" / "config.toml"
    default_config.parent.mkdir(parents=True)
    default_config.write_bytes(EXAMPLE_CONFIG_PATH.read_bytes())
    monkeypatch.setattr(bootstrap, "load_config", _fail_if_called)
    status = SyncStatus()

    _start_without_a_config_file(status, monkeypatch, caplog)

    _assert_reported_missing(config_file, status, caplog)
    assert str(default_config) not in caplog.text


def test_start_git_backup_reports_a_directory_at_the_variable_as_an_error(
    config_file, monkeypatch, caplog
):
    """Test a directory at $WORKSPACE_ADMIN_CONFIG gives the same error as a missing file."""
    config_file.unlink()
    config_file.mkdir()
    status = SyncStatus()

    _start_without_a_config_file(status, monkeypatch, caplog)

    _assert_reported_missing(config_file, status, caplog)


def test_start_git_backup_creates_the_variable_config_without_an_error(
    config_file, monkeypatch, caplog, tmp_path
):
    """Test the image's first start, which creates the config, logs no error."""
    config_file.unlink()
    template = tmp_path / "template.toml"
    template.write_bytes(EXAMPLE_CONFIG_PATH.read_bytes())
    monkeypatch.setenv("WORKSPACE_CONFIG_TEMPLATE", str(template))
    monkeypatch.setattr(bootstrap, "clone_configured_repos", _fail_if_called)
    status = SyncStatus()

    with caplog.at_level("INFO"):
        bootstrap.start_git_backup(status, 60).join(timeout=30)

    assert config_file.is_file()
    assert "ERROR" not in caplog.text
    assert status.snapshot()["state"] == "needs_setup"


def test_start_git_backup_clones_when_the_config_exists(config_file, monkeypatch):
    """Test an existing config file goes straight on to cloning."""
    assert config_file.is_file()
    steps = []
    monkeypatch.setattr(
        bootstrap, "clone_configured_repos", lambda _status, _config_path: steps.append("clone")
    )
    monkeypatch.setattr(
        bootstrap,
        "start_git_sync",
        lambda status, _config_path, interval, _stop_event: steps.append("sync"),
    )

    bootstrap.start_git_backup(SyncStatus(), 60).join(timeout=30)

    assert steps == ["clone", "sync"]


def test_start_git_sync_uses_the_stop_event_it_is_given(config_file, monkeypatch):
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

    _, returned = bootstrap.start_git_sync(SyncStatus(), config_file, 60, stop_event)

    assert scheduled["stop_event"] is stop_event
    assert returned is stop_event


def _start_a_waiting_sync(started):
    """Build a start_git_sync whose sync thread runs until it is stopped."""

    def fake_start_git_sync(_status, _config_path, _interval, stop_event):
        thread = threading.Thread(target=stop_event.wait, daemon=True)
        thread.start()
        started.append(thread)
        return thread, stop_event

    return fake_start_git_sync


def test_start_git_backup_lives_as_long_as_the_sync(monkeypatch):
    """Test the returned thread is the one to wait for on shutdown."""
    started = []
    monkeypatch.setattr(bootstrap, "clone_configured_repos", lambda _status, _config_path: {})
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

    def clone_during_shutdown(_status, _config_path):
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

    assert bootstrap.start_git_sync(SyncStatus(), config_file) is not None

    assert scheduled["interval_seconds"] == 120
    assert read_from == [config_file]


def test_start_git_sync_prefers_a_given_interval(config_file, monkeypatch):
    """Test --sync-interval overrides the config file, which is not read."""
    scheduled = {}
    _schedule_into(scheduled, monkeypatch)
    monkeypatch.setattr(bootstrap, "load_sync_interval", _fail_if_called)

    bootstrap.start_git_sync(SyncStatus(), config_file, 30)

    assert scheduled["interval_seconds"] == 30


def test_start_git_sync_is_not_started_when_the_interval_cannot_be_read(
    config_file, monkeypatch, caplog
):
    """Test a config file broken since the clone is logged, not raised."""
    scheduled = {}
    _schedule_into(scheduled, monkeypatch)

    def unreadable(_path):
        raise ConfigError("config.toml is not valid TOML: SYNC_INTERVAL_SECONDS")

    monkeypatch.setattr(bootstrap, "load_sync_interval", unreadable)
    status = SyncStatus()

    with caplog.at_level("ERROR"):
        assert bootstrap.start_git_sync(status, config_file) is None

    assert "SYNC_INTERVAL_SECONDS" in caplog.text
    assert not scheduled
    assert status.snapshot()["enabled"] is False


# --------------------------------------------------------------------------
# What /storage/status says before the backup runs
# --------------------------------------------------------------------------


def test_status_is_disabled_without_a_config_or_template(monkeypatch, tmp_path):
    """Test a workspace nobody has set up reports disabled."""
    monkeypatch.delenv(CONFIG_ENV_VAR)
    _point_home_at(tmp_path / "home", monkeypatch)
    monkeypatch.delenv("WORKSPACE_CONFIG_TEMPLATE", raising=False)
    status = SyncStatus()

    bootstrap.start_git_backup(status, 60).join(timeout=30)

    assert status.snapshot()["state"] == "disabled"


def test_status_needs_setup_once_the_template_is_provisioned(
    config_file, monkeypatch, tmp_path
):
    """Test a config file just created from the template reports needs_setup."""
    config_file.unlink()
    template = tmp_path / "template.toml"
    template.write_bytes(EXAMPLE_CONFIG_PATH.read_bytes())
    monkeypatch.setenv("WORKSPACE_CONFIG_TEMPLATE", str(template))
    status = SyncStatus()

    bootstrap.start_git_backup(status, 60).join(timeout=30)

    assert status.snapshot()["state"] == "needs_setup"


def test_status_needs_setup_while_placeholders_remain(config_file):
    """Test a config file still holding the template's values reports needs_setup."""
    config_file.write_bytes(EXAMPLE_CONFIG_PATH.read_bytes())
    status = SyncStatus()

    bootstrap.start_git_backup(status, 60).join(timeout=30)

    assert status.snapshot()["state"] == "needs_setup"


def test_status_reports_a_broken_config(config_file):
    """Test a config file that cannot be used reports config_error."""
    config_file.write_text("oops = \n", encoding="utf-8")
    status = SyncStatus()

    bootstrap.start_git_backup(status, 60).join(timeout=30)

    assert status.snapshot()["state"] == "config_error"


def test_status_lists_a_repository_whose_clone_failed(config_file, monkeypatch):
    """Test a failed clone shows up with its kind instead of an empty list."""
    monkeypatch.setattr(bootstrap, "load_config", lambda _path, _root: [make_repo("private")])

    def refuse(_repo):
        raise CloneError("denied", SyncErrorKind.AUTH_FAILED)

    monkeypatch.setattr(bootstrap, "clone_asset", refuse)
    status = SyncStatus()

    bootstrap.clone_configured_repos(status, config_file)

    snapshot = status.snapshot()
    assert snapshot["state"] == "starting"
    assert snapshot["repositories"]["private"]["state"] == "clone_failed"
    assert snapshot["repositories"]["private"]["last_error"]["kind"] == "auth_failed"


# --------------------------------------------------------------------------
# A discovered config inside a work tree is refused
# --------------------------------------------------------------------------

#: A config the work-tree check, not the placeholder check, has to refuse.
WORK_TREE_CONFIG = """\
[assets.private]
GIT_REPO_URL = "https://gitlab.example.org/team/private.git"
GIT_REPO_BRANCH = "main"
GIT_REPO_TOKEN = "a-real-looking-token"
"""


def _config_in_the_private_work_tree(persistent_dir):
    """Write a valid config into ``<root>/assets/private`` and return its path."""
    config_path = persistent_dir / "assets" / "private" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(WORK_TREE_CONFIG, encoding="utf-8")
    return config_path


def _symlink_or_skip(link, target):
    """Link ``link`` to the directory ``target``, or skip where that is not allowed."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")


def _assert_refused(monkeypatch, caplog):
    """Run the backup and assert the config was refused before anything ran."""
    monkeypatch.delenv("WORKSPACE_CONFIG_TEMPLATE", raising=False)
    monkeypatch.setattr(bootstrap, "clone_asset", _fail_if_called)
    monkeypatch.setattr(bootstrap, "start_sync_scheduler", _fail_if_called)
    status = SyncStatus()

    with caplog.at_level("INFO"):
        thread = bootstrap.start_git_backup(status, 60)
        thread.join(timeout=30)

    assert not thread.is_alive()
    errors = [record.getMessage() for record in caplog.records if record.levelname == "ERROR"]
    assert any("[assets.private]" in error and "contains the config file" in error
               for error in errors)
    assert "Unexpected error" not in caplog.text
    assert "a-real-looking-token" not in caplog.text
    assert status.snapshot()["state"] == "config_error"


def test_variable_config_inside_a_work_tree_is_refused(persistent_dir, monkeypatch, caplog):
    """Test a config named by $WORKSPACE_ADMIN_CONFIG inside assets/<name> is refused."""
    config_path = _config_in_the_private_work_tree(persistent_dir)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))

    _assert_refused(monkeypatch, caplog)


def test_relative_variable_config_inside_a_work_tree_is_refused(
    persistent_dir, monkeypatch, caplog
):
    """Test a relative $WORKSPACE_ADMIN_CONFIG inside assets/<name> is refused."""
    _config_in_the_private_work_tree(persistent_dir)
    monkeypatch.chdir(persistent_dir)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(Path("assets", "private", "config.toml")))

    _assert_refused(monkeypatch, caplog)


def test_config_reached_through_a_symlink_inside_a_work_tree_is_refused(
    persistent_dir, monkeypatch, caplog, tmp_path
):
    """Test a config path through a symlink into assets/<name> is refused."""
    config_path = _config_in_the_private_work_tree(persistent_dir)
    link = tmp_path / "link"
    _symlink_or_skip(link, config_path.parent)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(link / "config.toml"))

    _assert_refused(monkeypatch, caplog)


def test_config_inside_a_work_tree_under_a_symlinked_root_is_refused(
    persistent_dir, monkeypatch, caplog, tmp_path
):
    """Test the check holds when $PERSISTENT_DIR itself is reached through a symlink."""
    config_path = _config_in_the_private_work_tree(persistent_dir)
    link_root = tmp_path / "link-root"
    _symlink_or_skip(link_root, persistent_dir)
    monkeypatch.setenv("PERSISTENT_DIR", str(link_root))
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))

    _assert_refused(monkeypatch, caplog)
