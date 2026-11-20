"""
Unit tests for :mod:`admin.sync.git.push`.

Real git is never invoked: :class:`tests.helpers.fake_git.FakeGit` answers
every command.
"""

import subprocess
from datetime import datetime, timezone

import pytest

from admin.sync.git import command as command_module
from admin.sync.git import push as push_module
from admin.sync.git.errors import SyncError, SyncErrorKind
from admin.sync.git.push import (
    overwrite_remote,
    push_if_ahead,
    remote_backup_branch,
)
from tests.helpers.fake_git import FAKE_COMMIT, FakeGit, command_key, install_fake_git


def test_push_if_ahead_pushes_when_the_remote_is_behind(monkeypatch, repo):
    """Test the branch is pushed when it holds commits the remote lacks."""
    fake_git = install_fake_git(monkeypatch, FakeGit(outgoing=2))

    assert push_if_ahead(repo) is True
    assert fake_git.command_for("push")[-3:] == [
        "--",
        "origin",
        "refs/heads/main:refs/heads/main",
    ]


def test_push_if_ahead_skips_when_the_remote_is_level(monkeypatch, repo):
    """Test nothing is pushed when the remote already has everything."""
    fake_git = install_fake_git(monkeypatch, FakeGit(outgoing=0))

    assert push_if_ahead(repo) is False
    assert fake_git.command_for("push") is None


def test_push_if_ahead_raises_when_the_push_fails(monkeypatch, repo):
    """Test a rejected push is surfaced as SyncError."""
    install_fake_git(monkeypatch, FakeGit(outgoing=1, failing="push"))

    with pytest.raises(SyncError, match="push changes"):
        push_if_ahead(repo)


def _fake_run_push_auth_failure(auth_failure_stderr):
    """Build a fake subprocess.run: one outgoing commit, then a failed push."""

    def fake_run(command, _timeout, **_kwargs):
        key = command_key(command)
        if key == "push":
            return subprocess.CompletedProcess(command, 1, "", auth_failure_stderr)
        if key == "outgoing":
            return subprocess.CompletedProcess(command, 0, "1", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    return fake_run


def test_push_if_ahead_raises_clear_error_on_invalid_token(
    monkeypatch, repo_with_credentials
):
    """Test a rejected push with an invalid token gets a clear hint, no token."""
    auth_failure_stderr = (
        "remote: HTTP Basic: Access denied. The provided password or "
        "token is incorrect or your account has 2FA enabled\n"
        "fatal: Authentication failed for 'https://example.com/org/repo.git/'"
    )
    monkeypatch.setattr(
        command_module, "run_git", _fake_run_push_auth_failure(auth_failure_stderr)
    )

    with pytest.raises(SyncError) as excinfo:
        push_if_ahead(repo_with_credentials)

    message = str(excinfo.value)
    assert "invalid or expired" in message
    assert "secret-token" not in message
    assert excinfo.value.kind is SyncErrorKind.AUTH_FAILED


def test_push_if_ahead_raises_plain_error_without_credentials_on_auth_shaped_failure(
    monkeypatch, repo
):
    """Test an auth-shaped push failure without credentials gets no hint."""
    auth_failure_stderr = (
        "fatal: Authentication failed for 'https://example.com/org/repo.git/'"
    )
    monkeypatch.setattr(
        command_module, "run_git", _fake_run_push_auth_failure(auth_failure_stderr)
    )

    with pytest.raises(SyncError) as excinfo:
        push_if_ahead(repo)

    assert "invalid or expired" not in str(excinfo.value)
    # Still an auth failure, even with no hint to give.
    assert excinfo.value.kind is SyncErrorKind.AUTH_FAILED


def test_push_sends_auth_header_without_exposing_the_token(
    monkeypatch, repo_with_credentials
):
    """Test the push authenticates with a header, not a token in the URL."""
    fake_git = install_fake_git(monkeypatch, FakeGit(outgoing=1))

    push_if_ahead(repo_with_credentials)

    assert fake_git.env_for("push")["GIT_CONFIG_VALUE_0"].startswith(
        "Authorization: Basic "
    )
    assert not any("extraHeader" in part for part in fake_git.command_for("push"))
    assert "secret-token" not in str(fake_git.env_for("push"))


def test_push_has_no_auth_header_without_credentials(monkeypatch, repo):
    """Test an unauthenticated repository is pushed without a header."""
    fake_git = install_fake_git(monkeypatch, FakeGit(outgoing=1))

    push_if_ahead(repo)

    assert not fake_git.env_for("push")
    assert not any(
        "http.extraHeader" in part for part in fake_git.command_for("push")
    )


def test_push_if_ahead_counts_the_configured_branch_not_head(monkeypatch, repo):
    """Test what is counted is what the push sends, whatever is checked out."""
    fake_git = install_fake_git(monkeypatch, FakeGit(outgoing=1))

    push_if_ahead(repo)

    assert fake_git.command_for("outgoing")[-1] == "origin/main..refs/heads/main"


def test_an_unborn_branch_has_nothing_to_push(monkeypatch, repo):
    """Test a repository started on an empty remote with no files yet is left alone."""
    fake_git = install_fake_git(
        monkeypatch, FakeGit(missing_refs={"refs/heads/main"}, outgoing=1)
    )

    assert push_if_ahead(repo) is False
    assert "push" not in fake_git.keys


def test_overwrite_remote_backs_up_and_replaces_in_one_atomic_push(monkeypatch, repo):
    """Test the remote's commit is saved and the branch forced only under a lease."""
    moment = datetime(2026, 11, 19, 10, 15, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(push_module, "utc_now", lambda: moment)
    fake_git = install_fake_git(monkeypatch, FakeGit())

    branch = overwrite_remote(repo)

    assert branch == f"remote-backup/2026-11-19_10-15-30-UTC-{FAKE_COMMIT[:7]}"
    command = fake_git.command_for("overwrite-push")
    assert "--atomic" in command
    assert f"--force-with-lease=refs/heads/main:{FAKE_COMMIT}" in command
    assert command[-4:] == [
        "--",
        "origin",
        f"{FAKE_COMMIT}:refs/heads/{branch}",
        "refs/heads/main:refs/heads/main",
    ]
    # The lease holds the commit that was fetched, not whatever origin/main
    # becomes later.
    assert fake_git.command_for("ref")[-1] == "refs/remotes/origin/main^{commit}"


def test_each_overwrite_gets_a_branch_of_its_own(monkeypatch):
    """Test a later conflict never replaces the remote's earlier backup."""
    moments = iter(
        [
            datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 19, 10, 5, 0, tzinfo=timezone.utc),
        ]
    )
    monkeypatch.setattr(push_module, "utc_now", lambda: next(moments))

    assert remote_backup_branch(FAKE_COMMIT) != remote_backup_branch(FAKE_COMMIT)


def test_two_versions_saved_in_the_same_second_get_branches_of_their_own(monkeypatch):
    """Test the hash keeps two workspaces' conflicts apart within one second."""
    moment = datetime(2026, 11, 19, 10, 15, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(push_module, "utc_now", lambda: moment)

    assert remote_backup_branch("a1b2c3d" + "0" * 33) == (
        "remote-backup/2026-11-19_10-15-30-UTC-a1b2c3d"
    )
    assert remote_backup_branch("a1b2c3d" + "0" * 33) != remote_backup_branch(
        "e4f5a6b" + "0" * 33
    )


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        (" ! [rejected] main -> main (stale info)", SyncErrorKind.REMOTE_CHANGED),
        (
            "remote: error: GH006: Protected branch update failed",
            SyncErrorKind.OVERWRITE_REFUSED,
        ),
        ("fatal: Authentication failed for 'https://x/'", SyncErrorKind.AUTH_FAILED),
        ("fatal: unable to access 'https://x/': Connection refused", SyncErrorKind.NETWORK),
    ],
    ids=["remote-moved", "protected", "auth", "network"],
)
def test_a_failed_overwrite_keeps_a_kind_the_user_can_act_on(
    monkeypatch, repo, message, kind
):
    """Test a moved remote, a protected branch, a token and the network are told apart."""
    install_fake_git(
        monkeypatch, FakeGit(failing="overwrite-push", failure_message=message)
    )

    with pytest.raises(SyncError, match="branch main") as excinfo:
        overwrite_remote(repo)

    assert excinfo.value.kind is kind
