"""
Outcome of the most recent git backups, for the HTTP API to report.

The backup threads write here and request handlers read from here, so every
access goes through one lock. Only states, timestamps and a
:class:`admin.git.errors.SyncErrorKind` are kept, never an error message:
the status is served without authentication, and a message can hold git's
output and the names of the user's files.

The top-level :class:`BackupState` tells a workspace nobody has set up
apart from one whose config file is waiting to be filled in, is broken, or
is being cloned. Each repository is listed from the moment the config file
has been read, with a :class:`RepoState` of its own, so a clone that failed
shows up too.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from admin.git.errors import SyncErrorKind


class BackupState(StrEnum):
    """Where the git backup as a whole stands."""

    #: No config file, and no template to create one from.
    DISABLED = "disabled"
    #: The config file still holds the template's placeholders.
    NEEDS_SETUP = "needs_setup"
    #: The config file cannot be used; the log says why.
    CONFIG_ERROR = "config_error"
    #: The config file is fine and the repositories are being cloned.
    STARTING = "starting"
    #: The repositories are synchronized on every interval.
    RUNNING = "running"


class RepoState(StrEnum):
    """Where one repository stands."""

    #: Not cloned yet.
    CLONING = "cloning"
    #: The last attempt to clone failed; it is tried again every interval.
    CLONE_FAILED = "clone_failed"
    #: Cloned, and synchronized on every interval.
    SYNCING = "syncing"


def _utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None) -> str | None:
    """Format a timestamp for JSON, keeping None as None."""
    return moment.isoformat(timespec="seconds") if moment else None


@dataclass(frozen=True)
class RepoStatus:
    """
    Backup outcome of one repository.

    The last error is kept after a later success, so comparing
    ``last_success`` with ``last_error_at`` tells whether it still applies.
    """

    state: RepoState = RepoState.SYNCING
    last_attempt: datetime | None = None
    last_success: datetime | None = None
    last_error_kind: SyncErrorKind | None = None
    last_error_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the status as JSON-ready data."""
        last_error = None
        if self.last_error_kind is not None:
            last_error = {
                "kind": str(self.last_error_kind),
                "at": _iso(self.last_error_at),
            }
        return {
            "state": str(self.state),
            "last_attempt": _iso(self.last_attempt),
            "last_success": _iso(self.last_success),
            "last_error": last_error,
        }


class SyncStatus:
    """
    Thread-safe record of how the git backup is doing.

    Starts :attr:`BackupState.DISABLED` with no repositories.
    """

    def __init__(self, clock: Callable[[], datetime] = _utc_now) -> None:
        """
        Args:
            clock: Returns the current time. Replaceable in tests.
        """
        self._clock = clock
        self._lock = threading.Lock()
        self._state = BackupState.DISABLED
        self._repos: dict[str, RepoStatus] = {}

    @property
    def state(self) -> BackupState:
        """Where the git backup as a whole stands."""
        with self._lock:
            return self._state

    def set_state(self, state: BackupState) -> None:
        """
        Record where the git backup as a whole stands.

        Args:
            state: The new state.
        """
        with self._lock:
            self._state = state

    def configure(self, repo_names: list[str], cloned: set[str] | None = None) -> None:
        """
        List the configured repositories, and mark the backup as starting.

        Args:
            repo_names: Names of the configured repositories.
            cloned: Those of them already cloned. The others are listed as
                :attr:`RepoState.CLONING`.
        """
        cloned = cloned or set()
        with self._lock:
            self._state = BackupState.STARTING
            for name in repo_names:
                state = RepoState.SYNCING if name in cloned else RepoState.CLONING
                current = self._repos.get(name, RepoStatus())
                self._repos[name] = replace(current, state=state)

    def enable(self, repo_names: list[str]) -> None:
        """
        Mark the backup as running for the given repositories.

        Args:
            repo_names: Names of the repositories being synchronized. Each
                is listed from now on; one not listed before is taken to
                be cloned, with no attempt recorded yet.
        """
        with self._lock:
            self._state = BackupState.RUNNING
            for name in repo_names:
                self._repos.setdefault(name, RepoStatus())

    def record_cloned(self, repo_name: str) -> None:
        """
        Record that a repository has been cloned.

        Args:
            repo_name: Name of the repository.
        """
        with self._lock:
            current = self._repos.get(repo_name, RepoStatus())
            self._repos[repo_name] = replace(current, state=RepoState.SYNCING)

    def record_clone_failure(self, repo_name: str, kind: SyncErrorKind) -> None:
        """
        Record that cloning a repository failed.

        Args:
            repo_name: Name of the repository.
            kind: Why it failed.
        """
        self._record_failure(repo_name, kind, RepoState.CLONE_FAILED)

    def record_success(self, repo_name: str) -> None:
        """
        Record that a repository was synchronized.

        Args:
            repo_name: Name of the repository.
        """
        with self._lock:
            now = self._clock()
            current = self._repos.get(repo_name, RepoStatus())
            self._repos[repo_name] = replace(
                current,
                state=RepoState.SYNCING,
                last_attempt=now,
                last_success=now,
            )

    def record_failure(self, repo_name: str, kind: SyncErrorKind) -> None:
        """
        Record that synchronizing a repository failed.

        Args:
            repo_name: Name of the repository.
            kind: Why it failed.
        """
        self._record_failure(repo_name, kind, RepoState.SYNCING)

    def _record_failure(
        self, repo_name: str, kind: SyncErrorKind, state: RepoState
    ) -> None:
        """Record a failed attempt of either kind."""
        with self._lock:
            now = self._clock()
            current = self._repos.get(repo_name, RepoStatus())
            self._repos[repo_name] = replace(
                current,
                state=state,
                last_attempt=now,
                last_error_kind=kind,
                last_error_at=now,
            )

    def snapshot(self) -> dict[str, Any]:
        """
        Return a consistent copy of the status as JSON-ready data.

        Returns:
            ``state``; ``enabled``, true while the backup is running; and,
            per repository name, its state, last attempt, last success and
            last error.
        """
        with self._lock:
            return {
                "state": str(self._state),
                "enabled": self._state is BackupState.RUNNING,
                "repositories": {
                    name: status.to_dict()
                    for name, status in self._repos.items()
                },
            }
