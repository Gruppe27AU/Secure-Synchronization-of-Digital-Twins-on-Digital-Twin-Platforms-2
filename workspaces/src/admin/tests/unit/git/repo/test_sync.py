"""
Unit tests for :mod:`admin.git.repo.sync`: the order of a cycle.

Real git is never invoked: :class:`tests.helpers.fake_git.FakeGit` answers
every command.
"""

import subprocess
import threading

import pytest

from admin.git.repo.sync import sync_once, SyncStopped
from admin.git.run import command as command_module
from admin.git.run.errors import SyncError, SyncErrorKind
from tests.helpers.fake_git import command_key, FakeGit, install_fake_git
from tests.unit.git.repo.test_commit import MARKED_NOTES


def test_sync_once_commits_before_it_merges(monkeypatch, repo):
    """Test the user's work is committed first, so the merge has a clean tree.

    Git refuses to merge over modified files, so pulling before committing
    would fail as soon as the remote had anything to deliver.
    """
    fake_git = install_fake_git(
        monkeypatch, FakeGit(status_output=" M file.txt", incoming=1, outgoing=2)
    )

    assert sync_once(repo) is True
    assert fake_git.keys.index("commit") < fake_git.keys.index("merge")


def test_sync_once_reports_no_change_when_everything_is_in_sync(
    monkeypatch, repo
):
    """Test a clean tree and a level remote produce no commit and no push."""
    fake_git = install_fake_git(monkeypatch, FakeGit(status_output="", incoming=0))

    assert sync_once(repo) is False
    assert "commit" not in fake_git.keys
    assert "push" not in fake_git.keys


def test_sync_once_reports_a_change_when_only_the_remote_moved(
    monkeypatch, repo
):
    """Test merging someone else's work counts as a change worth reporting."""
    install_fake_git(monkeypatch, FakeGit(status_output="", incoming=1))

    assert sync_once(repo) is True


def test_sync_once_backs_up_every_cycle_before_it_pulls(monkeypatch, repo):
    """Test the backup push runs even with nothing to commit or merge."""
    repo.git_dir.mkdir(parents=True)
    fake_git = install_fake_git(monkeypatch, FakeGit())

    assert sync_once(repo) is False
    assert fake_git.keys.index("backup-push") < fake_git.keys.index("fetch")


def test_a_failed_backup_push_is_reported_after_the_cycle(monkeypatch, repo):
    """Test the cycle still pulls and pushes, then reports backup_failed."""
    repo.git_dir.mkdir(parents=True)
    fake_git = install_fake_git(
        monkeypatch, FakeGit(incoming=1, outgoing=1, failing="backup-push")
    )

    with pytest.raises(SyncError, match="backup branch") as excinfo:
        sync_once(repo)

    assert excinfo.value.kind is SyncErrorKind.BACKUP_FAILED
    assert excinfo.value.synced is True
    assert "merge" in fake_git.keys and "push" in fake_git.keys


def test_a_failed_backup_push_with_a_held_back_file_is_not_synced(monkeypatch, repo):
    """Test the cycle is no success while a file is still only in the container."""
    repo.git_dir.mkdir(parents=True)
    install_fake_git(monkeypatch, FakeGit(**MARKED_NOTES, failing="backup-push"))

    with pytest.raises(SyncError, match="backup branch") as excinfo:
        sync_once(repo)

    assert excinfo.value.synced is False


def test_a_failed_backup_push_outranks_a_conflict(monkeypatch, repo):
    """Test the status shows the work is not safe, not only the conflict."""
    repo.git_dir.mkdir(parents=True)
    install_fake_git(
        monkeypatch, FakeGit(incoming=1, conflicts=["a.txt"], failing="backup-push")
    )

    with pytest.raises(SyncError, match=r"backup branch.*a\.txt") as excinfo:
        sync_once(repo)

    assert excinfo.value.kind is SyncErrorKind.BACKUP_FAILED
    assert excinfo.value.synced is False


def test_the_backup_branch_stays_the_same_across_cycles(monkeypatch, repo):
    """Test one workspace always pushes to the same backup branch."""
    repo.git_dir.mkdir(parents=True)
    fake_git = install_fake_git(monkeypatch, FakeGit(incoming=1, failing="merge"))

    for _ in range(2):
        with pytest.raises(SyncError):
            sync_once(repo)

    pushes = [
        command[-1] for command in fake_git.commands
        if command_key(command) == "backup-push"
    ]
    assert len(pushes) == 2 and pushes[0] == pushes[1]


def test_sync_once_stops_after_the_commit_when_asked_to(monkeypatch, repo):
    """Test a stopping service starts no remote command after the commit."""
    fake_git = install_fake_git(
        monkeypatch, FakeGit(status_output=" M f.txt", incoming=1, outgoing=1)
    )
    stop_event = threading.Event()
    stop_event.set()

    with pytest.raises(SyncStopped, match="before it could back up"):
        sync_once(repo, stop_event)

    assert "commit" in fake_git.keys
    assert "backup-push" not in fake_git.keys
    assert "fetch" not in fake_git.keys


def test_sync_once_stops_before_the_push_when_asked_to(monkeypatch, repo):
    """Test a stop requested during the pull is honoured before the push."""
    fake_git = FakeGit(incoming=1, outgoing=1)
    stop_event = threading.Event()

    def run_and_stop_after_fetch(command, timeout, env=None):
        result = fake_git.run(command, timeout, env)
        if command_key(command) == "fetch":
            stop_event.set()
        return result

    monkeypatch.setattr(command_module, "run_git", run_and_stop_after_fetch)

    with pytest.raises(SyncStopped, match="before it could push"):
        sync_once(repo, stop_event)

    assert "merge" in fake_git.keys
    assert "push" not in fake_git.keys


def test_sync_once_runs_to_the_end_while_not_stopped(monkeypatch, repo):
    """Test an unset stop event changes nothing."""
    fake_git = install_fake_git(
        monkeypatch, FakeGit(status_output=" M f.txt", incoming=1, outgoing=1)
    )

    assert sync_once(repo, threading.Event()) is True
    assert "push" in fake_git.keys


def test_every_command_names_the_git_dir_and_working_tree(monkeypatch, repo):
    """Test git is never left to find the repository through the .git file.

    Without the .git file git walks up and would commit into, and push
    from, whatever repository encloses the working tree.
    """
    fake_git = install_fake_git(
        monkeypatch, FakeGit(status_output=" M f.txt", incoming=1, outgoing=1)
    )

    sync_once(repo)

    for command in fake_git.commands:
        assert f"--git-dir={repo.git_dir}" in command, command_key(command)
        assert f"--work-tree={repo.work_tree}" in command, command_key(command)
        # Options after the subcommand would be read as its arguments.
        assert command.index(f"--git-dir={repo.git_dir}") < _subcommand_index(command)


def _subcommand_index(command):
    """Index of the git subcommand: the first word that is not an option."""
    index = 1
    while command[index].startswith("-"):
        index += 2 if command[index] in ("-c", "-C") else 1
    return index


def test_a_failed_backup_push_outranks_a_failed_push(monkeypatch, repo):
    """Test a push that fails after a failed backup still reports backup_failed."""
    repo.git_dir.mkdir(parents=True)
    fake_git = FakeGit(outgoing=1, failing="backup-push")

    def run(command, timeout, env=None):
        if command_key(command) == "push":
            return subprocess.CompletedProcess(command, 1, "", "fatal: push refused")
        return fake_git.run(command, timeout, env)

    monkeypatch.setattr(command_module, "run_git", run)

    with pytest.raises(SyncError, match="backup branch.*push refused") as excinfo:
        sync_once(repo)

    assert excinfo.value.kind is SyncErrorKind.BACKUP_FAILED
