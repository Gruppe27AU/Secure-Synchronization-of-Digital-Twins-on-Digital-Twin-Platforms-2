"""
Why a git backup step failed, in a form that is safe to show to anyone.

:class:`SyncErrorKind` is what ``/storage/status`` reports. The message of a
:class:`SyncError` can hold git's own output and the names of the user's
files, so it only ever goes to the log; the kind holds neither.

Every rule that reads a kind out of git's output is here:
:func:`failure_kind` for every git command, and :func:`merge_failure_kind`
for a merge refused because of a local file.
"""

from enum import StrEnum

from admin.sync.git.auth import is_auth_failure

#: Substrings of git's and curl's own messages when the remote cannot be
#: reached at all, matched case-insensitively.
_NETWORK_FAILURE_MARKERS = (
    "could not resolve host",
    "could not resolve proxy",
    "failed to connect to",
    "couldn't connect to server",
    "connection refused",
    "connection timed out",
    "connection reset",
    "network is unreachable",
    "no route to host",
    "operation timed out",
)

#: git's reason when ``--force-with-lease`` finds the remote has moved.
_REMOTE_CHANGED_MARKER = "stale info"

#: git's errors when a merge would overwrite a local file.
_LOCAL_CHANGES_MARKERS = ("would be overwritten", "local changes")


class SyncErrorKind(StrEnum):
    """
    Why a clone or synchronization failed, coarse enough to show to anyone.
    """

    AUTH_FAILED = "auth_failed"
    NETWORK = "network"
    TIMEOUT = "timeout"
    GIT_FAILED = "git_failed"
    PULL_FAILED = "pull_failed"
    MERGE_CONFLICT = "merge_conflict"
    REMOTE_CHANGED = "remote_changed"
    OVERWRITE_REFUSED = "overwrite_refused"
    LOCAL_CHANGES = "local_changes"
    OPERATION_IN_PROGRESS = "operation_in_progress"
    WRONG_BRANCH = "wrong_branch"
    DETACHED_HEAD = "detached_head"
    INDEX_LOCKED = "index_locked"
    UNRESOLVED_CONFLICTS = "unresolved_conflicts"
    CONFLICT_MARKERS = "conflict_markers"
    FILE_TOO_LARGE = "file_too_large"
    MASS_DELETION = "mass_deletion"
    UNEXPECTED = "unexpected"


class SyncError(Exception):
    """
    Raised when committing, pulling or pushing a repository fails.

    Attributes:
        kind: Why it failed, safe to expose where the message is not.
        git_output: What git printed, when a git command failed; empty
            otherwise. For telling failures apart, never for display.
    """

    def __init__(
        self,
        message: str,
        kind: SyncErrorKind = SyncErrorKind.GIT_FAILED,
        git_output: str = "",
    ):
        super().__init__(message)
        self.kind = kind
        self.git_output = git_output


def is_network_failure(output: str) -> bool:
    """
    Check whether a failed git command could not reach the remote.

    Args:
        output: What the failed command printed, usually its stderr.

    Returns:
        True if the remote could not be resolved or connected to.
    """
    lowered = output.lower()
    return any(marker in lowered for marker in _NETWORK_FAILURE_MARKERS)


def failure_kind(output: str) -> SyncErrorKind:
    """
    Sort a failed git command by what the user can do about it.

    Args:
        output: What the failed command printed, usually its stderr.

    Returns:
        :attr:`SyncErrorKind.AUTH_FAILED` for a rejected token,
        :attr:`SyncErrorKind.NETWORK` for an unreachable remote,
        :attr:`SyncErrorKind.REMOTE_CHANGED` when ``--force-with-lease``
        found the remote moved, and :attr:`SyncErrorKind.GIT_FAILED` for
        anything else.
    """
    if is_auth_failure(output):
        return SyncErrorKind.AUTH_FAILED
    if is_network_failure(output):
        return SyncErrorKind.NETWORK
    if _REMOTE_CHANGED_MARKER in output.lower():
        return SyncErrorKind.REMOTE_CHANGED
    return SyncErrorKind.GIT_FAILED


def merge_failure_kind(output: str, otherwise: SyncErrorKind) -> SyncErrorKind:
    """Return ``local_changes`` if a merge refused because a local file is in the way."""
    if any(marker in output.lower() for marker in _LOCAL_CHANGES_MARKERS):
        return SyncErrorKind.LOCAL_CHANGES
    return otherwise
