"""
Unit tests for :mod:`admin.git.repo.checks`, the checks on the files about to
be committed. Real git is never invoked: :class:`tests.helpers.fake_git.FakeGit`
answers every command.
"""

import pathlib

import pytest

from admin.git.repo import checks
from admin.git.repo.checks import (
    ensure_default_excludes,
    ensure_no_mass_deletion,
    large_files,
    staged_conflict_markers,
)
from admin.git.run.errors import SyncError, SyncErrorKind
from tests.helpers.fake_git import FakeGit, install_fake_git
from tests.helpers.repo_factory import make_repo

MIN = checks.MASS_DELETION_MIN_FILES


@pytest.fixture(name="repo")
def fixture_repo(tmp_path):
    """A repository whose git directory and work tree exist."""
    repo = make_repo(git_dir=tmp_path / "git", work_tree=tmp_path / "tree")
    repo.git_dir.mkdir()
    repo.work_tree.mkdir()
    return repo


# --------------------------------------------------------------------------
# Mass deletion
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("deleted", "tracked"),
    [
        (0, 50),
        (MIN - 1, MIN),  # few, and not all
        (10, 20),  # half is not more than half
    ],
)
def test_an_ordinary_deletion_is_allowed(monkeypatch, repo, deleted, tracked):
    """Test deletions short of the limits pass."""
    install_fake_git(monkeypatch, FakeGit(deleted_files=deleted, tracked_files=tracked))

    ensure_no_mass_deletion(repo)


@pytest.mark.parametrize(
    ("deleted", "tracked"),
    [
        (1, 1),  # a tree of one file, emptied
        (MIN - 1, MIN - 1),  # every file, fewer than the minimum
        (11, 20),  # most files, at least the minimum
    ],
)
def test_an_emptied_or_mostly_emptied_tree_is_refused(
    monkeypatch, repo, deleted, tracked
):
    """Test every file missing, or most of them, stops the commit."""
    install_fake_git(monkeypatch, FakeGit(deleted_files=deleted, tracked_files=tracked))

    with pytest.raises(SyncError, match=f"{deleted} of {tracked} tracked") as excinfo:
        ensure_no_mass_deletion(repo)

    assert excinfo.value.kind is SyncErrorKind.MASS_DELETION


def test_nothing_deleted_does_not_count_the_tracked_files(monkeypatch, repo):
    """Test the usual case costs one git command, not two."""
    fake_git = install_fake_git(monkeypatch, FakeGit())

    ensure_no_mass_deletion(repo)

    assert fake_git.keys == ["deleted-files"]


# --------------------------------------------------------------------------
# Large files
# --------------------------------------------------------------------------


def test_large_new_and_changed_files_are_listed(monkeypatch, repo):
    """Test only files over the limit are listed, each once, gone ones skipped."""
    monkeypatch.setattr(checks, "MAX_FILE_BYTES", 10)
    (repo.work_tree / "big.bin").write_bytes(b"x" * 11)
    (repo.work_tree / "edge.bin").write_bytes(b"x" * 10)
    (repo.work_tree / "changed.dat").write_bytes(b"x" * 20)
    install_fake_git(
        monkeypatch,
        FakeGit(
            untracked=["big.bin", "edge.bin", "gone.bin"],
            modified=["changed.dat", "big.bin"],
        ),
    )

    assert large_files(repo) == ["big.bin", "changed.dat"]


# --------------------------------------------------------------------------
# Conflict markers
# --------------------------------------------------------------------------


def test_only_real_conflict_markers_are_listed(monkeypatch, repo):
    """Test whitespace errors and a Markdown underline are not markers."""
    install_fake_git(
        monkeypatch,
        FakeGit(
            marker_output=(
                "a.md:2: leftover conflict marker\n"
                "a.md:4: leftover conflict marker\n"
                "b.md:3: trailing whitespace.\n"
                "c.md:2: leftover conflict marker\n"
            ),
            staged_files={"a.md": "x\n<<<<<<< HEAD\n", "c.md": "Title\n=======\n"},
        ),
    )

    assert staged_conflict_markers(repo) == ["a.md"]


def test_a_failed_marker_check_is_an_error(monkeypatch, repo):
    """Test git failing, rather than finding markers, raises."""
    install_fake_git(monkeypatch, FakeGit(failing="marker-check"))

    with pytest.raises(SyncError, match="conflict markers"):
        staged_conflict_markers(repo)


# --------------------------------------------------------------------------
# Default excludes
# --------------------------------------------------------------------------


def test_an_outdated_block_is_replaced_and_user_lines_kept(repo):
    """Test the backup's block is rewritten in place of the old one."""
    exclude = repo.git_dir / "info" / "exclude"
    exclude.parent.mkdir()
    exclude.write_text(
        f"mine\n{checks.EXCLUDE_BLOCK_START}\nold-pattern\n"
        f"{checks.EXCLUDE_BLOCK_END}\nalso-mine",
        encoding="utf-8",
    )

    ensure_default_excludes(repo)

    text = exclude.read_text(encoding="utf-8")
    assert text.startswith("mine\nalso-mine\n")
    assert "old-pattern" not in text
    assert ".venv/" in text


def test_an_unwritable_exclude_file_is_logged(monkeypatch, repo, caplog):
    """Test the backup goes on when info/exclude cannot be written."""

    def refuse(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(pathlib.Path, "write_text", refuse)

    ensure_default_excludes(repo)

    assert "Cannot write the default excludes" in caplog.text
