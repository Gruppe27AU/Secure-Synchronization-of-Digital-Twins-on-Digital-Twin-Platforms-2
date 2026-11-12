"""
Shared HTTP authentication for git commands.

Builds the per-command settings that :mod:`admin.git.repo.clone` and
:mod:`admin.git.repo.sync` hand to ``git`` for every command that contacts the
remote:

- the ``Authorization`` header, passed through git's ``GIT_CONFIG_COUNT``
  environment variables rather than ``-c`` on the command line, so the
  token is never in a process's argument list, where any user of the
  machine can read it, and never written into the repository's own
  ``.git/config`` or remote URL. The header is scoped to the remote's own
  host, so a command that ends up talking to any other host does not send
  it;
- options limiting the command to the https transport, keeping it from
  falling back on a credential helper that might prompt, and giving up on
  a transfer that has stalled.

Also recognizes when a failed git command was rejected because of those
credentials, so callers can raise a clear, token-free error instead of
just forwarding git's raw failure message.
"""

import base64
from urllib.parse import urlsplit

from admin.git.setup.config import RepoConfig

#: Shown to the user when a git command fails and the failure looks like a
#: rejected credential. GitLab (and git's own http backend) give the same
#: message for a token that is simply wrong as for one that has expired,
#: so the two cannot be told apart from the failure text alone.
AUTH_FAILURE_HINT = (
    "the configured GIT_REPO_TOKEN was rejected - it may "
    "be invalid or expired; check config.toml"
)

#: Git options that refuse every transport except https.
PROTOCOL_OPTIONS = [
    "-c",
    "protocol.allow=never",
    "-c",
    "protocol.https.allow=always",
]

#: Git options that stop a command which talks to the remote from waiting
#: on anything but the remote itself. Resetting ``credential.helper`` to an
#: empty list matters because a helper such as Git Credential Manager can
#: prompt even with ``GIT_TERMINAL_PROMPT=0``; credentials only ever come
#: from :func:`auth_environment`. Once the connection is up, the
#: ``lowSpeed`` pair aborts a transfer that has moved less than 1 byte per
#: second for 60 seconds, well before
#: :data:`admin.git.run.runner.REMOTE_TIMEOUT_SECONDS` would. It does not cover
#: a stall while connecting, such as a TLS handshake that never completes;
#: that is left to the time limit.
NO_WAIT_OPTIONS = [
    "-c",
    "credential.helper=",
    "-c",
    "http.lowSpeedLimit=1",
    "-c",
    "http.lowSpeedTime=60",
]

#: Substrings that appear in git/GitLab's own stderr when HTTP credentials
#: are rejected, matched case-insensitively. Deliberately specific (no bare
#: "401"/"403") so an unrelated number in an error message cannot trigger a
#: false positive. With prompts switched off, a 401 to a header git did not
#: build itself ends in git failing to ask for a username instead.
_AUTH_FAILURE_MARKERS = (
    "authentication failed",
    "http basic: access denied",
    "invalid credentials",
    "returned error: 401",
    "returned error: 403",
    "could not read username",
    "could not read password",
)

#: Username sent with the token. HTTP Basic needs one, but the forge
#: identifies the user by the token alone; GitLab documents ``oauth2``.
TOKEN_USERNAME = "oauth2"


def _basic_auth_value(credentials: str) -> str:
    """
    Base64-encode ``username:token`` for an HTTP Basic auth header.

    Args:
        credentials: A ``username:token`` pair.

    Returns:
        The value to place after ``Authorization: Basic``.
    """
    return base64.b64encode(credentials.encode("utf-8")).decode("ascii")


def header_scope(repo_url: str) -> str:
    """
    Return the URL prefix the ``Authorization`` header is limited to.

    The whole host rather than the repository's own path, because a forge
    may redirect within its host, for example from ``.../repo`` to
    ``.../repo.git/``, and the header has to survive that.

    Args:
        repo_url: The repository's https URL.

    Returns:
        The scheme and host with a trailing slash, for example
        ``https://gitlab.com/``.
    """
    parts = urlsplit(repo_url)
    return f"{parts.scheme}://{parts.netloc}/"


def auth_environment(repo: RepoConfig) -> dict[str, str]:
    """
    Build the environment that authenticates a command's requests.

    ``GIT_CONFIG_COUNT`` with ``GIT_CONFIG_KEY_0`` and ``GIT_CONFIG_VALUE_0``
    is git's way of passing one-off settings in the environment.

    Args:
        repo: Repository configuration, possibly without credentials.

    Returns:
        The variables setting ``http.<host>.extraHeader``, or an empty
        mapping when no credentials are configured.
    """
    if not repo.token:
        return {}

    credentials = f"{TOKEN_USERNAME}:{repo.token}"
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"http.{header_scope(repo.repo_url)}.extraHeader",
        "GIT_CONFIG_VALUE_0": (
            f"Authorization: Basic {_basic_auth_value(credentials)}"
        ),
    }


def remote_options() -> list[str]:
    """
    Build the one-off git options for a command that contacts the remote.

    Only https is allowed, so a URL that is not https - ``file://``, a local
    path, ``ssh://`` or ``ext::`` - is refused by git itself, even if it
    got past :mod:`admin.git.setup.config` or was edited into ``.git/config``.
    No credential helper is consulted and a stalled transfer is abandoned
    (:data:`NO_WAIT_OPTIONS`). The credentials are not among them; they
    come from :func:`auth_environment`.

    Returns:
        The protocol restriction and :data:`NO_WAIT_OPTIONS`.
    """
    return [*PROTOCOL_OPTIONS, *NO_WAIT_OPTIONS]


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
