"""
Why a git backup step failed, in a form that is safe to show to anyone.

:class:`SyncErrorKind` is what ``/storage/status`` reports. The message of a
:class:`SyncError` can hold git's own output and the names of the user's
files, so it only ever goes to the log; the kind holds neither.

:func:`failure_kind` sorts a failed git command's output into the kinds a
user can act on: a rejected token, an unreachable remote, or anything else.
"""

from enum import StrEnum

from admin.git.auth import is_auth_failure

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


class SyncErrorKind(StrEnum):
    """
    Why a clone or synchronization failed, coarse enough to show to anyone.
    """

    AUTH_FAILED = "auth_failed"
    NETWORK = "network"
    TIMEOUT = "timeout"
    GIT_FAILED = "git_failed"
    PULL_FAILED = "pull_failed"
    LOCAL_CHANGES = "local_changes"
    OPERATION_IN_PROGRESS = "operation_in_progress"
    WRONG_BRANCH = "wrong_branch"
    DETACHED_HEAD = "detached_head"
    INDEX_LOCKED = "index_locked"
    UNRESOLVED_CONFLICTS = "unresolved_conflicts"
    CONFLICT_MARKERS = "conflict_markers"
    FILE_TOO_LARGE = "file_too_large"
    MASS_DELETION = "mass_deletion"
    RENAMED_ON_CONFLICT = "renamed_on_conflict"
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
        :attr:`SyncErrorKind.NETWORK` for an unreachable remote, and
        :attr:`SyncErrorKind.GIT_FAILED` for anything else.
    """
    if is_auth_failure(output):
        return SyncErrorKind.AUTH_FAILED
    if is_network_failure(output):
        return SyncErrorKind.NETWORK
    return SyncErrorKind.GIT_FAILED
