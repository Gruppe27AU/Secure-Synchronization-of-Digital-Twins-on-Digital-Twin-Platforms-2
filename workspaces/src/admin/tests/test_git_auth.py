"""
Unit tests for shared git HTTP authentication.

Covers :mod:`admin.git.auth`: building the credentials header, leaving it
out when no credentials are configured, and recognizing rejected
credentials in git's stderr without mistaking unrelated failures for them.
"""

import base64
from dataclasses import replace

from admin.git.auth import auth_header_options, is_auth_failure
from tests.repo_factory import make_repo


def test_auth_header_options_empty_without_credentials():
    """Test a repository without credentials gets no auth options."""
    repo = make_repo()

    assert not auth_header_options(repo)


def test_auth_header_options_builds_basic_auth_header():
    """Test the header is a correctly base64-encoded Basic auth value."""
    repo = replace(make_repo(), username="git-user", token="secret-token")

    options = auth_header_options(repo)

    assert options[0] == "-c"
    expected = base64.b64encode(b"git-user:secret-token").decode("ascii")
    assert options[1] == f"http.extraHeader=Authorization: Basic {expected}"
    # The token must never appear in plain text in the built options.
    assert "secret-token" not in options[1]


def test_auth_header_options_empty_when_only_username_set():
    """Test a username without a token still yields no auth options."""
    repo = replace(make_repo(), username="git-user", token="")

    assert not auth_header_options(repo)


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
