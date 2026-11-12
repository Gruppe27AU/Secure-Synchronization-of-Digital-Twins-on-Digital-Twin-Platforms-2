"""
Unit tests for shared git HTTP authentication.

Covers :mod:`admin.git.run.auth`: building the credentials header, leaving it
out when no credentials are configured, keeping it off the command line and
limited to the remote's host, restricting remote commands to https without
waiting on credential helpers or stalled transfers, and recognizing rejected
credentials in git's stderr without mistaking unrelated failures for them.
"""

import base64
from dataclasses import replace

import pytest

from admin.git.run.auth import (
    NO_WAIT_OPTIONS,
    auth_environment,
    header_scope,
    is_auth_failure,
    remote_options,
)
from tests.helpers.repo_factory import make_repo


def test_auth_environment_empty_without_credentials():
    """Test a repository without credentials gets no auth settings."""
    assert not auth_environment(make_repo())


def test_auth_environment_builds_basic_auth_header():
    """Test the header is a correctly base64-encoded Basic auth value."""
    repo = replace(make_repo(), token="secret-token")

    env = auth_environment(repo)

    expected = base64.b64encode(b"oauth2:secret-token").decode("ascii")
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {expected}"
    # The token must never appear in plain text in the built settings.
    assert "secret-token" not in "".join(env.values())


def test_auth_header_is_scoped_to_the_remote_host():
    """Test a request to any other host does not get the header."""
    repo = replace(
        make_repo(repo_url="https://gitlab.example.org:8443/me/priv.git"),
        token="secret-token",
    )

    assert auth_environment(repo)["GIT_CONFIG_KEY_0"] == (
        "http.https://gitlab.example.org:8443/.extraHeader"
    )


@pytest.mark.parametrize(
    ("url", "scope"),
    [
        ("https://gitlab.com/me/priv.git", "https://gitlab.com/"),
        ("https://gitlab.com/me/priv", "https://gitlab.com/"),
        ("https://host:8443/a/b/c.git", "https://host:8443/"),
    ],
)
def test_header_scope_is_scheme_and_host(url, scope):
    """Test the scope survives a redirect within the host."""
    assert header_scope(url) == scope


def test_remote_options_restrict_to_https():
    """Test the protocol restriction is applied."""
    assert remote_options() == [
        "-c",
        "protocol.allow=never",
        "-c",
        "protocol.https.allow=always",
        *NO_WAIT_OPTIONS,
    ]


def test_remote_options_never_wait_for_a_helper_or_a_stalled_transfer():
    """Test credential helpers are switched off and stalls are abandoned."""
    options = remote_options()

    # An empty value resets the list, so no helper configured on the
    # machine (such as Git Credential Manager) is ever asked.
    assert "credential.helper=" in options
    assert "http.lowSpeedLimit=1" in options
    assert "http.lowSpeedTime=60" in options


def test_is_auth_failure_recognizes_gitlab_access_denied():
    """Test GitLab's 'HTTP Basic: Access denied' rejection is recognized."""
    stderr = (
        "remote: HTTP Basic: Access denied. The provided password or token "
        "is incorrect or your account has 2FA enabled\n"
        "fatal: Authentication failed for 'https://gitlab.com/org/repo.git/'"
    )

    assert is_auth_failure(stderr) is True


def test_is_auth_failure_recognizes_http_403():
    """Test a bare 'returned error: 403' from git's http backend is recognized."""
    stderr = (
        "fatal: unable to access 'https://gitlab.com/org/repo.git/': "
        "The requested URL returned error: 403"
    )

    assert is_auth_failure(stderr) is True


def test_is_auth_failure_recognizes_http_401():
    """Test a bare 'returned error: 401' from git's http backend is recognized."""
    stderr = (
        "fatal: unable to access 'https://gitlab.com/org/repo.git/': "
        "The requested URL returned error: 401"
    )

    assert is_auth_failure(stderr) is True


def test_is_auth_failure_is_case_insensitive():
    """Test the check does not depend on the casing git happens to use."""
    assert is_auth_failure("FATAL: AUTHENTICATION FAILED") is True


def test_is_auth_failure_false_for_unrelated_errors():
    """Test failures unrelated to credentials are not mistaken for auth errors."""
    assert is_auth_failure("fatal: repository 'https://x/y.git' not found") is False
    assert is_auth_failure("fatal: unable to access: Could not resolve host") is False


def test_is_auth_failure_does_not_match_bare_numbers():
    """Test an unrelated message that happens to contain '401' is not flagged."""
    assert is_auth_failure("fatal: exit code 401 from hook") is False


def test_is_auth_failure_recognizes_a_401_git_could_not_answer():
    """Test the 401 of an expired token, with prompts off, counts as rejected.

    The forge answers 401 to the header, git wants to ask for a username
    instead, and cannot, because prompts are switched off.
    """
    stderr = (
        "fatal: could not read Username for 'https://gitlab.com': "
        "terminal prompts disabled"
    )

    assert is_auth_failure(stderr) is True
