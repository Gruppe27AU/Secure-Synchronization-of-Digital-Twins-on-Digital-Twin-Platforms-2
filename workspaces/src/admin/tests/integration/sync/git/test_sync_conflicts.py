"""
Integration tests for merge conflicts with real git.

A conflict aborts the merge, and the workspace's branch replaces the
remote's: the remote's version is first kept on a ``remote-backup/``
branch, in the same atomic push. A second clone plays the "colleague".
"""

import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from admin.sync.git import command as command_module
from admin.sync.git import push as push_module
from admin.sync.git.errors import SyncError, SyncErrorKind
from admin.sync.git.push import backup_branch
from admin.sync.git.sync import sync_once
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


def _remote_backups(world):
    """The remote's ``remote-backup/`` branches and the commit each points at."""
    listing = git(
        world.remote,
        "for-each-ref",
        "--format=%(refname:short) %(objectname)",
        "refs/heads/remote-backup/",
    )
    return dict(line.split() for line in listing.splitlines())


def _sha(cwd, ref):
    return git(cwd, "rev-parse", ref).strip()


@pytest.fixture(name="ticking_clock")
def fixture_ticking_clock(monkeypatch):
    """Make every remote backup branch a minute later than the one before."""
    moments = (
        datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc) + timedelta(minutes=step)
        for step in range(100)
    )
    monkeypatch.setattr(push_module, "_utc_now", lambda: next(moments))


@pytest.mark.parametrize(
    "conflict",
    [_both_edit, _remote_deletes, _local_deletes, _file_meets_directory],
    ids=["both-edit", "remote-deletes", "local-deletes", "file-meets-directory"],
)
def test_a_conflict_makes_the_workspace_the_remote_branch(world, conflict):
    """Test the remote ends up exactly as the workspace, its version saved."""
    path = conflict(world)
    remote_main = _sha(world.remote, "main")
    local = _read(world.workspace.work_tree / path)
    saved = []

    assert sync_once(world.workspace, on_overwrite=saved.append) is True

    workspace_main = _sha(world.workspace.git_dir, "main")
    assert _sha(world.remote, "main") == workspace_main
    assert _show(world, "main", path) == (local.decode() if local else None)
    assert _read(world.workspace.work_tree / path) == local
    assert not (world.workspace.git_dir / "MERGE_HEAD").exists()
    assert _remote_backups(world) == {saved[0]: remote_main}
    # Nothing of the remote's version lingers on the branch.
    assert subprocess.run(
        ["git", "merge-base", "--is-ancestor", remote_main, workspace_main],
        cwd=world.remote, check=False,
    ).returncode != 0


def test_every_remote_commit_is_kept_on_the_remote_backup_branch(world):
    """Test a commit that did not conflict is backed up too, not merged."""
    world.colleague_pushes(lambda tree: write(tree / "other.txt", "other v2\n"))
    _both_edit(world)
    remote_main = _sha(world.remote, "main")

    sync_once(world.workspace)

    (branch,) = _remote_backups(world)
    assert _show(world, branch, "other.txt") == "other v2\n"
    assert _show(world, branch, "notebook.txt").startswith("COLLEAGUE1\n")
    assert _sha(world.remote, branch) == remote_main
    assert world.remote_file("other.txt") == "other v1\n"


def test_nothing_is_left_to_do_after_an_overwrite(world):
    """Test the next cycle finds the workspace and the remote in step."""
    _both_edit(world)
    sync_once(world.workspace)

    assert sync_once(world.workspace) is False

    assert _sha(world.remote, "main") == _sha(world.workspace.git_dir, "main")


def test_each_conflict_gets_a_remote_backup_branch_of_its_own(world, ticking_clock):
    """Test a second overwrite never replaces the first backup."""
    del ticking_clock
    _both_edit(world)
    sync_once(world.workspace)
    git(world.colleague, "fetch", "--quiet", "origin")
    git(world.colleague, "reset", "--quiet", "--hard", "origin/main")
    first = _sha(world.remote, "main")
    world.colleague_pushes(lambda tree: write(tree / "notebook.txt", "COLLEAGUE2\n"))
    write(world.workspace.work_tree / "notebook.txt", "LOCAL2\n")
    second = _sha(world.remote, "main")

    sync_once(world.workspace)

    backups = _remote_backups(world)
    assert len(backups) == 2
    assert second in backups.values() and first not in backups.values()
    assert world.remote_file("notebook.txt") == "LOCAL2\n"


def test_a_push_during_the_overwrite_is_never_lost(world, monkeypatch, ticking_clock):
    """Test the lease refuses the overwrite when the remote moved since the fetch."""
    del ticking_clock
    _both_edit(world)
    real_run_git = command_module.run_git
    late = {}

    def run_git(command, timeout, env=None):
        if "--atomic" in command and not late:
            world.colleague_pushes(lambda tree: write(tree / "late.txt", "late\n"))
            late["sha"] = _sha(world.remote, "main")
        return real_run_git(command, timeout, env=env)

    monkeypatch.setattr(command_module, "run_git", run_git)

    with pytest.raises(SyncError, match="changed meanwhile") as excinfo:
        sync_once(world.workspace)

    assert excinfo.value.kind is SyncErrorKind.REMOTE_CHANGED
    assert _sha(world.remote, "main") == late["sha"]
    # Atomic: the refused overwrite left no backup branch behind either.
    assert not _remote_backups(world)

    assert sync_once(world.workspace) is True

    assert _show(world, next(iter(_remote_backups(world))), "late.txt") == "late\n"
    assert world.remote_file("late.txt") is None


def test_a_remote_that_refuses_force_pushes_keeps_its_branch(world):
    """Test a protected branch stops the cycle, and the work stays backed up."""
    git(world.remote, "config", "receive.denyNonFastForwards", "true")
    path = _both_edit(world)
    remote_main = _sha(world.remote, "main")

    with pytest.raises(SyncError, match=rf"{path}.*backup/") as excinfo:
        sync_once(world.workspace)

    assert excinfo.value.kind is SyncErrorKind.OVERWRITE_REFUSED
    assert _sha(world.remote, "main") == remote_main
    assert not _remote_backups(world)
    assert _show(world, backup_branch(world.workspace), path).startswith("LOCAL1\n")


def test_the_backup_branch_is_pushed_every_cycle(world):
    """Test the work reaches the backup branch without anything going wrong."""
    write(world.workspace.work_tree / "thesis.md", "work\n")

    assert sync_once(world.workspace) is True

    branch = backup_branch(world.workspace)
    assert _show(world, branch, "thesis.md") == "work\n"
    assert world.remote_file("thesis.md") == "work\n"
