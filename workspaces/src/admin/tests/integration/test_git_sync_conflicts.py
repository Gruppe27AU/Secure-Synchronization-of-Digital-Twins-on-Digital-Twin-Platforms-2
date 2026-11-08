"""
Integration tests for resolving merge conflicts with real git.

A bare repository in ``tmp_path`` stands in for the remote. The workspace
is cloned with :func:`admin.git.clone.clone_asset` and synchronized with
:func:`admin.git.sync.sync_once`; a second clone plays a "colleague".
"""

from admin.git.sync import sync_once
from tests.helpers.git_world import NOTEBOOK, write


def test_local_change_beats_a_remote_deletion(world):
    """Test a file deleted remotely but changed locally is pushed back."""
    world.colleague_pushes(lambda tree: (tree / "model.txt").unlink())
    write(world.workspace.work_tree / "model.txt", "local work\n")

    assert sync_once(world.workspace) is True

    assert world.remote_file("model.txt") == "local work\n"


def test_local_deletion_beats_a_remote_change(world):
    """Test a file deleted locally but changed remotely stays deleted."""
    world.colleague_pushes(lambda tree: write(tree / "old.txt", "old v2\n"))
    (world.workspace.work_tree / "old.txt").unlink()

    assert sync_once(world.workspace) is True

    assert world.remote_file("old.txt") is None
    assert not (world.workspace.work_tree / "old.txt").exists()


def test_conflicted_file_keeps_no_remote_lines(world):
    """Test the whole local file wins, not only its conflicting lines."""
    world.colleague_pushes(lambda tree: write(
        tree / "notebook.txt",
        NOTEBOOK.replace("line1\n", "COLLEAGUE1\n").replace(
            "line8\n", "COLLEAGUE8\n"),
    ))
    local = NOTEBOOK.replace("line1\n", "LOCAL1\n")
    write(world.workspace.work_tree / "notebook.txt", local)

    sync_once(world.workspace)

    assert world.remote_file("notebook.txt") == local
    assert (world.workspace.work_tree / "notebook.txt").read_bytes() == (
        local.encode()
    )


def test_files_without_a_conflict_get_the_remote_changes(world):
    """Test the colleague's work in other files survives a conflict."""
    def change(tree):
        write(tree / "notebook.txt", NOTEBOOK.replace("line1\n", "COLLEAGUE1\n"))
        write(tree / "other.txt", "other v2\n")

    world.colleague_pushes(change)
    write(
        world.workspace.work_tree / "notebook.txt",
        NOTEBOOK.replace("line1\n", "LOCAL1\n"),
    )

    sync_once(world.workspace)

    assert (world.workspace.work_tree / "other.txt").read_bytes() == b"other v2\n"
    assert world.remote_file("other.txt") == "other v2\n"


def test_the_next_sync_after_a_conflict_is_clean(world):
    """Test a resolved conflict leaves nothing behind to block later syncs."""
    world.colleague_pushes(lambda tree: (tree / "model.txt").unlink())
    write(world.workspace.work_tree / "model.txt", "local work\n")
    sync_once(world.workspace)

    assert sync_once(world.workspace) is False
