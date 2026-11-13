"""
Unit tests for :func:`admin.git.service.scheduler.sync_all`: every repository
cloned or synchronized once, failures recorded, and nothing started once
stopped. sync_once and the git file restore are replaced throughout.
"""

import threading

import pytest

from admin.git.repo.sync import SyncStopped
from admin.git.run.errors import SyncError, SyncErrorKind
from admin.git.service import scheduler
from admin.git.service.status import SyncStatus
from tests.helpers.repo_factory import make_repo
from tests.unit.git.service.scheduler_stubs import named_repo


pytestmark = pytest.mark.usefixtures("restored", "not_cloned")


def test_sync_all_clones_a_repository_whose_clone_failed(monkeypatch, not_cloned):
    """Test a failed clone is retried on the interval, without a restart."""
    not_cloned.add("private")
    cloned, synced = [], []
    monkeypatch.setattr(scheduler, "clone_asset", lambda repo: cloned.append(repo.name))
    monkeypatch.setattr(
        scheduler, "sync_once", lambda repo, _stop_event: synced.append(repo.name)
    )
    status = SyncStatus()

    scheduler.sync_all([named_repo("private"), named_repo("common")], status)

    assert cloned == ["private"]
    assert synced == ["common"]
    assert status.snapshot()["repositories"]["private"]["state"] == "syncing"


def test_sync_all_records_a_clone_that_fails_again(monkeypatch, not_cloned):
    """Test a clone that still fails is reported with its kind."""
    not_cloned.add("private")

    def refuse(_repo):
        raise SyncError("denied", SyncErrorKind.AUTH_FAILED)

    monkeypatch.setattr(scheduler, "clone_asset", refuse)
    status = SyncStatus()

    scheduler.sync_all([named_repo("private")], status)

    private = status.snapshot()["repositories"]["private"]
    assert private["state"] == "clone_failed"
    assert private["last_error"]["kind"] == "auth_failed"


def test_sync_all_restores_the_git_file_before_each_sync(monkeypatch, restored):
    """Test a lost .git file is written back before the cycle runs."""
    order = []
    monkeypatch.setattr(
        scheduler,
        "sync_once",
        lambda repo, _stop_event: order.append(list(restored)),
    )

    scheduler.sync_all([make_repo("private"), make_repo("common")], SyncStatus())

    assert order == [["private"], ["private", "common"]]


def test_sync_all_syncs_every_repository(monkeypatch):
    """Test every configured repository is synchronized once per round."""
    synced = []
    monkeypatch.setattr(
        scheduler, "sync_once", lambda repo, _stop_event: synced.append(repo)
    )

    repos = [make_repo("private"), make_repo("common")]
    scheduler.sync_all(repos, SyncStatus())

    assert [repo.name for repo in synced] == ["private", "common"]


def test_sync_all_continues_after_a_failing_repository(monkeypatch, caplog):
    """Test one broken repository does not stop the others."""
    synced = []

    def fake_sync_once(repo, _stop_event):
        synced.append(repo.name)
        if repo.name == "private":
            raise SyncError("Failed to push changes for repository 'private'")

    monkeypatch.setattr(scheduler, "sync_once", fake_sync_once)

    scheduler.sync_all([make_repo("private"), make_repo("common")], SyncStatus())

    assert synced == ["private", "common"]
    assert "private" in caplog.text


def test_sync_all_survives_an_unexpected_error(monkeypatch, caplog):
    """Test an error other than SyncError does not escape and kill the thread.

    sync_all runs on a background thread, so an escaping exception would
    end the backup silently while the service kept serving requests.
    """
    synced = []

    def fake_sync_once(repo, _stop_event):
        synced.append(repo.name)
        if repo.name == "private":
            raise FileNotFoundError(2, "No such file or directory: 'git'")

    monkeypatch.setattr(scheduler, "sync_once", fake_sync_once)

    scheduler.sync_all([make_repo("private"), make_repo("common")], SyncStatus())

    assert synced == ["private", "common"]
    assert "Unexpected error" in caplog.text


def test_sync_all_records_success_and_failure_kinds(monkeypatch):
    """Test each repository's outcome reaches the status, kind and all."""

    def fake_sync_once(repo, _stop_event):
        if repo.name == "private":
            raise SyncError(
                "Failed to push changes for repository 'private': secret stderr",
                SyncErrorKind.AUTH_FAILED,
            )

    monkeypatch.setattr(scheduler, "sync_once", fake_sync_once)
    status = SyncStatus()

    scheduler.sync_all([make_repo("private"), make_repo("common")], status)

    repos = status.snapshot()["repositories"]
    assert repos["private"]["last_error"]["kind"] == "auth_failed"
    assert repos["private"]["last_success"] is None
    assert repos["common"]["last_success"] is not None
    assert repos["common"]["last_error"] is None
    # The message, with git's output in it, never reaches the status.
    assert "secret stderr" not in str(status.snapshot())


def test_sync_all_records_an_unexpected_error(monkeypatch):
    """Test an error other than SyncError is recorded as unexpected."""

    def fake_sync_once(_repo, _stop_event):
        raise FileNotFoundError(2, "No such file or directory: 'git'")

    monkeypatch.setattr(scheduler, "sync_once", fake_sync_once)
    status = SyncStatus()

    scheduler.sync_all([make_repo("private")], status)

    last_error = status.snapshot()["repositories"]["private"]["last_error"]
    assert last_error["kind"] == "unexpected"


def test_sync_all_starts_no_repository_once_stopped(monkeypatch):
    """Test a stop between two repositories leaves the second one alone."""
    stop_event = threading.Event()
    synced = []

    def fake_sync_once(repo, _stop_event):
        synced.append(repo.name)
        stop_event.set()

    monkeypatch.setattr(scheduler, "sync_once", fake_sync_once)
    status = SyncStatus()

    scheduler.sync_all(
        [make_repo("private"), make_repo("common")], status, stop_event
    )

    assert synced == ["private"]
    assert "common" not in status.snapshot()["repositories"]


def test_sync_all_records_nothing_for_a_round_cut_short(monkeypatch, caplog):
    """Test a cycle stopped for shutdown is neither a success nor a failure."""

    def fake_sync_once(repo, _stop_event):
        raise SyncStopped(f"Stopped synchronizing repository '{repo.name}'")

    monkeypatch.setattr(scheduler, "sync_once", fake_sync_once)
    status = SyncStatus()

    with caplog.at_level("INFO"):
        scheduler.sync_all(
            [make_repo("private"), make_repo("common")], status, threading.Event()
        )

    assert status.snapshot()["repositories"] == {}
    assert "Stopped synchronizing repository 'private'" in caplog.text
    assert "ERROR" not in caplog.text
