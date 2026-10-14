"""
Unit tests for deciding what the git backup stages.

Covers :mod:`admin.git.staging`: reading git's status output, picking out
oversized files, building the pathspecs that leave them out, and keeping
the default excludes in ``info/exclude`` without touching the user's own
lines. Nothing here runs git.
"""

import os

import pytest

from admin.git.staging import (
    DEFAULT_EXCLUDES,
    EXCLUDES_BEGIN,
    EXCLUDES_END,
    literal_excludes,
    oversized_files,
    parse_status,
    write_default_excludes,
)

# --------------------------------------------------------------------------
# Reading git status
# --------------------------------------------------------------------------


def test_parse_status_reads_codes_and_paths():
    """Test each entry becomes its two-letter code and its path."""
    output = " M notebook.ipynb\0?? data/new run.csv\0D  old.txt\0"

    assert parse_status(output) == [
        (" M", "notebook.ipynb"),
        ("??", "data/new run.csv"),
        ("D ", "old.txt"),
    ]


def test_parse_status_keeps_unusual_names_whole():
    """Test a name with a newline or a leading space is not split or trimmed."""
    output = "?? line\nbreak.txt\0??  leading space.txt\0"

    assert parse_status(output) == [
        ("??", "line\nbreak.txt"),
        ("??", " leading space.txt"),
    ]


def test_parse_status_skips_the_old_name_of_a_rename():
    """Test the entry after a rename is its old name, not another change."""
    output = "R  new.txt\0old.txt\0 M other.txt\0"

    assert parse_status(output) == [("R ", "new.txt"), (" M", "other.txt")]


def test_parse_status_of_a_clean_tree_is_empty():
    """Test no output means nothing changed."""
    assert not parse_status("")


# --------------------------------------------------------------------------
# Picking out oversized files
# --------------------------------------------------------------------------


def _file(tmp_path, name, size):
    """Create a file of the given size and return its name."""
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return name


def test_oversized_files_picks_only_files_over_the_limit(tmp_path):
    """Test a file at the limit is kept and one byte more is left out."""
    at_limit = _file(tmp_path, "at.bin", 100)
    over = _file(tmp_path, "dir/over.bin", 101)

    assert oversized_files(tmp_path, [at_limit, over], 100) == [over]


def test_oversized_files_ignores_paths_that_are_gone(tmp_path):
    """Test a deleted file is never oversized, so its deletion is committed."""
    assert not oversized_files(tmp_path, ["deleted.bin"], 0)


def test_oversized_files_ignores_directories(tmp_path):
    """Test a directory git lists, such as a nested repository, is skipped."""
    (tmp_path / "nested").mkdir()

    assert not oversized_files(tmp_path, ["nested/"], 0)


def test_oversized_files_measures_a_symlink_not_its_target(tmp_path):
    """Test git stores a link as its target path, so the target's size is moot."""
    target = _file(tmp_path, "big.bin", 1000)
    try:
        os.symlink(tmp_path / target, tmp_path / "link")
    except OSError:
        pytest.skip("symbolic links cannot be created here")

    assert not oversized_files(tmp_path, ["link"], 100)


def test_literal_excludes_leave_wildcards_alone():
    """Test a name with * or [ is matched as it is, not as a pattern."""
    assert literal_excludes(["a*.bin", "dir/[1].csv"]) == [
        ":(exclude,literal)a*.bin",
        ":(exclude,literal)dir/[1].csv",
    ]


# --------------------------------------------------------------------------
# The default excludes in info/exclude
# --------------------------------------------------------------------------


def _exclude_file(git_dir):
    """The file the excludes are written to."""
    return git_dir / "info" / "exclude"


def test_write_default_excludes_creates_the_file(tmp_path):
    """Test a git directory without info/exclude gets one."""
    assert write_default_excludes(tmp_path) is True

    lines = _exclude_file(tmp_path).read_text(encoding="utf-8").splitlines()
    assert lines == [EXCLUDES_BEGIN, *DEFAULT_EXCLUDES, EXCLUDES_END]


def test_write_default_excludes_keeps_the_users_lines(tmp_path):
    """Test lines the user or git's template wrote stay where they are."""
    exclude_file = _exclude_file(tmp_path)
    exclude_file.parent.mkdir()
    exclude_file.write_text("# git's template comment\nmy-scratch/", encoding="utf-8")

    write_default_excludes(tmp_path)

    content = exclude_file.read_text(encoding="utf-8")
    assert content.startswith("# git's template comment\nmy-scratch/\n\n")
    assert EXCLUDES_BEGIN in content


def test_write_default_excludes_leaves_a_current_file_alone(tmp_path):
    """Test the file is only rewritten when something changed."""
    write_default_excludes(tmp_path)
    before = _exclude_file(tmp_path).read_text(encoding="utf-8")

    assert write_default_excludes(tmp_path) is False
    assert _exclude_file(tmp_path).read_text(encoding="utf-8") == before


def test_write_default_excludes_replaces_an_outdated_block(tmp_path):
    """Test a workspace cloned before the list changed gets the new list."""
    exclude_file = _exclude_file(tmp_path)
    exclude_file.parent.mkdir()
    exclude_file.write_text(
        f"mine/\n\n{EXCLUDES_BEGIN}\nold-pattern/\n{EXCLUDES_END}\n",
        encoding="utf-8",
    )

    assert write_default_excludes(tmp_path) is True

    content = exclude_file.read_text(encoding="utf-8")
    assert "old-pattern/" not in content
    assert content.count(EXCLUDES_BEGIN) == 1
    assert content.startswith("mine/\n\n")
