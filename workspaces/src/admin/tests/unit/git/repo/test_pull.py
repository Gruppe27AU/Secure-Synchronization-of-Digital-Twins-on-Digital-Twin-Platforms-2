"""
Unit tests for :mod:`admin.git.repo.pull`.

Real git is never invoked: :class:`tests.helpers.fake_git.FakeGit` answers
every command.
"""


import pytest

from admin.git.repo.pull import pull_changes
from admin.git.run.errors import SyncError, SyncErrorKind
from tests.helpers.fake_git import FakeGit, install_fake_git


def test_pull_changes_does_nothing_when_already_up_to_date(monkeypatch, repo):
    """Test no merge is attempted when the remote has nothing new."""
    fake_git = install_fake_git(monkeypatch, FakeGit(incoming=0))

    assert pull_changes(repo) is False
    assert fake_git.keys == ["fetch", "ref", "incoming"]


def test_pull_changes_merges_cleanly_when_there_is_no_conflict(
    monkeypatch, repo, caplog
):
    """Test a clean merge needs only one merge command."""
    fake_git = install_fake_git(monkeypatch, FakeGit(incoming=2))

    with caplog.at_level("INFO"):
        assert pull_changes(repo) is True

    assert fake_git.keys == ["fetch", "ref", "incoming", "head", "merge"]
    assert "Merged 2 incoming" in caplog.text


def test_pull_changes_aborts_a_conflict_and_names_the_files(monkeypatch, repo):
    """Test a conflict is aborted, not resolved, and every file is named."""
    fake_git = install_fake_git(
        monkeypatch,
        FakeGit(incoming=1, conflicts=["my notes.ipynb"], deleted_locally=["old.txt"]),
    )

    with pytest.raises(SyncError, match=r"my notes\.ipynb, old\.txt") as excinfo:
        pull_changes(repo)

    assert excinfo.value.kind is SyncErrorKind.MERGE_CONFLICT
    assert fake_git.keys[-3:] == ["merge", "unmerged", "merge-abort"]


def test_pull_changes_fetches_the_branch_with_an_explicit_refspec(
    monkeypatch, repo
):
    """Test the fetch names the branch only as a refspec, after ``--``."""
    fake_git = install_fake_git(monkeypatch, FakeGit(incoming=0))

    pull_changes(repo)

    assert fake_git.command_for("fetch")[-3:] == [
        "--",
        "origin",
        "+refs/heads/main:refs/remotes/origin/main",
    ]


def test_pull_changes_raises_when_the_fetch_fails(monkeypatch, repo):
    """Test an unreachable remote is surfaced as SyncError."""
    install_fake_git(monkeypatch, FakeGit(failing="fetch"))

    with pytest.raises(SyncError, match="fetch from the remote") as excinfo:
        pull_changes(repo)

    assert excinfo.value.kind is SyncErrorKind.GIT_FAILED


def test_pull_changes_aborts_even_when_the_conflicts_cannot_be_listed(
    monkeypatch, repo
):
    """Test no half merge is left behind when listing the conflicts fails."""
    fake_git = install_fake_git(
        monkeypatch,
        FakeGit(incoming=1, conflicts=["shared.txt"], failing="unmerged"),
    )

    with pytest.raises(SyncError):
        pull_changes(repo)

    assert fake_git.keys[-2:] == ["unmerged", "merge-abort"]


def test_pull_changes_aborts_a_merge_that_stops_without_a_conflict(
    monkeypatch, repo
):
    """Test a merge git refuses for another reason is aborted and raised."""
    fake_git = install_fake_git(monkeypatch, FakeGit(incoming=1, failing="merge"))

    with pytest.raises(SyncError, match="merge was rejected"):
        pull_changes(repo)

    assert fake_git.keys == [
        "fetch", "ref", "incoming", "head", "merge", "unmerged", "merge-abort"
    ]


def test_fetch_sends_auth_header_without_exposing_the_token(
    monkeypatch, repo_with_credentials
):
    """Test the fetch authenticates the same way the clone and push do."""
    fake_git = install_fake_git(monkeypatch, FakeGit(incoming=0))

    pull_changes(repo_with_credentials)

    assert fake_git.env_for("fetch")["GIT_CONFIG_VALUE_0"].startswith(
        "Authorization: Basic "
    )
    assert not any("extraHeader" in part for part in fake_git.command_for("fetch"))
    assert "secret-token" not in str(fake_git.env_for("fetch"))


def test_pull_changes_does_not_merge_after_a_branch_switch_during_the_fetch(
    monkeypatch, repo
):
    """Test a merge never lands on a branch the user switched to meanwhile."""
    fake_git = install_fake_git(
        monkeypatch,
        FakeGit(incoming=1, head_after_fetch="refs/heads/experiment"),
    )

    with pytest.raises(SyncError) as excinfo:
        pull_changes(repo)

    assert excinfo.value.kind is SyncErrorKind.WRONG_BRANCH
    assert fake_git.keys == ["fetch", "ref", "incoming", "head"]
