"""
Integration tests for keeping the backup in its own repository, with real git.

The working tree's ``.git`` file is easily lost, and git would then fall
back on whatever repository encloses the working tree. These tests check
that the backup still commits and pushes only to its own remote, and that
a working tree that has lost most of its files is not committed.
"""

import pytest

from admin.git import sync as sync_module
from admin.git.scheduler import sync_all
from admin.git.status import SyncStatus
from admin.git.sync import SyncError, SyncErrorKind, sync_once
from tests.helpers.git_world import SEED_FILES, git, write


@pytest.fixture(name="enclosing")
def fixture_enclosing(world, tmp_path):
    """The user's own repository around the working tree, with its own remote."""
    other = tmp_path / "other.git"
    git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(other))

    enclosing = world.workspace.work_tree.parent
    git(enclosing, "init", "--quiet", "-b", "main")
    git(enclosing, "remote", "add", "origin", str(other))
    write(enclosing / ".gitignore", f"{world.workspace.work_tree.name}/\n")
    git(enclosing, "add", ".gitignore")
    git(enclosing, "commit", "--quiet", "-m", "my repo")
    git(enclosing, "push", "--quiet", "origin", "main")
    return enclosing


def test_a_lost_git_file_does_not_redirect_the_backup(world, enclosing, tmp_path):
    """Test the backup goes to its own remote, not the enclosing repository's."""
    work_tree = world.workspace.work_tree
    (work_tree / ".git").unlink()
    write(enclosing / "notes.md", "unrelated personal notes\n")
    write(work_tree / "thesis.md", "work\n")

    assert sync_once(world.workspace) is True

    assert world.remote_file("thesis.md") == "work\n"
    assert git(enclosing, "rev-list", "--count", "HEAD").strip() == "1"
    assert git(tmp_path / "other.git", "rev-list", "--count", "main").strip() == "1"
    assert "notes.md" in git(enclosing, "status", "--porcelain")


def test_a_lost_git_file_is_written_back_for_the_user(world, enclosing):
    """Test the user's own git commands reach the backup repository again."""
    work_tree = world.workspace.work_tree
    (work_tree / ".git").unlink()
    status = SyncStatus()
    status.enable([world.workspace.name])

    sync_all([world.workspace], status)

    assert status.snapshot()["repositories"][world.workspace.name]["last_error"] is None
    assert git(work_tree, "rev-parse", "--absolute-git-dir").strip() != str(
        (enclosing / ".git").resolve()
    )
    assert "notebook.txt" in git(work_tree, "ls-files")


def test_an_emptied_working_tree_is_not_committed(world, monkeypatch):
    """Test losing every file never deletes them on the remote."""
    monkeypatch.setattr(sync_module, "MASS_DELETION_MIN_FILES", 2)
    work_tree = world.workspace.work_tree
    for name in SEED_FILES:
        (work_tree / name).unlink()
    (work_tree / ".git").unlink()

    with pytest.raises(SyncError) as excinfo:
        sync_once(world.workspace)

    assert excinfo.value.kind is SyncErrorKind.MASS_DELETION
    for name, content in SEED_FILES.items():
        assert world.remote_file(name) == content


def test_a_deletion_committed_by_hand_is_pushed(world, monkeypatch):
    """Test the way out of the guard: commit the deletion yourself."""
    monkeypatch.setattr(sync_module, "MASS_DELETION_MIN_FILES", 2)
    work_tree = world.workspace.work_tree
    for name in SEED_FILES:
        (work_tree / name).unlink()
    with pytest.raises(SyncError):
        sync_once(world.workspace)

    git(work_tree, "add", "-A")
    git(work_tree, "commit", "--quiet", "-m", "clear out")

    assert sync_once(world.workspace) is True
    for name in SEED_FILES:
        assert world.remote_file(name) is None
