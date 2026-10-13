"""
Outcome of the most recent git backups, for the HTTP API to report.

The sync thread writes here and request handlers read from here, so every
access goes through one lock. Only timestamps and a
:class:`admin.git.sync.SyncErrorKind` are kept, never an error message:
the status is served without authentication, and a message can hold git's
output and the names of the user's files.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from admin.git.sync import SyncErrorKind


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
            "last_attempt": _iso(self.last_attempt),
            "last_success": _iso(self.last_success),
            "last_error": last_error,
        }


class SyncStatus:
    """
    Thread-safe record of how the git backup is doing.

    Starts disabled with no repositories. :meth:`enable` is called once the
    scheduler has repositories to synchronize.
    """

    def __init__(self, clock: Callable[[], datetime] = _utc_now) -> None:
        """
        Args:
            clock: Returns the current time. Replaceable in tests.
        """
        self._clock = clock
        self._lock = threading.Lock()
        self._enabled = False
        self._repos: dict[str, RepoStatus] = {}

    def enable(self, repo_names: list[str]) -> None:
        """
        Mark the backup as running for the given repositories.

        Args:
            repo_names: Names of the repositories being synchronized. Each
                is listed from now on, with no attempt recorded yet.
        """
        with self._lock:
            self._enabled = True
            for name in repo_names:
                self._repos.setdefault(name, RepoStatus())

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
                current, last_attempt=now, last_success=now
            )

    def record_failure(self, repo_name: str, kind: SyncErrorKind) -> None:
        """
        Record that synchronizing a repository failed.

        Args:
            repo_name: Name of the repository.
            kind: Why it failed.
        """
        with self._lock:
            now = self._clock()
            current = self._repos.get(repo_name, RepoStatus())
            self._repos[repo_name] = replace(
                current,
                last_attempt=now,
                last_error_kind=kind,
                last_error_at=now,
            )

    def snapshot(self) -> dict[str, Any]:
        """
        Return a consistent copy of the status as JSON-ready data.

        Returns:
            ``enabled`` and, per repository name, its last attempt, last
            success and last error.
        """
        with self._lock:
            return {
                "enabled": self._enabled,
                "repositories": {
                    name: status.to_dict()
                    for name, status in self._repos.items()
                },
            }
