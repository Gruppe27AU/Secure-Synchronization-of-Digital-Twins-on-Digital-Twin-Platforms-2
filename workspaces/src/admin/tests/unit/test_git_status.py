"""
Unit tests for the record of recent git backups.

Covers :mod:`admin.git.status`: starting disabled, listing repositories
once enabled, recording successes and failures, and keeping the last error
after a later success. The clock is replaced, so timestamps are exact.
"""

import itertools
import threading
from datetime import datetime, timedelta, timezone

import pytest

from admin.git.status import BackupState, SyncStatus
from admin.git.errors import SyncErrorKind

START = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)


def step_clock():
    """Return a clock that moves one minute forward every time it is read."""
    minutes = itertools.count()
    return lambda: START + timedelta(minutes=next(minutes))


def test_status_starts_disabled_and_empty():
    """Test nothing is reported before the backup is enabled."""
    assert SyncStatus().snapshot() == {
        "state": "disabled",
        "enabled": False,
        "repositories": {},
    }


def test_enable_lists_repositories_with_no_attempt_yet():
    """Test enabled repositories are listed before their first sync."""
    status = SyncStatus()
    status.enable(["private", "common"])

    assert status.snapshot() == {
        "state": "running",
        "enabled": True,
        "repositories": {
            "private": {
                "state": "syncing",
                "last_attempt": None,
                "last_success": None,
                "last_error": None,
            },
            "common": {
                "state": "syncing",
                "last_attempt": None,
                "last_success": None,
                "last_error": None,
            },
        },
    }


def test_record_success_sets_attempt_and_success():
    """Test a success is timestamped as both attempt and success."""
    status = SyncStatus(clock=step_clock())
    status.enable(["private"])
    status.record_success("private")

    assert status.snapshot()["repositories"]["private"] == {
        "state": "syncing",
        "last_attempt": "2026-10-09T12:00:00+00:00",
        "last_success": "2026-10-09T12:00:00+00:00",
        "last_error": None,
    }


def test_record_failure_reports_only_the_kind():
    """Test a failure exposes its kind and time, and nothing else."""
    status = SyncStatus(clock=step_clock())
    status.enable(["private"])
    status.record_failure("private", SyncErrorKind.AUTH_FAILED)

    assert status.snapshot()["repositories"]["private"] == {
        "state": "syncing",
        "last_attempt": "2026-10-09T12:00:00+00:00",
        "last_success": None,
        "last_error": {
            "kind": "auth_failed",
            "at": "2026-10-09T12:00:00+00:00",
        },
    }


def test_last_error_is_kept_after_a_later_success():
    """Test a success does not erase the error, only outdates it."""
    status = SyncStatus(clock=step_clock())
    status.enable(["private"])
    status.record_failure("private", SyncErrorKind.GIT_FAILED)
    status.record_success("private")

    repo = status.snapshot()["repositories"]["private"]
    assert repo["last_success"] == "2026-10-09T12:01:00+00:00"
    assert repo["last_error"] == {
        "kind": "git_failed",
        "at": "2026-10-09T12:00:00+00:00",
    }


def test_enable_again_keeps_recorded_outcomes():
    """Test enabling a repository twice does not reset its history."""
    status = SyncStatus(clock=step_clock())
    status.enable(["private"])
    status.record_success("private")
    status.enable(["private"])

    repo = status.snapshot()["repositories"]["private"]
    assert repo["last_success"] == "2026-10-09T12:00:00+00:00"


def test_snapshot_is_a_copy():
    """Test changing a snapshot does not change the recorded status."""
    status = SyncStatus()
    status.enable(["private"])
    status.snapshot()["repositories"].clear()

    assert "private" in status.snapshot()["repositories"]


def test_concurrent_writers_lose_no_update():
    """Test records from several threads all arrive."""
    status = SyncStatus()
    names = [f"repo-{index}" for index in range(20)]

    threads = [
        threading.Thread(target=status.record_success, args=(name,))
        for name in names
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(status.snapshot()["repositories"]) == sorted(names)


# --------------------------------------------------------------------------
# States: telling "not set up" apart from setup failures
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [BackupState.NEEDS_SETUP, BackupState.CONFIG_ERROR, BackupState.STARTING],
)
def test_a_backup_that_is_not_running_says_why(state):
    """Test each state before running is reported, and none counts as enabled."""
    status = SyncStatus()
    status.set_state(state)

    snapshot = status.snapshot()

    assert snapshot["state"] == str(state)
    assert snapshot["enabled"] is False


def test_configure_lists_repositories_before_they_are_cloned():
    """Test the repositories show up as soon as the config file is read."""
    status = SyncStatus()
    status.configure(["private", "common"], cloned={"common"})

    snapshot = status.snapshot()

    assert snapshot["state"] == "starting"
    assert snapshot["repositories"]["private"]["state"] == "cloning"
    assert snapshot["repositories"]["common"]["state"] == "syncing"


def test_a_failed_clone_is_reported_with_its_kind():
    """Test a clone that failed is visible, with why, and not as a sync."""
    status = SyncStatus(clock=step_clock())
    status.configure(["private"])
    status.record_clone_failure("private", SyncErrorKind.AUTH_FAILED)

    assert status.snapshot()["repositories"]["private"] == {
        "state": "clone_failed",
        "last_attempt": "2026-10-09T12:00:00+00:00",
        "last_success": None,
        "last_error": {"kind": "auth_failed", "at": "2026-10-09T12:00:00+00:00"},
    }


def test_a_clone_that_succeeds_later_is_syncing():
    """Test a retried clone moves the repository on."""
    status = SyncStatus()
    status.configure(["private"])
    status.record_clone_failure("private", SyncErrorKind.NETWORK)
    status.record_cloned("private")

    assert status.snapshot()["repositories"]["private"]["state"] == "syncing"


def test_enable_keeps_a_failed_clone_failed():
    """Test starting the scheduler does not hide a clone that failed."""
    status = SyncStatus()
    status.configure(["private"])
    status.record_clone_failure("private", SyncErrorKind.NETWORK)
    status.enable(["private"])

    assert status.snapshot()["state"] == "running"
    assert status.snapshot()["repositories"]["private"]["state"] == "clone_failed"
