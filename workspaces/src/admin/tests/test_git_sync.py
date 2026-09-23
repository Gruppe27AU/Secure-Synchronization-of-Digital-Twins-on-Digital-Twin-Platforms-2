"""
Unit tests for committing and pushing workspace changes.

Covers :mod:`admin.git.sync`: skipping a clean working tree, committing
and pushing a dirty one, surfacing a failed push as
:class:`admin.git.sync.SyncError`, and the two settings the sync has to
supply itself because cloning leaves them out - the commit identity and
the credentials header.
"""

import re
import subprocess
from dataclasses import replace

import pytest

from admin.git.sync import SyncError, build_commit_message, has_changes, sync_once
from tests.repo_factory import make_repo

#: Git subcommands the fake recognises, used to tell the calls apart.
GIT_SUBCOMMANDS = ("status", "add", "commit", "push")


@pytest.fixture(name="repo")
def fixture_repo(tmp_path):
    """A RepoConfig for an already-cloned repository, without credentials."""
    return make_repo(
        git_dir=tmp_path / "workspace" / "common",
        work_tree=tmp_path / "app" / "assets" / "common",
    )


@pytest.fixture(name="repo_with_credentials")
def fixture_repo_with_credentials(repo):
    """The same repository, but with a username and token configured."""
    return replace(repo, username="git-user", token="secret-token")


def _subcommand(command):
    """Return the git subcommand in a command list, for example 'push'."""
    for part in command:
        if part in GIT_SUBCOMMANDS:
            return part
    return None


class FakeGit:
    """
    Stands in for ``subprocess.run`` and records every git call.

    Args:
        status_output: What ``git status --porcelain`` should print. An
            empty string means the working tree is clean.
        failing: Subcommand that should fail, for example ``"push"``.
    """

    def __init__(self, status_output="", failing=None):
        self.status_output = status_output
        self.failing = failing
        self.commands = []

    def run(self, command, **_kwargs):
        """Record the command and return a canned result."""
        self.commands.append(command)
        subcommand = _subcommand(command)

        if subcommand == self.failing:
            return subprocess.CompletedProcess(
                command, 1, "", f"fatal: {subcommand} was rejected"
            )

        stdout = self.status_output if subcommand == "status" else ""
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def command_for(self, subcommand):
        """Return the recorded command for a subcommand, or None."""
        for command in self.commands:
            if _subcommand(command) == subcommand:
                return command
        return None

    @property
    def subcommands(self):
        """The subcommands that were run, in order."""
        return [_subcommand(command) for command in self.commands]


def _install(monkeypatch, fake_git):
    """Make admin.git.sync use the fake instead of real git."""
    monkeypatch.setattr(subprocess, "run", fake_git.run)
    return fake_git


def test_sync_once_commits_and_pushes_when_there_are_changes(monkeypatch, repo):
    """Test a dirty working tree is staged, committed and pushed."""
    fake_git = _install(monkeypatch, FakeGit(status_output=" M notebook.ipynb"))

    assert sync_once(repo) is True
    assert fake_git.subcommands == ["status", "add", "commit", "push"]


def test_sync_once_skips_when_there_are_no_changes(monkeypatch, repo):
    """Test a clean working tree is left alone entirely."""
    fake_git = _install(monkeypatch, FakeGit(status_output=""))

    assert sync_once(repo) is False
    assert fake_git.subcommands == ["status"]


def test_sync_once_raises_when_push_fails(monkeypatch, repo):
    """Test a rejected push is surfaced as SyncError."""
    _install(monkeypatch, FakeGit(status_output=" M file.txt", failing="push"))

    with pytest.raises(SyncError, match="push was rejected"):
        sync_once(repo)


def test_sync_once_raises_when_commit_fails(monkeypatch, repo):
    """Test a failed commit is surfaced as SyncError and stops the push."""
    fake_git = _install(
        monkeypatch, FakeGit(status_output=" M file.txt", failing="commit")
    )

    with pytest.raises(SyncError, match="commit was rejected"):
        sync_once(repo)

    assert "push" not in fake_git.subcommands


def test_has_changes_raises_when_status_fails(monkeypatch, repo):
    """Test an unreadable working tree is surfaced as SyncError."""
    _install(monkeypatch, FakeGit(failing="status"))

    with pytest.raises(SyncError, match="read the status"):
        has_changes(repo)


def test_commit_message_contains_a_timestamp():
    """Test the commit message ends in a UTC timestamp."""
    assert re.fullmatch(
        r"workspace backup \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        build_commit_message(),
    )


def test_commit_sets_an_author_identity(monkeypatch, repo):
    """Test the commit carries an identity, which cloning does not set."""
    fake_git = _install(monkeypatch, FakeGit(status_output=" M file.txt"))

    sync_once(repo)

    commit_command = fake_git.command_for("commit")
    assert "user.name=workspace-admin" in commit_command
    assert "user.email=workspace-admin@localhost" in commit_command


def _fake_run_push_auth_failure(status_output, auth_failure_stderr):
    """Build a fake subprocess.run that fails only the push, on an auth error."""

    def fake_run(command, **_kwargs):
        subcommand = _subcommand(command)
        if subcommand == "push":
            return subprocess.CompletedProcess(command, 1, "", auth_failure_stderr)
        stdout = status_output if subcommand == "status" else ""
        return subprocess.CompletedProcess(command, 0, stdout, "")

    return fake_run


def test_sync_once_raises_clear_error_on_invalid_token(
    monkeypatch, repo_with_credentials
):
    """Test a rejected push with an invalid token gets a clear hint, no token."""
    auth_failure_stderr = (
        "remote: HTTP Basic: Access denied. The provided password or "
        "token is incorrect or your account has 2FA enabled\n"
        "fatal: Authentication failed for 'https://example.com/org/repo.git/'"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run_push_auth_failure(" M file.txt", auth_failure_stderr),
    )

    with pytest.raises(SyncError) as excinfo:
        sync_once(repo_with_credentials)

    message = str(excinfo.value)
    assert "invalid or expired" in message
    assert "secret-token" not in message


def test_sync_once_raises_plain_error_without_credentials_on_auth_shaped_failure(
    monkeypatch, repo
):
    """Test an auth-shaped push failure without credentials gets no hint."""
    auth_failure_stderr = (
        "fatal: Authentication failed for 'https://example.com/org/repo.git/'"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run_push_auth_failure(" M file.txt", auth_failure_stderr),
    )

    with pytest.raises(SyncError) as excinfo:
        sync_once(repo)

    assert "invalid or expired" not in str(excinfo.value)


def test_push_sends_auth_header_without_exposing_the_token(
    monkeypatch, repo_with_credentials
):
    """Test the push authenticates with a header, not a token in the URL."""
    fake_git = _install(monkeypatch, FakeGit(status_output=" M file.txt"))

    sync_once(repo_with_credentials)

    push_command = fake_git.command_for("push")
    assert any(
        part.startswith("http.extraHeader=Authorization: Basic ")
        for part in push_command
    )
    # The token must never appear in plain text in the command.
    assert "secret-token" not in " ".join(push_command)


def test_push_has_no_auth_header_without_credentials(monkeypatch, repo):
    """Test an unauthenticated repository is pushed without a header."""
    fake_git = _install(monkeypatch, FakeGit(status_output=" M file.txt"))

    sync_once(repo)

    push_command = fake_git.command_for("push")
    assert not any("http.extraHeader" in part for part in push_command)


def test_push_targets_the_configured_branch(monkeypatch, repo):
    """Test the push goes to the branch from the config file."""
    fake_git = _install(monkeypatch, FakeGit(status_output=" M file.txt"))

    sync_once(repo)

    assert fake_git.command_for("push")[-2:] == ["origin", repo.branch]
