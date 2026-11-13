"""
Unit tests for :mod:`admin.git.repo.busy`.

Real git is never invoked: :class:`tests.helpers.fake_git.FakeGit` answers
every command.
"""

import os
import time

import pytest

from admin.git.repo import busy
from admin.git.repo.busy import ensure_safe_to_sync
from admin.git.repo.commit import commit_local_changes
from admin.git.run.errors import SyncError, SyncErrorKind
from tests.helpers.fake_git import FakeGit, install_fake_git


@pytest.mark.parametrize(
    "marker",
    [
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "REBASE_HEAD",
        "rebase-merge",
        "rebase-apply",
    ],
)
def test_commit_refuses_while_an_operation_is_in_progress(
    monkeypatch, repo, marker
):
    """Test a half finished merge, rebase or cherry-pick is never concluded."""
    repo.git_dir.mkdir(parents=True)
    (repo.git_dir / marker).touch()
    fake_git = install_fake_git(monkeypatch, FakeGit(status_output="UD f.txt"))

    with pytest.raises(SyncError, match=marker) as excinfo:
        commit_local_changes(repo)

    assert excinfo.value.kind is SyncErrorKind.OPERATION_IN_PROGRESS

    assert "add" not in fake_git.keys
    assert "commit" not in fake_git.keys


def test_commit_refuses_unmerged_files(monkeypatch, repo):
    """Test a file git still lists as conflicted is never committed."""
    fake_git = install_fake_git(
        monkeypatch,
        FakeGit(
            status_output="UD f.txt",
            unmerged_output=(
                "100644 1111111 1\tf.txt\x00100644 2222222 2\tf.txt\x00"
            ),
        ),
    )

    with pytest.raises(
        SyncError, match=r"unresolved conflicts in f\.txt$"
    ) as excinfo:
        commit_local_changes(repo)

    assert excinfo.value.kind is SyncErrorKind.UNRESOLVED_CONFLICTS

    assert "add" not in fake_git.keys


def _lock_index(git_dir, age_seconds):
    """Create the git directory holding an index.lock of the given age."""
    git_dir.mkdir(parents=True)
    lock = git_dir / "index.lock"
    lock.touch()
    modified = time.time() - age_seconds
    os.utime(lock, (modified, modified))
    return lock


def test_commit_refuses_a_detached_head(monkeypatch, repo):
    """Test nothing is committed on a detached HEAD, e.g. during git bisect."""
    fake_git = install_fake_git(monkeypatch, FakeGit(status_output=" M f.txt", head=None))

    with pytest.raises(SyncError, match="HEAD is detached") as excinfo:
        commit_local_changes(repo)

    assert excinfo.value.kind is SyncErrorKind.DETACHED_HEAD
    assert fake_git.keys == ["head"]


def test_commit_refuses_another_branch(monkeypatch, repo):
    """Test the user's own branch never gets a backup commit."""
    fake_git = install_fake_git(
        monkeypatch,
        FakeGit(status_output=" M f.txt", head="refs/heads/experiment"),
    )

    with pytest.raises(
        SyncError, match="'experiment' is checked out, not 'main'"
    ) as excinfo:
        commit_local_changes(repo)

    assert excinfo.value.kind is SyncErrorKind.WRONG_BRANCH
    assert fake_git.keys == ["head"]


def test_commit_refuses_a_branch_that_only_starts_like_the_configured_one(
    monkeypatch, repo
):
    """Test the comparison is on the whole ref, not a prefix of it."""
    install_fake_git(monkeypatch, FakeGit(head="refs/heads/main-old"))

    with pytest.raises(SyncError) as excinfo:
        commit_local_changes(repo)

    assert excinfo.value.kind is SyncErrorKind.WRONG_BRANCH


def test_a_failure_to_read_the_branch_is_a_git_failure(monkeypatch, repo):
    """Test a broken symbolic-ref is not mistaken for a detached HEAD."""
    install_fake_git(monkeypatch, FakeGit(failing="head"))

    with pytest.raises(SyncError, match="read the checked-out branch") as excinfo:
        commit_local_changes(repo)

    assert excinfo.value.kind is SyncErrorKind.GIT_FAILED


def test_commit_refuses_while_another_git_process_holds_the_index(
    monkeypatch, repo
):
    """Test a recent index.lock skips the cycle and is left alone."""
    lock = _lock_index(repo.git_dir, age_seconds=5)
    fake_git = install_fake_git(monkeypatch, FakeGit(status_output=" M f.txt"))

    with pytest.raises(SyncError, match="another git process") as excinfo:
        commit_local_changes(repo)

    assert excinfo.value.kind is SyncErrorKind.INDEX_LOCKED
    assert lock.exists()
    assert fake_git.keys == []


def test_a_stale_index_lock_is_removed_and_the_cycle_goes_ahead(
    monkeypatch, repo, caplog
):
    """Test a lock left by a killed git process no longer wedges the backup."""
    lock = _lock_index(repo.git_dir, age_seconds=busy.STALE_LOCK_SECONDS + 60)
    fake_git = install_fake_git(monkeypatch, FakeGit(status_output=" M f.txt"))

    with caplog.at_level("WARNING"):
        assert commit_local_changes(repo) is True

    assert not lock.exists()
    assert "commit" in fake_git.keys
    assert "Removed" in caplog.text


def test_a_stale_index_lock_that_cannot_be_removed_skips_the_cycle(
    monkeypatch, repo
):
    """Test a lock that cannot be deleted is reported rather than ignored."""
    _lock_index(repo.git_dir, age_seconds=busy.STALE_LOCK_SECONDS + 60)
    fake_git = install_fake_git(monkeypatch, FakeGit())

    def refuse(_path, missing_ok=False):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(busy.Path, "unlink", refuse)

    with pytest.raises(SyncError, match="cannot remove the stale") as excinfo:
        commit_local_changes(repo)

    assert excinfo.value.kind is SyncErrorKind.INDEX_LOCKED
    assert fake_git.keys == []


def test_a_missing_work_tree_stops_the_cycle(monkeypatch, repo):
    """Test a folder deleted while the service runs is reported, not restored."""
    repo.work_tree.rmdir()
    fake_git = install_fake_git(monkeypatch, FakeGit())

    with pytest.raises(SyncError, match="restored on the next start") as excinfo:
        ensure_safe_to_sync(repo)

    assert excinfo.value.kind is SyncErrorKind.MASS_DELETION
    assert not fake_git.keys
    assert not repo.work_tree.exists()
