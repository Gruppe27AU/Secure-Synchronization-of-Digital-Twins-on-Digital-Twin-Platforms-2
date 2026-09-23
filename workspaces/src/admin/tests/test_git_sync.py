"""
Unit tests for synchronizing a workspace repository with its remote.

Covers :mod:`admin.git.sync`: committing what the user changed, merging
the remote's work in, keeping local files when the two sides collide, and
pushing the result. Real git is never invoked - ``subprocess.run`` is
replaced by :class:`FakeGit`, which records the commands and answers them
with canned results.
"""

import re
import subprocess
from dataclasses import dataclass, field, replace

import pytest

from admin.git.sync import (
    SyncError,
    build_commit_message,
    commit_local_changes,
    has_changes,
    pull_changes,
    push_if_ahead,
    sync_once,
)
from tests.repo_factory import make_repo


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


def _command_key(command):
    """
    Name the git command in a way the tests can assert on.

    The three merges are told apart, because the order of detect, abort
    and resolve is the whole point of the conflict handling.
    """
    for word in ("status", "add", "commit", "push", "fetch"):
        if word in command:
            return word

    if "rev-list" in command:
        return "incoming" if command[-1].startswith("HEAD..") else "outgoing"

    if "merge" in command:
        if "--abort" in command:
            return "merge-abort"
        return "merge-ours" if "ours" in command else "merge"

    if "diff" in command:
        return "conflicted-files"

    return None


@dataclass
class FakeGit:
    """
    Stands in for ``subprocess.run`` and records every git call.

    Args:
        status_output: What ``git status --porcelain`` prints. Empty means
            the working tree is clean.
        incoming: Commits the remote has that the workspace does not.
        outgoing: Commits the workspace has that the remote does not.
        conflicts: Files the first merge attempt cannot resolve. A
            non-empty list makes that attempt fail, as real git does.
        failing: Command key that should fail, for example ``"push"``.
        commands: Every command that was run, filled in as they arrive.
    """

    status_output: str = ""
    incoming: int = 0
    outgoing: int = 0
    conflicts: list = field(default_factory=list)
    failing: str | None = None
    commands: list = field(default_factory=list)

    def run(self, command, **_kwargs):
        """Record the command and answer it."""
        self.commands.append(command)
        key = _command_key(command)

        if key == self.failing:
            return subprocess.CompletedProcess(
                command, 1, "", f"fatal: {key} was rejected"
            )

        # The plain merge is what detects a conflict, so it has to fail
        # exactly as real git does when it cannot resolve one.
        if key == "merge" and self.conflicts:
            return subprocess.CompletedProcess(
                command, 1, "", "CONFLICT (content): Merge conflict"
            )

        return subprocess.CompletedProcess(command, 0, self._stdout(key), "")

    def _stdout(self, key):
        """The output git would print for a command."""
        return {
            "status": self.status_output,
            "incoming": str(self.incoming),
            "outgoing": str(self.outgoing),
            "conflicted-files": "\n".join(self.conflicts),
        }.get(key, "")

    def command_for(self, key):
        """Return the recorded command with this key, or None."""
        for command in self.commands:
            if _command_key(command) == key:
                return command
        return None

    @property
    def keys(self):
        """The commands that were run, in order."""
        return [_command_key(command) for command in self.commands]


def _install(monkeypatch, fake_git):
    """Make admin.git.sync use the fake instead of real git."""
    monkeypatch.setattr(subprocess, "run", fake_git.run)
    return fake_git


# --------------------------------------------------------------------------
# Committing the user's own work
# --------------------------------------------------------------------------


def test_commit_local_changes_commits_a_dirty_working_tree(monkeypatch, repo):
    """Test a modified working tree is staged and committed."""
    fake_git = _install(monkeypatch, FakeGit(status_output=" M notebook.ipynb"))

    assert commit_local_changes(repo) is True
    assert fake_git.keys == ["status", "add", "commit"]


def test_commit_local_changes_skips_a_clean_working_tree(monkeypatch, repo):
    """Test a clean working tree is left alone entirely."""
    fake_git = _install(monkeypatch, FakeGit(status_output=""))

    assert commit_local_changes(repo) is False
    assert fake_git.keys == ["status"]


def test_commit_message_contains_a_timestamp():
    """Test the commit message ends in a UTC timestamp."""
    assert re.fullmatch(
        r"workspace backup \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        build_commit_message(),
    )


def test_commit_sets_an_author_identity(monkeypatch, repo):
    """Test the commit carries an identity, which cloning does not set."""
    fake_git = _install(monkeypatch, FakeGit(status_output=" M file.txt"))

    commit_local_changes(repo)

    commit_command = fake_git.command_for("commit")
    assert "user.name=workspace-admin" in commit_command
    assert "user.email=workspace-admin@localhost" in commit_command


def test_has_changes_raises_when_status_fails(monkeypatch, repo):
    """Test an unreadable working tree is surfaced as SyncError."""
    _install(monkeypatch, FakeGit(failing="status"))

    with pytest.raises(SyncError, match="read the status"):
        has_changes(repo)


# --------------------------------------------------------------------------
# Pulling the remote's work in
# --------------------------------------------------------------------------


def test_pull_changes_does_nothing_when_already_up_to_date(monkeypatch, repo):
    """Test no merge is attempted when the remote has nothing new."""
    fake_git = _install(monkeypatch, FakeGit(incoming=0))

    assert pull_changes(repo) == []
    assert fake_git.keys == ["fetch", "incoming"]


def test_pull_changes_merges_cleanly_when_there_is_no_conflict(
    monkeypatch, repo, caplog
):
    """Test a clean merge reports no kept files and needs only one merge."""
    fake_git = _install(monkeypatch, FakeGit(incoming=2))

    with caplog.at_level("INFO"):
        assert pull_changes(repo) == []

    assert fake_git.keys == ["fetch", "incoming", "merge"]
    assert "without conflicts" in caplog.text


def test_pull_changes_keeps_local_files_on_conflict(monkeypatch, repo):
    """Test a conflict is detected, abandoned, and redone keeping local files."""
    fake_git = _install(
        monkeypatch, FakeGit(incoming=1, conflicts=["shared.txt", "notes.md"])
    )

    assert pull_changes(repo) == ["shared.txt", "notes.md"]
    # Detect, inspect, undo, then resolve - in that order.
    assert fake_git.keys == [
        "fetch",
        "incoming",
        "merge",
        "conflicted-files",
        "merge-abort",
        "merge-ours",
    ]
    assert "ours" in fake_git.command_for("merge-ours")


def test_pull_changes_logs_the_conflict_and_its_resolution(
    monkeypatch, repo, caplog
):
    """Test both the detection and the resolution are logged, by filename."""
    _install(monkeypatch, FakeGit(incoming=1, conflicts=["shared.txt"]))

    with caplog.at_level("INFO"):
        pull_changes(repo)

    assert "Merge conflict" in caplog.text
    assert "keeping the local version" in caplog.text
    assert caplog.text.count("shared.txt") >= 2


def test_pull_changes_raises_when_the_fetch_fails(monkeypatch, repo):
    """Test an unreachable remote is surfaced as SyncError."""
    _install(monkeypatch, FakeGit(failing="fetch"))

    with pytest.raises(SyncError, match="fetch from the remote"):
        pull_changes(repo)


def test_pull_changes_raises_when_the_resolving_merge_fails(monkeypatch, repo):
    """Test a merge that fails even with 'ours' is not swallowed."""
    _install(
        monkeypatch,
        FakeGit(incoming=1, conflicts=["shared.txt"], failing="merge-ours"),
    )

    with pytest.raises(SyncError, match="keeping local files"):
        pull_changes(repo)


def test_fetch_sends_auth_header_without_exposing_the_token(
    monkeypatch, repo_with_credentials
):
    """Test the fetch authenticates the same way the clone and push do."""
    fake_git = _install(monkeypatch, FakeGit(incoming=0))

    pull_changes(repo_with_credentials)

    fetch_command = fake_git.command_for("fetch")
    assert any(
        part.startswith("http.extraHeader=Authorization: Basic ")
        for part in fetch_command
    )
    assert "secret-token" not in " ".join(fetch_command)


# --------------------------------------------------------------------------
# Pushing the result
# --------------------------------------------------------------------------


def test_push_if_ahead_pushes_when_the_remote_is_behind(monkeypatch, repo):
    """Test the branch is pushed when it holds commits the remote lacks."""
    fake_git = _install(monkeypatch, FakeGit(outgoing=2))

    assert push_if_ahead(repo) is True
    assert fake_git.command_for("push")[-2:] == ["origin", repo.branch]


def test_push_if_ahead_skips_when_the_remote_is_level(monkeypatch, repo):
    """Test nothing is pushed when the remote already has everything."""
    fake_git = _install(monkeypatch, FakeGit(outgoing=0))

    assert push_if_ahead(repo) is False
    assert fake_git.command_for("push") is None


def test_push_if_ahead_raises_when_the_push_fails(monkeypatch, repo):
    """Test a rejected push is surfaced as SyncError."""
    _install(monkeypatch, FakeGit(outgoing=1, failing="push"))

    with pytest.raises(SyncError, match="push changes"):
        push_if_ahead(repo)


def _fake_run_push_auth_failure(auth_failure_stderr):
    """Build a fake subprocess.run: one outgoing commit, then a failed push."""

    def fake_run(command, **_kwargs):
        key = _command_key(command)
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
        subprocess, "run", _fake_run_push_auth_failure(auth_failure_stderr)
    )

    with pytest.raises(SyncError) as excinfo:
        push_if_ahead(repo_with_credentials)

    message = str(excinfo.value)
    assert "invalid or expired" in message
    assert "secret-token" not in message


def test_push_if_ahead_raises_plain_error_without_credentials_on_auth_shaped_failure(
    monkeypatch, repo
):
    """Test an auth-shaped push failure without credentials gets no hint."""
    auth_failure_stderr = (
        "fatal: Authentication failed for 'https://example.com/org/repo.git/'"
    )
    monkeypatch.setattr(
        subprocess, "run", _fake_run_push_auth_failure(auth_failure_stderr)
    )

    with pytest.raises(SyncError) as excinfo:
        push_if_ahead(repo)

    assert "invalid or expired" not in str(excinfo.value)


def test_push_sends_auth_header_without_exposing_the_token(
    monkeypatch, repo_with_credentials
):
    """Test the push authenticates with a header, not a token in the URL."""
    fake_git = _install(monkeypatch, FakeGit(outgoing=1))

    push_if_ahead(repo_with_credentials)

    push_command = fake_git.command_for("push")
    assert any(
        part.startswith("http.extraHeader=Authorization: Basic ")
        for part in push_command
    )
    assert "secret-token" not in " ".join(push_command)


def test_push_has_no_auth_header_without_credentials(monkeypatch, repo):
    """Test an unauthenticated repository is pushed without a header."""
    fake_git = _install(monkeypatch, FakeGit(outgoing=1))

    push_if_ahead(repo)

    assert not any(
        "http.extraHeader" in part for part in fake_git.command_for("push")
    )


# --------------------------------------------------------------------------
# The whole cycle
# --------------------------------------------------------------------------


def test_sync_once_commits_before_it_merges(monkeypatch, repo):
    """Test the user's work is committed first, so the merge has a clean tree.

    Git refuses to merge over modified files, so pulling before committing
    would fail as soon as the remote had anything to deliver.
    """
    fake_git = _install(
        monkeypatch, FakeGit(status_output=" M file.txt", incoming=1, outgoing=2)
    )

    assert sync_once(repo) is True
    assert fake_git.keys.index("commit") < fake_git.keys.index("merge")


def test_sync_once_reports_no_change_when_everything_is_in_sync(
    monkeypatch, repo
):
    """Test a clean tree and a level remote produce no commit and no push."""
    fake_git = _install(monkeypatch, FakeGit(status_output="", incoming=0))

    assert sync_once(repo) is False
    assert "commit" not in fake_git.keys
    assert "push" not in fake_git.keys


def test_sync_once_reports_a_change_when_only_the_remote_moved(
    monkeypatch, repo
):
    """Test merging someone else's work counts as a change worth reporting."""
    _install(
        monkeypatch, FakeGit(status_output="", incoming=1, conflicts=["a.txt"])
    )

    assert sync_once(repo) is True


def test_sync_once_raises_when_a_step_fails(monkeypatch, repo):
    """Test a failing step stops the cycle instead of continuing blindly."""
    fake_git = _install(
        monkeypatch, FakeGit(status_output=" M file.txt", failing="commit")
    )

    with pytest.raises(SyncError, match="commit was rejected"):
        sync_once(repo)

    assert "fetch" not in fake_git.keys
