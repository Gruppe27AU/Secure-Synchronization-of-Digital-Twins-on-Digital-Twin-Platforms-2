"""
Unit tests for the periodic scheduling of the git backup.

Covers :mod:`admin.git.scheduler`: synchronizing every repository, keeping
going when one of them fails, and running until the stop event is set.
These tests never touch git - :func:`admin.git.sync.sync_once` is replaced
throughout.
"""

import threading

from admin.git import scheduler
from admin.git.sync import SyncError
from tests.helpers.repo_factory import make_repo


class StubStopEvent:
    """
    A stop event that lets the loop run a fixed number of times.

    Args:
        allowed_rounds: How many times ``wait`` returns False before it
            returns True and ends the loop.
    """

    def __init__(self, allowed_rounds):
        self.allowed_rounds = allowed_rounds
        self.waits = []

    def wait(self, timeout=None):
        """Record the interval and report whether the loop should stop."""
        self.waits.append(timeout)
        return len(self.waits) > self.allowed_rounds

    def set(self):
        """Stop the loop at its next wait, like threading.Event.set."""
        self.allowed_rounds = 0


def test_sync_all_syncs_every_repository(monkeypatch):
    """Test every configured repository is synchronized once per round."""
    synced = []
    monkeypatch.setattr(scheduler, "sync_once", synced.append)

    repos = [make_repo("private"), make_repo("common")]
    scheduler.sync_all(repos)

    assert [repo.name for repo in synced] == ["private", "common"]


def test_sync_all_continues_after_a_failing_repository(monkeypatch, caplog):
    """Test one broken repository does not stop the others."""
    synced = []

    def fake_sync_once(repo):
        synced.append(repo.name)
        if repo.name == "private":
            raise SyncError("Failed to push changes for repository 'private'")

    monkeypatch.setattr(scheduler, "sync_once", fake_sync_once)

    scheduler.sync_all([make_repo("private"), make_repo("common")])

    assert synced == ["private", "common"]
    assert "private" in caplog.text


def test_sync_all_survives_an_unexpected_error(monkeypatch, caplog):
    """Test an error other than SyncError does not escape and kill the thread.

    sync_all runs on a background thread, so an escaping exception would
    end the backup silently while the service kept serving requests.
    """
    synced = []

    def fake_sync_once(repo):
        synced.append(repo.name)
        if repo.name == "private":
            raise FileNotFoundError(2, "No such file or directory: 'git'")

    monkeypatch.setattr(scheduler, "sync_once", fake_sync_once)

    scheduler.sync_all([make_repo("private"), make_repo("common")])

    assert synced == ["private", "common"]
    assert "Unexpected error" in caplog.text


def test_sync_loop_runs_until_the_stop_event_is_set(monkeypatch):
    """Test the loop synchronizes once per interval and then stops."""
    rounds = []
    monkeypatch.setattr(scheduler, "sync_all", rounds.append)

    repos = [make_repo("common")]
    stop_event = StubStopEvent(allowed_rounds=3)

    scheduler._sync_loop(repos, 300, stop_event)  # pylint: disable=protected-access

    assert len(rounds) == 3
    assert stop_event.waits == [300, 300, 300, 300]


def test_sync_loop_does_not_sync_when_stopped_immediately(monkeypatch):
    """Test nothing is synchronized when the event is already set."""
    rounds = []
    monkeypatch.setattr(scheduler, "sync_all", rounds.append)

    stop_event = StubStopEvent(allowed_rounds=3)
    stop_event.set()

    # pylint: disable-next=protected-access
    scheduler._sync_loop([make_repo("common")], 300, stop_event)

    assert not rounds


def test_start_sync_scheduler_runs_on_a_background_daemon_thread(monkeypatch):
    """Test the scheduler runs off the main thread and does not block it."""
    synced = threading.Event()
    stop_event = threading.Event()

    def fake_sync_all(_repos):
        synced.set()
        stop_event.set()

    monkeypatch.setattr(scheduler, "sync_all", fake_sync_all)

    thread = scheduler.start_sync_scheduler([make_repo("common")], 0, stop_event)
    thread.join(timeout=5)

    assert thread.daemon is True
    assert synced.is_set()
    assert thread.is_alive() is False
