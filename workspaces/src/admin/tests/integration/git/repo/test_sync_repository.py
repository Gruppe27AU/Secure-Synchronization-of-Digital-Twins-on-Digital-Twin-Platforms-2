"""
Integration tests for keeping the backup in its own repository, with real git.

The working tree's ``.git`` file is easily lost, and git would then fall
back on whatever repository encloses the working tree. These tests check
that the backup still commits and pushes only to its own remote, and that
a working tree that has lost most of its files is not committed, and that
one deleted outright is restored.
"""

import shutil

import pytest

from admin.git.repo import checks
from admin.git.repo.clone import clone_asset
from admin.git.service.scheduler import sync_all
from admin.git.service.status import SyncStatus
from admin.git.run.errors import SyncError, SyncErrorKind
from admin.git.repo.sync import sync_once
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


def test_an_emptied_working_tree_is_not_committed(world):
    """Test losing every file, fewer than the minimum, never deletes them remotely."""
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
    monkeypatch.setattr(checks, "MASS_DELETION_MIN_FILES", 2)
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


def _delete_work_tree(world):
    shutil.rmtree(world.workspace.work_tree)
    assert not world.workspace.work_tree.exists()


def _assert_restored(world):
    work_tree = world.workspace.work_tree
    for name, content in SEED_FILES.items():
        assert (work_tree / name).read_text() == content
    assert (work_tree / ".git").is_file()
    assert sync_once(world.workspace) is False
    for name, content in SEED_FILES.items():
        assert world.remote_file(name) == content


def test_a_deleted_work_tree_is_restored_on_startup(world):
    """Test the startup clone puts a deleted work tree back, not deleting remotely."""
    _delete_work_tree(world)

    assert clone_asset(world.workspace) is False

    _assert_restored(world)


def test_an_emptied_work_tree_is_restored_on_startup(world):
    """Test a folder holding only its .git file gets its files back."""
    for name in SEED_FILES:
        (world.workspace.work_tree / name).unlink()

    assert clone_asset(world.workspace) is False

    _assert_restored(world)


def test_a_work_tree_deleted_while_running_stays_deleted(world):
    """Test a cycle reports the missing folder and deletes nothing remotely."""
    _delete_work_tree(world)
    status = SyncStatus()
    status.enable([world.workspace.name])

    sync_all([world.workspace], status)

    repo_status = status.snapshot()["repositories"][world.workspace.name]
    assert repo_status["last_error"]["kind"] == "mass_deletion"
    assert not world.workspace.work_tree.exists()
    for name, content in SEED_FILES.items():
        assert world.remote_file(name) == content
