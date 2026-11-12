"""
Integration tests for pull-only repositories, with real git.

A repository shared between users, such as ``common``, only ever takes the
remote's changes: the workspace's own edits are never committed or pushed,
so no user can overwrite another's files.
"""

from dataclasses import replace

import pytest

from admin.git.setup.config import SyncMode
from admin.git.run.errors import SyncError, SyncErrorKind
from admin.git.repo.sync import sync_once
from tests.helpers.git_world import git, write


@pytest.fixture(name="workspace")
def fixture_workspace(world):
    """The world's workspace, made pull-only."""
    return replace(world.workspace, mode=SyncMode.PULL)


def test_the_colleagues_changes_arrive(world, workspace):
    """Test a pull-only repository is brought up to date."""
    world.colleague_pushes(lambda tree: write(tree / "other.txt", "other v2\n"))

    assert sync_once(workspace) is True

    assert (workspace.work_tree / "other.txt").read_bytes() == b"other v2\n"


def test_local_changes_are_never_pushed(world, workspace):
    """Test a workspace's edits stay local and the remote keeps everyone's files."""
    write(workspace.work_tree / "model.txt", "local edit\n")
    write(workspace.work_tree / "new.txt", "local file\n")
    remote_head = git(world.remote, "rev-parse", "main")

    assert sync_once(workspace) is False

    assert git(world.remote, "rev-parse", "main") == remote_head
    assert world.remote_file("model.txt") == "model v1\n"
    assert world.remote_file("new.txt") is None


def test_a_local_change_in_the_way_is_reported_and_kept(world, workspace):
    """Test the remote never silently overwrites a local edit."""
    world.colleague_pushes(lambda tree: write(tree / "model.txt", "model v2\n"))
    write(workspace.work_tree / "model.txt", "local edit\n")

    with pytest.raises(SyncError) as excinfo:
        sync_once(workspace)

    assert excinfo.value.kind is SyncErrorKind.LOCAL_CHANGES
    assert (workspace.work_tree / "model.txt").read_bytes() == b"local edit\n"


def test_unrelated_local_edits_do_not_block_an_update(world, workspace):
    """Test the remote's changes to other files still arrive."""
    world.colleague_pushes(lambda tree: write(tree / "other.txt", "other v2\n"))
    write(workspace.work_tree / "model.txt", "local edit\n")

    sync_once(workspace)

    assert (workspace.work_tree / "other.txt").read_bytes() == b"other v2\n"
    assert (workspace.work_tree / "model.txt").read_bytes() == b"local edit\n"
