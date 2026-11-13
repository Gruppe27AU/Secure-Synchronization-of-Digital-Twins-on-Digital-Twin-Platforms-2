"""
Unit tests for :mod:`admin.git.repo.commit`.

Real git is never invoked: :class:`tests.helpers.fake_git.FakeGit` answers
every command.
"""

import re

import pytest

from admin.git.repo import checks
from admin.git.repo.commit import (
    build_commit_message,
    commit_local_changes,
    has_changes,
    HeldBack,
)
from admin.git.repo.sync import sync_once
from admin.git.run.errors import SyncError, SyncErrorKind
from tests.helpers.fake_git import FakeGit, install_fake_git


MARKED_NOTES = {
    "status_output": "M  notes.md",
    "marker_output": (
        "notes.md:2: leftover conflict marker\n"
        "notes.md:4: leftover conflict marker\n"
        "notes.md:6: leftover conflict marker\n"
    ),
    "staged_files": {
        "notes.md": "a\n<<<<<<< HEAD\nb\n=======\nc\n>>>>>>> origin\n"
    },
}


def test_commit_local_changes_commits_a_dirty_working_tree(monkeypatch, repo):
    """Test a modified working tree is staged and committed."""
    fake_git = install_fake_git(monkeypatch, FakeGit(status_output=" M notebook.ipynb"))

    assert commit_local_changes(repo) is True
    # Gate, check for deletions and large files, stage, check for markers,
    # then commit what is left staged.
    assert fake_git.keys == [
        "head",
        "unmerged",
        "status",
        "deleted-files",
        "untracked-files",
        "modified-files",
        "add",
        "marker-check",
        "staged-check",
        "commit",
    ]


def test_commit_local_changes_skips_a_clean_working_tree(monkeypatch, repo):
    """Test a clean working tree is left alone entirely."""
    fake_git = install_fake_git(monkeypatch, FakeGit(status_output=""))

    assert commit_local_changes(repo) is False
    assert fake_git.keys == ["head", "unmerged", "status"]


def test_commit_holds_back_a_file_with_conflict_markers(monkeypatch, repo):
    """Test a file still holding markers is left out and reported."""
    fake_git = install_fake_git(monkeypatch, FakeGit(**MARKED_NOTES, staged=False))

    with pytest.raises(
        HeldBack, match=r"conflict markers left in notes\.md$"
    ) as excinfo:
        commit_local_changes(repo)

    assert excinfo.value.kind is SyncErrorKind.CONFLICT_MARKERS
    assert excinfo.value.committed is False
    # Only that file is unstaged, so the working tree is left as the user had it.
    assert fake_git.command_for("unstage")[-2:] == ["--", "notes.md"]
    assert "commit" not in fake_git.keys


def test_one_marked_file_does_not_block_the_rest(monkeypatch, repo):
    """Test the other changes are still committed, pulled and pushed."""
    fake_git = install_fake_git(monkeypatch, FakeGit(**MARKED_NOTES, outgoing=1))

    with pytest.raises(SyncError) as excinfo:
        sync_once(repo)

    assert excinfo.value.kind is SyncErrorKind.CONFLICT_MARKERS
    assert fake_git.keys.index("unstage") < fake_git.keys.index("commit")
    assert "push" in fake_git.keys


def test_commit_ignores_whitespace_warnings(monkeypatch, repo):
    """Test trailing whitespace, which the same check reports, is committed."""
    fake_git = install_fake_git(
        monkeypatch,
        FakeGit(
            status_output=" M notes.md",
            marker_output="notes.md:1: trailing whitespace.\n+text   \n",
        ),
    )

    assert commit_local_changes(repo) is True
    assert "commit" in fake_git.keys


def test_commit_ignores_a_markdown_heading_underline(monkeypatch, repo):
    """Test a bare '=======', which git also reports, is committed."""
    fake_git = install_fake_git(
        monkeypatch,
        FakeGit(
            status_output=" M notes.md",
            marker_output="notes.md:2: leftover conflict marker\n",
            staged_files={"notes.md": "Results\n=======\n"},
        ),
    )

    assert commit_local_changes(repo) is True
    assert "commit" in fake_git.keys


def test_commit_message_contains_a_timestamp():
    """Test the commit message ends in a UTC timestamp."""
    assert re.fullmatch(
        r"workspace backup \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        build_commit_message(),
    )


def test_commit_sets_an_author_identity(monkeypatch, repo):
    """Test the commit carries an identity, which cloning does not set."""
    fake_git = install_fake_git(monkeypatch, FakeGit(status_output=" M file.txt"))

    commit_local_changes(repo)

    commit_command = fake_git.command_for("commit")
    assert "user.name=workspace-admin" in commit_command
    assert "user.email=workspace-admin@localhost" in commit_command


def test_has_changes_raises_when_status_fails(monkeypatch, repo):
    """Test an unreadable working tree is surfaced as SyncError."""
    install_fake_git(monkeypatch, FakeGit(failing="status"))

    with pytest.raises(SyncError, match="read the status"):
        has_changes(repo)


def test_commit_refuses_when_most_tracked_files_are_missing(monkeypatch, repo):
    """Test an emptied working tree is not committed as a mass deletion."""
    fake_git = install_fake_git(
        monkeypatch,
        FakeGit(status_output=" D f.txt", deleted_files=11, tracked_files=20),
    )

    with pytest.raises(SyncError, match="11 of 20 tracked files") as excinfo:
        commit_local_changes(repo)

    assert excinfo.value.kind is SyncErrorKind.MASS_DELETION
    assert "add" not in fake_git.keys
    assert "commit" not in fake_git.keys


def test_a_file_over_the_size_limit_is_held_back(monkeypatch, repo):
    """Test a large file is never staged, and the rest still is."""
    monkeypatch.setattr(checks, "MAX_FILE_BYTES", 10)
    repo.work_tree.mkdir(parents=True, exist_ok=True)
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
