"""
Unit tests for :mod:`admin.git.repo.sync`: a cycle that cannot go ahead.

Real git is never invoked: :class:`tests.helpers.fake_git.FakeGit` answers
every command.
"""


import pytest

from admin.git.repo import push
from admin.git.repo.sync import sync_once
from admin.git.run import command as command_module
from admin.git.run.errors import SyncError, SyncErrorKind
from admin.git.run.runner import (
    GitTimeoutError,
    LOCAL_TIMEOUT_SECONDS,
    REMOTE_TIMEOUT_SECONDS,
)
from tests.helpers.fake_git import command_key, FakeGit, install_fake_git


def test_only_the_remote_commands_get_the_long_time_limit(monkeypatch, repo):
    """Test fetch and both pushes may take longer than the local commands."""
    fake_git = install_fake_git(
        monkeypatch, FakeGit(status_output=" M file.txt", incoming=1, outgoing=1)
    )

    sync_once(repo)

    remote = ("fetch", "push", "backup-push")
    for key, timeout in zip(fake_git.keys, fake_git.timeouts):
        expected = REMOTE_TIMEOUT_SECONDS if key in remote else LOCAL_TIMEOUT_SECONDS
        assert timeout == expected, key


@pytest.mark.parametrize(
    ("stuck", "fake_git", "action"),
    [
        ("fetch", FakeGit(), "fetch from the remote"),
        ("push", FakeGit(outgoing=1), "push changes"),
        # The detecting merge is allowed to fail, but not to hang.
        ("merge", FakeGit(incoming=1), "merge the remote's changes"),
    ],
)
def test_a_command_that_times_out_raises_sync_error(
    monkeypatch, repo, stuck, fake_git, action
):
    """Test a hung git command ends the cycle with SyncError."""

    def run(command, timeout, env=None):
        if command_key(command) == stuck:
            raise GitTimeoutError("git did not finish within 1 seconds and was stopped")
        return fake_git.run(command, timeout, env)

    monkeypatch.setattr(command_module, "run_git", run)

    with pytest.raises(SyncError, match=f"{action}.*did not finish") as excinfo:
        sync_once(repo)

    # Its own kind, so a hung remote is not mistaken for a broken repository.
    assert excinfo.value.kind is SyncErrorKind.TIMEOUT


def test_an_unreachable_remote_is_reported_as_a_network_failure(monkeypatch, repo):
    """Test a remote that cannot be reached gets its own kind."""
    install_fake_git(
        monkeypatch,
        FakeGit(
            failing="fetch",
            failure_message=(
                "fatal: unable to access 'https://example.com/org/repo.git/': "
                "Could not resolve host: example.com"
            ),
        ),
    )

    with pytest.raises(SyncError) as excinfo:
        sync_once(repo)

    assert excinfo.value.kind is SyncErrorKind.NETWORK


def test_a_conflict_stops_the_cycle_before_the_push(monkeypatch, repo):
    """Test a conflict is reported with the backup branch and nothing is pushed."""
    repo.git_dir.mkdir(parents=True)
    fake_git = install_fake_git(
        monkeypatch, FakeGit(incoming=1, conflicts=["a.txt"], outgoing=1)
    )

    with pytest.raises(SyncError, match=r"a\.txt.*on branch backup/") as excinfo:
        sync_once(repo)

    assert excinfo.value.kind is SyncErrorKind.MERGE_CONFLICT
    assert "push" not in fake_git.keys


def test_sync_once_raises_when_a_step_fails(monkeypatch, repo):
    """Test a failing step stops the cycle instead of continuing blindly."""
    fake_git = install_fake_git(
        monkeypatch, FakeGit(status_output=" M file.txt", failing="commit")
    )

    with pytest.raises(SyncError, match="commit was rejected"):
        sync_once(repo)

    assert "fetch" not in fake_git.keys


def test_sync_once_leaves_an_unfinished_merge_alone(monkeypatch, repo):
    """Test a merge the user has not finished is neither merged into nor pushed.

    Even with nothing to commit, merging would trip over the user's merge
    and the cleanup after a failed merge would abort it, throwing away
    whatever they had resolved so far.
    """
    repo.git_dir.mkdir(parents=True)
    (repo.git_dir / "MERGE_HEAD").touch()
    fake_git = install_fake_git(monkeypatch, FakeGit(incoming=1, outgoing=1))

    with pytest.raises(SyncError, match="MERGE_HEAD"):
        sync_once(repo)

    assert fake_git.keys == []


def test_a_pull_that_fails_for_good_names_the_backup_branch(monkeypatch, repo):
    """Test the failure says which backup branch holds the work."""
    repo.git_dir.mkdir(parents=True)
    fake_git = install_fake_git(
        monkeypatch,
        FakeGit(
            incoming=1,
            failing="merge",
            failure_message="fatal: refusing to merge unrelated histories",
        ),
    )

    with pytest.raises(SyncError, match="on branch backup/") as excinfo:
        sync_once(repo)

    assert excinfo.value.kind is SyncErrorKind.PULL_FAILED
    workspace = (repo.git_dir / push.WORKSPACE_ID_FILE).read_text().strip()
    assert fake_git.command_for("backup-push")[-2:] == [
        "origin",
        f"+refs/heads/main:refs/heads/backup/{workspace}",
    ]


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("fatal: Authentication failed for 'https://x/'", SyncErrorKind.AUTH_FAILED),
        ("fatal: unable to access 'https://x/': Could not resolve host: x",
         SyncErrorKind.NETWORK),
    ],
)
def test_a_pull_that_may_recover_is_reported_as_it_is(monkeypatch, repo, message, kind):
    """Test a rejected token or a network failure is not a lasting pull failure."""
    install_fake_git(monkeypatch, FakeGit(failing="fetch", failure_message=message))

    with pytest.raises(SyncError) as excinfo:
        sync_once(repo)

    assert excinfo.value.kind is kind


def test_a_branch_the_remote_never_had_is_created_by_the_push(monkeypatch, repo):
    """Test an empty remote is not a failure: the first push creates the branch."""
    fake_git = install_fake_git(
        monkeypatch,
        FakeGit(
            failing="fetch",
            failure_message="fatal: couldn't find remote ref main",
            missing_refs={"refs/remotes/origin/main"},
            outgoing=1,
        ),
    )

    assert sync_once(repo) is True

    assert fake_git.command_for("outgoing")[-1] == "refs/heads/main"
    assert "push" in fake_git.keys


def test_a_branch_that_disappeared_from_the_remote_is_a_pull_failure(
    monkeypatch, repo
):
    """Test a branch renamed or deleted on the forge is a lasting pull failure."""
    repo.git_dir.mkdir(parents=True)
    fake_git = install_fake_git(
        monkeypatch,
        FakeGit(failing="fetch", failure_message="fatal: couldn't find remote ref main"),
    )

    with pytest.raises(SyncError) as excinfo:
        sync_once(repo)

    assert excinfo.value.kind is SyncErrorKind.PULL_FAILED
    assert "backup-push" in fake_git.keys
