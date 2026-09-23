"""
Shared HTTP authentication for git commands.

Builds the one-off ``-c http.extraHeader=...`` option that
:mod:`admin.git.clone` and :mod:`admin.git.sync` pass to ``git``, so
credentials are authenticated per command without ever being written into
a repository's own ``.git/config`` or embedded in its remote URL. Also
recognizes when a failed git command was rejected because of those
credentials, so callers can raise a clear, token-free error instead of
just forwarding git's raw failure message.
"""

import base64

from admin.git.config import RepoConfig

#: Shown to the user when a git command fails and the failure looks like a
#: rejected credential. GitLab (and git's own http backend) give the same
#: message for a token that is simply wrong as for one that has expired,
#: so the two cannot be told apart from the failure text alone.
AUTH_FAILURE_HINT = (
    "the configured GIT_REPO_USERNAME/GIT_REPO_TOKEN was rejected - it may "
    "be invalid or expired; check config.env"
)

#: Substrings that appear in git/GitLab's own stderr when HTTP credentials
#: are rejected, matched case-insensitively. Deliberately specific (no bare
#: "401"/"403") so an unrelated number in an error message cannot trigger a
#: false positive.
_AUTH_FAILURE_MARKERS = (
    "authentication failed",
    "http basic: access denied",
    "invalid credentials",
    "returned error: 401",
    "returned error: 403",
)


def _basic_auth_value(credentials: str) -> str:
    """
    Base64-encode ``username:token`` for an HTTP Basic auth header.

    Args:
        credentials: A ``username:token`` pair.

    Returns:
        The value to place after ``Authorization: Basic``.
    """
    return base64.b64encode(credentials.encode("utf-8")).decode("ascii")


def auth_header_options(repo: RepoConfig) -> list[str]:
    """
    Build the one-off git options that authenticate a request.

    Args:
        repo: Repository configuration, possibly without credentials.

    Returns:
        The ``-c http.extraHeader=...`` pair, or an empty list when no
        credentials are configured.
    """
    if not repo.username or not repo.token:
        return []

    credentials = f"{repo.username}:{repo.token}"
    return [
        "-c",
        f"http.extraHeader=Authorization: Basic "
        f"{_basic_auth_value(credentials)}",
    ]


def is_auth_failure(stderr: str) -> bool:
    """
    Check whether a failed git command was rejected because of credentials.

    Args:
        stderr: The standard error captured from the failed git command.

    Returns:
        True if the failure looks like a rejected username/token, as
        opposed to, for example, a missing repository or a network error.
    """
    lowered = stderr.lower()
    return any(marker in lowered for marker in _AUTH_FAILURE_MARKERS)
