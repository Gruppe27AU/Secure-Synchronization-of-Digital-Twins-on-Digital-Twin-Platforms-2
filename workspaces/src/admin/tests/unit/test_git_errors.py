"""
Unit tests for sorting git failures into status kinds.

Covers :mod:`admin.git.errors`: a rejected token, an unreachable remote and
anything else each get their own kind.
"""

import pytest

from admin.git.errors import SyncErrorKind, failure_kind, is_network_failure


@pytest.mark.parametrize(
    "stderr",
    [
        "fatal: unable to access 'https://x/': Could not resolve host: x",
        "fatal: unable to access 'https://x/': Failed to connect to x port 443",
        "fatal: unable to access 'https://x/': Connection timed out",
        "fatal: unable to access 'https://x/': Connection refused",
    ],
)
def test_network_failures_are_recognized(stderr):
    """Test an unreachable remote is told apart from a git failure."""
    assert is_network_failure(stderr)
    assert failure_kind(stderr) is SyncErrorKind.NETWORK


def test_an_auth_failure_wins_over_everything_else():
    """Test a rejected token is reported as such."""
    assert failure_kind(
        "fatal: Authentication failed for 'https://x/'"
    ) is SyncErrorKind.AUTH_FAILED


def test_anything_else_is_a_git_failure():
    """Test an unrecognized failure keeps the general kind."""
    assert failure_kind(
        "fatal: refusing to merge unrelated histories"
    ) is SyncErrorKind.GIT_FAILED
