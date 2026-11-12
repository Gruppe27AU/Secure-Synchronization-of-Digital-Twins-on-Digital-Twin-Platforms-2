"""
HTTP authentication and transport options for git commands that reach the remote.

The ``Authorization`` header goes in git's environment (``GIT_CONFIG_COUNT``),
never on the command line or into ``.git/config``, and only to the
repository's own URL. Also recognises a rejected token in git's output.
"""

import base64

from admin.git.setup.config import RepoConfig

#: Added to the error when git's output looks like a rejected token. Forges
#: give the same message for a wrong and an expired token.
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

#: Git options so a command waits on nothing but the remote: no credential
#: helper (one may prompt even with prompts off), and a transfer below 1 byte
#: per second for 60 seconds is abandoned.
NO_WAIT_OPTIONS = [
    "-c",
    "credential.helper=",
    "-c",
    "http.lowSpeedLimit=1",
    "-c",
    "http.lowSpeedTime=60",
]

#: Texts in git's and forges' output when credentials are rejected, matched
#: case-insensitively. No bare "401", so an unrelated number cannot match.
_AUTH_FAILURE_MARKERS = (
    "authentication failed",
    "http basic: access denied",
    "invalid credentials",
    "returned error: 401",
    "returned error: 403",
    "could not read username",
    "could not read password",
)

#: Username sent with the token; the forge identifies the user by the token.
TOKEN_USERNAME = "oauth2"


def _basic_auth_value(credentials: str) -> str:
    """Return ``username:token`` Base64-encoded for an HTTP Basic header."""
    return base64.b64encode(credentials.encode("utf-8")).decode("ascii")


def header_scope(repo_url: str) -> str:
    """
    Return the URL the ``Authorization`` header is limited to: the repository's own.

    git matches ``http.<url>.*`` against the remote URL once per command, so
    the header still follows a redirect, but another repository on the same
    host never gets it.
    """
    return repo_url.rstrip("/")


def auth_environment(repo: RepoConfig) -> dict[str, str]:
    """
    Return the variables setting ``http.<url>.extraHeader``; empty without a token.
    """
    if not repo.token:
        return {}
    credentials = f"{TOKEN_USERNAME}:{repo.token}"
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"http.{header_scope(repo.repo_url)}.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {_basic_auth_value(credentials)}",
    }


def remote_options() -> list[str]:
    """Return :data:`PROTOCOL_OPTIONS` and :data:`NO_WAIT_OPTIONS`."""
    return [*PROTOCOL_OPTIONS, *NO_WAIT_OPTIONS]


def is_auth_failure(stderr: str) -> bool:
    """Check whether git's output looks like rejected credentials."""
    lowered = stderr.lower()
    return any(marker in lowered for marker in _AUTH_FAILURE_MARKERS)
