"""
Unit tests for sorting git failures into status kinds.

Covers :mod:`admin.sync.git.errors`: a rejected token, an unreachable remote,
a moved remote, a local file in the way of a merge and anything else each get
their own kind.
"""

import pytest

from admin.sync.git.errors import (
    SyncErrorKind,
    failure_kind,
    is_network_failure,
    merge_failure_kind,
)


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


def test_a_stale_lease_means_the_remote_changed():
    """Test --force-with-lease refusing a moved remote is told apart."""
    assert failure_kind(
        " ! [rejected]        main -> main (stale info)"
    ) is SyncErrorKind.REMOTE_CHANGED


@pytest.mark.parametrize(
    "output",
    [
        "error: Your local changes to the following files would be overwritten by merge",
        "error: The following untracked working tree files would be overwritten by merge",
    ],
)
def test_a_local_file_in_the_way_of_a_merge_is_named(output):
    """Test a merge refused for a local file reports local_changes."""
    assert merge_failure_kind(output, SyncErrorKind.GIT_FAILED) is SyncErrorKind.LOCAL_CHANGES


def test_another_merge_failure_keeps_the_given_kind():
    """Test the caller's kind is kept when no local file is in the way."""
    output = "fatal: refusing to merge unrelated histories"

    assert merge_failure_kind(output, SyncErrorKind.PULL_FAILED) is SyncErrorKind.PULL_FAILED
