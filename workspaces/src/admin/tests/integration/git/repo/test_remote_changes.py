"""
Integration tests for remotes that change under the backup, with real git.

Ported from the reproductions of review round 5: a remote whose history was
rewritten or whose branch was renamed can no longer be pulled, yet the
workspace's work must still reach the remote; and a remote with no commits
at all must still be usable.
"""

import os

import pytest

from admin.git.repo.clone import clone_asset
from admin.git.run.errors import SyncError, SyncErrorKind
from admin.git.repo.push import backup_branch
from admin.git.repo.sync import sync_once
from admin.git.service.scheduler import sync_all
from admin.git.service.status import SyncStatus
from tests.helpers.git_world import git, write
from tests.helpers.repo_factory import make_repo


def _backup_file(world, path):
    """Return a file on the workspace's backup branch of the remote."""
    branch = backup_branch(world.workspace)
    return git(world.remote, "show", f"{branch}:{path}")


def test_a_rewritten_remote_history_still_gets_the_work(world):
    """Test unrelated histories push the work to the backup branch, every cycle."""
    seed = world.colleague
    git(seed, "checkout", "--quiet", "--orphan", "fresh")
    git(seed, "rm", "-rqf", ".")
    write(seed / "n.txt", "new\n")
    git(seed, "add", "-A")
    git(seed, "commit", "--quiet", "-m", "fresh")
    git(seed, "push", "--quiet", "-f", "origin", "fresh:main")
    write(world.workspace.work_tree / "thesis.md", "work\n")

    with pytest.raises(SyncError) as excinfo:
        sync_once(world.workspace)

    assert excinfo.value.kind is SyncErrorKind.PULL_FAILED
    assert _backup_file(world, "thesis.md") == "work\n"

    write(world.workspace.work_tree / "thesis.md", "more work\n")
    with pytest.raises(SyncError):
        sync_once(world.workspace)

    assert _backup_file(world, "thesis.md") == "more work\n"


def test_a_renamed_remote_branch_still_gets_the_work(world):
    """Test a branch renamed on the forge pushes the work to the backup branch."""
    git(world.remote, "branch", "-m", "main", "trunk")
    git(world.remote, "symbolic-ref", "HEAD", "refs/heads/trunk")
    write(world.workspace.work_tree / "thesis.md", "work\n")

    with pytest.raises(SyncError) as excinfo:
        sync_once(world.workspace)

    assert excinfo.value.kind is SyncErrorKind.PULL_FAILED
    assert _backup_file(world, "thesis.md") == "work\n"


@pytest.fixture(name="empty")
def fixture_empty(world, tmp_path):
    """A workspace repository whose remote has no commits at all."""
    # world only sets up git's config and the local-remote options.
    del world
    remote = tmp_path / "empty.git"
    git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(remote))
    return make_repo(
        "private",
        repo_url=str(remote),
        git_dir=tmp_path / "empty-git",
        work_tree=tmp_path / "empty-tree",
    ), remote


def test_an_empty_remote_is_started_and_filled(empty):
    """Test an empty remote can be cloned, and the first sync creates its branch."""
    repo, remote = empty

    assert clone_asset(repo) is True
    write(repo.work_tree / "first.txt", "first\n")

    assert sync_once(repo) is True

    assert git(remote, "show", "main:first.txt") == "first\n"


def test_an_empty_remote_with_nothing_to_back_up_is_not_an_error(empty):
    """Test a fresh, empty repository waits quietly for the first file."""
    repo, _ = empty
    clone_asset(repo)

    assert sync_once(repo) is False


def test_an_empty_remote_logs_no_lost_files_on_startup(empty, caplog):
    """Test a repository with no files yet is not reported as having lost them."""
    repo, _ = empty
    clone_asset(repo)
    sync_once(repo)

    assert clone_asset(repo) is False

    assert "lost the files" not in caplog.text


@pytest.mark.skipif(os.name != "posix", reason="needs a shell hook")
def test_a_refused_backup_branch_still_counts_the_cycle_as_synced(world):
    """Test a forge rule against backup/* reports backup_failed, yet last_success moves."""
    hook = world.remote / "hooks" / "pre-receive"
    hook.write_text('#!/bin/sh\ngrep -q " refs/heads/backup/" && exit 1\nexit 0\n')
    hook.chmod(0o755)
    write(world.workspace.work_tree / "new.txt", "new\n")
    status = SyncStatus()
    status.enable([world.workspace.name])

    sync_all([world.workspace], status)

    repo_status = status.snapshot()["repositories"][world.workspace.name]
    assert repo_status["last_error"]["kind"] == "backup_failed"
    assert repo_status["last_success"] == repo_status["last_error"]["at"]
    assert world.remote_file("new.txt") == "new\n"


@pytest.mark.skipif(os.name != "posix", reason="needs a shell hook")
def test_an_empty_remote_gets_its_branch_before_the_backup_branch(empty):
    """Test the first push creates the configured branch, so a forge makes it the default."""
    repo, remote = empty
    hook = remote / "hooks" / "post-receive"
    hook.write_text('#!/bin/sh\nwhile read old new ref; do echo "$ref"; done >> pushed\n')
    hook.chmod(0o755)
    clone_asset(repo)
    write(repo.work_tree / "first.txt", "first\n")

    sync_once(repo)
    write(repo.work_tree / "second.txt", "second\n")
    sync_once(repo)

    pushed = (remote / "pushed").read_text().split()
    assert pushed[0] == "refs/heads/main"
    assert pushed[1:] == [f"refs/heads/{backup_branch(repo)}", "refs/heads/main"]
