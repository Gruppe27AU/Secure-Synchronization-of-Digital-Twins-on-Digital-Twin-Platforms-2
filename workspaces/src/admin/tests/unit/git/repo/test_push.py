"""
Unit tests for :mod:`admin.git.repo.push`.

Real git is never invoked: :class:`tests.helpers.fake_git.FakeGit` answers
every command.
"""

import subprocess

import pytest

from admin.git.repo.push import push_if_ahead, push_to_backup_branch
from admin.git.run import command as command_module
from admin.git.run.errors import SyncError, SyncErrorKind
from tests.helpers.fake_git import command_key, FakeGit, install_fake_git


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


def test_no_backup_before_the_branch_is_on_the_remote(monkeypatch, repo):
    """Test an empty remote gets the configured branch first, so it becomes the default."""
    fake_git = install_fake_git(
        monkeypatch, FakeGit(missing_refs={"refs/remotes/origin/main"})
    )

    assert push_to_backup_branch(repo) is None
    assert "backup-push" not in fake_git.keys


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("! [remote rejected] (pre-receive hook declined)", SyncErrorKind.BACKUP_FAILED),
        ("fatal: Authentication failed for 'https://x/'", SyncErrorKind.AUTH_FAILED),
        ("fatal: unable to access 'https://x/': Connection refused", SyncErrorKind.NETWORK),
    ],
)
def test_a_failed_backup_push_keeps_a_kind_the_user_can_act_on(
    monkeypatch, repo, message, kind
):
    """Test only a refused push is backup_failed; a token or network failure stays itself."""
    repo.git_dir.mkdir(parents=True)
    install_fake_git(monkeypatch, FakeGit(failing="backup-push", failure_message=message))

    with pytest.raises(SyncError, match="backup branch") as excinfo:
        push_to_backup_branch(repo)

    assert excinfo.value.kind is kind
