"""
Unit tests for what a sync cycle does when part of it cannot go ahead.

Covers :mod:`admin.git.repo.sync` and :mod:`admin.git.repo.checks`: a pull that fails
for good, the backup branch, a remote without the branch, files held back
for their size, and the default excludes. Real git is never invoked -
:class:`tests.helpers.fake_git.FakeGit` answers every command.
"""

import pytest

from admin.git.repo import checks, push
from admin.git.repo.commit import HeldBack, commit_local_changes
from admin.git.run.errors import SyncError, SyncErrorKind
from admin.git.repo.push import push_if_ahead
from admin.git.repo.sync import sync_once
from tests.helpers.fake_git import FakeGit, command_key, install_fake_git
from tests.helpers.repo_factory import make_repo


@pytest.fixture(name="repo")
def fixture_repo(tmp_path):
    """A RepoConfig for an already-cloned repository, without credentials."""
    return make_repo(
        git_dir=tmp_path / "workspace" / "common",
        work_tree=tmp_path / "app" / "assets" / "common",
    )


# --------------------------------------------------------------------------
# A pull that fails for good, and a remote without the branch
# --------------------------------------------------------------------------


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


def test_an_unborn_branch_has_nothing_to_push(monkeypatch, repo):
    """Test a repository started on an empty remote with no files yet is left alone."""
    fake_git = install_fake_git(
        monkeypatch, FakeGit(missing_refs={"refs/heads/main"}, outgoing=1)
    )

    assert push_if_ahead(repo) is False
    assert "push" not in fake_git.keys


# --------------------------------------------------------------------------
# What is never backed up
# --------------------------------------------------------------------------


def test_a_file_over_the_size_limit_is_held_back(monkeypatch, repo):
    """Test a large file is never staged, and the rest still is."""
    monkeypatch.setattr(checks, "MAX_FILE_BYTES", 10)
    repo.work_tree.mkdir(parents=True)
    (repo.work_tree / "big.bin").write_bytes(b"x" * 11)
    (repo.work_tree / "small.txt").write_bytes(b"x")
    fake_git = install_fake_git(
        monkeypatch,
        FakeGit(status_output="?? big.bin", untracked=["big.bin", "small.txt"]),
    )

    with pytest.raises(HeldBack, match=r"big\.bin") as excinfo:
        commit_local_changes(repo)

    assert excinfo.value.kind is SyncErrorKind.FILE_TOO_LARGE
    assert excinfo.value.committed is True
    assert fake_git.command_for("add")[-2:] == [":/", ":(exclude,top,literal)big.bin"]


def test_the_default_excludes_are_kept_beside_the_users_own(monkeypatch, repo):
    """Test virtual environments and caches are excluded, user lines kept."""
    exclude = repo.git_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True)
    exclude.write_text("my-own-pattern\n", encoding="utf-8")
    install_fake_git(monkeypatch, FakeGit())

    commit_local_changes(repo)
    commit_local_changes(repo)

    text = exclude.read_text(encoding="utf-8")
    assert text.startswith("my-own-pattern\n")
    assert ".venv/" in text and ".ipynb_checkpoints/" in text
    assert text.count(checks.EXCLUDE_BLOCK_START) == 1
