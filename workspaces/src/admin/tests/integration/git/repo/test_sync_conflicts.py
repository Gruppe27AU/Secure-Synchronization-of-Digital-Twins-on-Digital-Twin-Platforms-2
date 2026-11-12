"""
Integration tests for merge conflicts with real git.

A conflict aborts the merge and stops the cycle: the remote keeps the
colleague's work, the workspace keeps the user's, and the user's work is
on the backup branch. A second clone plays the "colleague".
"""

import subprocess

import pytest

from admin.git.run.errors import SyncError, SyncErrorKind
from admin.git.repo.push import backup_branch
from admin.git.repo.sync import sync_once
from tests.helpers.git_world import NOTEBOOK, git, write


def _both_edit(world):
    world.colleague_pushes(lambda tree: write(
        tree / "notebook.txt", NOTEBOOK.replace("line1\n", "COLLEAGUE1\n")
    ))
    write(world.workspace.work_tree / "notebook.txt",
          NOTEBOOK.replace("line1\n", "LOCAL1\n"))
    return "notebook.txt"


def _remote_deletes(world):
    world.colleague_pushes(lambda tree: (tree / "model.txt").unlink())
    write(world.workspace.work_tree / "model.txt", "local work\n")
    return "model.txt"


def _local_deletes(world):
    world.colleague_pushes(lambda tree: write(tree / "old.txt", "old v2\n"))
    (world.workspace.work_tree / "old.txt").unlink()
    return "old.txt"


def _file_meets_directory(world):
    def add_directory(tree):
        (tree / "clash").mkdir()
        write(tree / "clash" / "y", "remote\n")

    world.colleague_pushes(add_directory)
    write(world.workspace.work_tree / "clash", "local file\n")
    return "clash"


def _read(path):
    return path.read_bytes() if path.is_file() else None


def _show(world, ref, path):
    try:
        return git(world.remote, "show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return None


@pytest.mark.parametrize(
    "conflict",
    [_both_edit, _remote_deletes, _local_deletes, _file_meets_directory],
    ids=["both-edit", "remote-deletes", "local-deletes", "file-meets-directory"],
)
def test_a_conflict_aborts_the_merge_and_keeps_both_sides(world, conflict):
    """Test neither side is overwritten and the local work is backed up."""
    path = conflict(world)
    remote_main = git(world.remote, "rev-parse", "main")
    local = _read(world.workspace.work_tree / path)

    with pytest.raises(SyncError, match=rf"{path}.*backup/") as excinfo:
        sync_once(world.workspace)

    assert excinfo.value.kind is SyncErrorKind.MERGE_CONFLICT
    assert git(world.remote, "rev-parse", "main") == remote_main
    assert _read(world.workspace.work_tree / path) == local
    assert not (world.workspace.git_dir / "MERGE_HEAD").exists()
    backed_up = _show(world, backup_branch(world.workspace), path)
    assert backed_up == (local.decode() if local else None)


def test_the_backup_branch_is_pushed_every_cycle(world):
    """Test the work reaches the backup branch without anything going wrong."""
    write(world.workspace.work_tree / "thesis.md", "work\n")

    assert sync_once(world.workspace) is True

    branch = backup_branch(world.workspace)
    assert _show(world, branch, "thesis.md") == "work\n"
    assert world.remote_file("thesis.md") == "work\n"


def test_a_conflict_merged_by_hand_is_pushed(world):
    """Test the next cycle pushes a merge the user finished themselves."""
    _both_edit(world)
    with pytest.raises(SyncError):
        sync_once(world.workspace)

    tree = world.workspace.work_tree
    with pytest.raises(subprocess.CalledProcessError):
        git(tree, "merge", "origin/main")
    write(tree / "notebook.txt", "merged\n")
    git(tree, "add", "notebook.txt")
    git(tree, "commit", "--quiet", "--no-edit")

    assert sync_once(world.workspace) is True

    assert world.remote_file("notebook.txt") == "merged\n"
