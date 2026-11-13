"""
Unit tests for the loop of :mod:`admin.git.service.scheduler`: the background
thread, the interval and the stop event.
"""

import threading

import pytest

from admin.git.service import scheduler
from admin.git.service.status import SyncStatus
from tests.helpers.repo_factory import make_repo
from tests.unit.git.service.scheduler_stubs import StubStopEvent


pytestmark = pytest.mark.usefixtures("restored", "not_cloned")


def test_sync_loop_runs_until_the_stop_event_is_set(monkeypatch):
    """Test the loop synchronizes once per interval and then stops."""
    rounds = []
    monkeypatch.setattr(
        scheduler, "sync_all", lambda repos, _status, _stop_event: rounds.append(repos)
    )

    repos = [make_repo("common")]
    stop_event = StubStopEvent(allowed_rounds=3)

    # pylint: disable-next=protected-access
    scheduler._sync_loop(repos, 300, stop_event, SyncStatus())

    assert len(rounds) == 3
    assert stop_event.waits == [300, 300, 300, 300]


def test_sync_loop_does_not_sync_when_stopped_immediately(monkeypatch):
    """Test nothing is synchronized when the event is already set."""
    rounds = []
    monkeypatch.setattr(
        scheduler, "sync_all", lambda repos, _status, _stop_event: rounds.append(repos)
    )

    stop_event = StubStopEvent(allowed_rounds=3)
    stop_event.set()

    # pylint: disable-next=protected-access
    scheduler._sync_loop([make_repo("common")], 300, stop_event, SyncStatus())

    assert not rounds


def test_start_sync_scheduler_runs_on_a_background_daemon_thread(monkeypatch):
    """Test the scheduler runs off the main thread and does not block it."""
    synced = threading.Event()
    stop_event = threading.Event()

    def fake_sync_all(_repos, _status, _stop_event):
        synced.set()
        stop_event.set()

    monkeypatch.setattr(scheduler, "sync_all", fake_sync_all)

    thread = scheduler.start_sync_scheduler(
        [make_repo("common")], 0, stop_event, SyncStatus()
    )
    thread.join(timeout=5)

    assert thread.daemon is True
    assert synced.is_set()
    assert thread.is_alive() is False


def test_start_sync_scheduler_enables_the_status(monkeypatch):
    """Test the scheduled repositories are listed before their first sync."""
    monkeypatch.setattr(scheduler, "sync_all", lambda _repos, _status, _stop_event: None)
    stop_event = threading.Event()
    stop_event.set()
    status = SyncStatus()

    thread = scheduler.start_sync_scheduler(
        [make_repo("private"), make_repo("common")], 300, stop_event, status
    )
    thread.join(timeout=5)

    snapshot = status.snapshot()
    assert snapshot["enabled"] is True
    assert sorted(snapshot["repositories"]) == ["common", "private"]


def test_sync_loop_hands_its_stop_event_to_the_round(monkeypatch):
    """Test a running round can see the service stopping, not just the wait."""
    received = []
    monkeypatch.setattr(
        scheduler,
        "sync_all",
        lambda _repos, _status, stop_event: received.append(stop_event),
    )
    stop_event = StubStopEvent(allowed_rounds=1)

    # pylint: disable-next=protected-access
    scheduler._sync_loop([make_repo("common")], 300, stop_event, SyncStatus())

    assert received == [stop_event]
