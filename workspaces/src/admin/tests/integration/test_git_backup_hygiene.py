"""
Integration tests for what the backup leaves out, with real git.

Ported from the reproductions of review round 5: one file holding a line
that looks like a conflict marker no longer blocks every other file, a file
too large to back up is left out on its own, and folders that are rebuilt
rather than kept are never staged.
"""

import os
import subprocess

import pytest

from admin.git import checks
from admin.git.sync import SyncError, SyncErrorKind, sync_once
from tests.helpers.git_world import git, write


def test_a_marker_line_does_not_block_other_files(world):
    """Test the rest of the work is backed up and the marked file waits."""
    write(world.workspace.work_tree / "doc.md", "x\n<<<<<<< HEAD\n")
    write(world.workspace.work_tree / "other.md", "unrelated work\n")

    with pytest.raises(SyncError) as excinfo:
        sync_once(world.workspace)

    assert excinfo.value.kind is SyncErrorKind.CONFLICT_MARKERS
    assert world.remote_file("other.md") == "unrelated work\n"
    assert world.remote_file("doc.md") is None
    assert (world.workspace.work_tree / "doc.md").exists()


def test_gitattributes_lets_marker_like_lines_through(world):
    """Test the documented escape hatch: a longer marker size for a path."""
    write(world.workspace.work_tree / ".gitattributes", "*.md conflict-marker-size=32\n")
    write(world.workspace.work_tree / "doc.md", "x\n<<<<<<< HEAD\n")

    assert sync_once(world.workspace) is True

    assert world.remote_file("doc.md") == "x\n<<<<<<< HEAD\n"


def test_a_file_over_the_size_limit_is_left_out(world, monkeypatch):
    """Test a large file stays local and everything else is pushed."""
    monkeypatch.setattr(checks, "MAX_FILE_BYTES", 100)
    write(world.workspace.work_tree / "big.bin", "x" * 101)
    write(world.workspace.work_tree / "small.txt", "small\n")

    with pytest.raises(SyncError) as excinfo:
        sync_once(world.workspace)

    assert excinfo.value.kind is SyncErrorKind.FILE_TOO_LARGE
    assert world.remote_file("small.txt") == "small\n"
    assert world.remote_file("big.bin") is None


def test_virtual_environments_and_caches_are_never_staged(world):
    """Test the default excludes keep rebuilt folders out of the repository."""
    tree = world.workspace.work_tree
    (tree / ".venv" / "lib").mkdir(parents=True)
    write(tree / ".venv" / "lib" / "site.py", "x\n")
    (tree / ".ipynb_checkpoints").mkdir()
    write(tree / ".ipynb_checkpoints" / "nb-checkpoint.ipynb", "{}\n")
    write(tree / "analysis.py", "print(1)\n")

    assert sync_once(world.workspace) is True

    tracked = git(world.remote, "ls-tree", "-r", "--name-only", "main")
    assert "analysis.py" in tracked
    assert ".venv" not in tracked
    assert ".ipynb_checkpoints" not in tracked


@pytest.mark.skipif(os.name != "posix", reason="needs a non-UTF-8 file name")
def test_a_file_name_that_is_not_utf8_is_backed_up(world):
    """Test a Latin-1 name neither stops the cycle nor holds back other work."""
    tree = os.fsencode(world.workspace.work_tree)
    with open(os.path.join(tree, b"\xe6ble.txt"), "wb") as latin1:
        latin1.write(b"apple\n")
    write(world.workspace.work_tree / "other.txt", "unrelated work\n")

    assert sync_once(world.workspace) is True

    tracked = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--name-only", "main"],
        cwd=world.remote,
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    assert b"\xe6ble.txt" in tracked
    assert world.remote_file("other.txt") == "unrelated work\n"


def _write_marker(tree, path):
    write(tree / path, "x\n<<<<<<< HEAD\n")


def _write_large(tree, path):
    write(tree / path, "x" * 101)


@pytest.mark.parametrize(
    "case",
    [
        ("model.txt", _write_marker, SyncErrorKind.CONFLICT_MARKERS,
         "remove the conflict markers"),
        ("doc.md", _write_marker, SyncErrorKind.CONFLICT_MARKERS,
         "remove the conflict markers"),
        ("big.bin", _write_large, SyncErrorKind.FILE_TOO_LARGE,
         "remove or shrink it"),
    ],
    ids=["tracked-marker", "untracked-marker", "untracked-large"],
)
def test_a_held_back_file_the_remote_changed_is_named(world, monkeypatch, case):
    """Test the held-back file is reported, not taken for a lasting failure."""
    path, hold_back, kind, hint = case
    monkeypatch.setattr(checks, "MAX_FILE_BYTES", 100)
    world.colleague_pushes(lambda tree: write(tree / path, "remote\n"))
    hold_back(world.workspace.work_tree, path)
    local = (world.workspace.work_tree / path).read_bytes()

    with pytest.raises(SyncError, match=rf"{path} is held back.*{hint}") as excinfo:
        sync_once(world.workspace)

    assert excinfo.value.kind is kind
    assert world.remote_file(path) == "remote\n"
    assert (world.workspace.work_tree / path).read_bytes() == local
