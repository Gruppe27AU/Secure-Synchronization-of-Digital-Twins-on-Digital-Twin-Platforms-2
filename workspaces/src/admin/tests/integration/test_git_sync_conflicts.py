"""
Integration tests for resolving merge conflicts with real git.

A bare repository in ``tmp_path`` stands in for the remote. The workspace
is cloned with :func:`admin.git.clone.clone_asset` and synchronized with
:func:`admin.git.sync.sync_once`; a second clone plays a "colleague".
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from admin.git import clone as clone_module
from admin.git import sync as sync_module
from admin.git.clone import clone_asset
from admin.git.config import RepoConfig
from admin.git.sync import sync_once
from tests.helpers.repo_factory import make_repo

NOTEBOOK = "".join(f"line{number}\n" for number in range(1, 9))


def _git(cwd, *args):
    """Run git as the colleague and return its output."""
    result = subprocess.run(
        ["git", "-c", "user.name=colleague", "-c", "user.email=c@example.com",
         *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _write(path, content):
    """Write a file with LF line endings on every platform."""
    path.write_bytes(content.encode())


@dataclass
class World:
    """The remote, the workspace and the colleague of one test."""

    remote: Path
    workspace: RepoConfig
    colleague: Path

    def colleague_pushes(self, change):
        """Let the colleague change their clone, commit and push."""
        change(self.colleague)
        _git(self.colleague, "add", "-A")
        _git(self.colleague, "commit", "-m", "colleague")
        _git(self.colleague, "push", "origin", "main")

    def remote_file(self, path):
        """Return a file on the remote's main branch, or None if absent."""
        try:
            return _git(self.remote, "show", f"main:{path}")
        except subprocess.CalledProcessError:
            return None


@pytest.fixture(name="world")
def fixture_world(tmp_path, monkeypatch):
    """A remote holding four files, cloned by the workspace and a colleague."""
    # Keep the user's own git config, such as autocrlf, out of the test.
    global_config = tmp_path / "gitconfig"
    global_config.touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    # The remote is a local path, which the https-only options would refuse.
    monkeypatch.setattr(sync_module, "remote_options", lambda repo: [])
    monkeypatch.setattr(clone_module, "remote_options", lambda repo: [])

    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(remote))

    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "--quiet", str(remote), str(seed))
    _write(seed / "notebook.txt", NOTEBOOK)
    _write(seed / "model.txt", "model v1\n")
    _write(seed / "old.txt", "old v1\n")
    _write(seed / "other.txt", "other v1\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "base")
    _git(seed, "push", "--quiet", "origin", "main")

    workspace = make_repo(
        repo_url=str(remote),
        git_dir=tmp_path / "workspace" / "git",
        work_tree=tmp_path / "app" / "tree",
    )
    clone_asset(workspace)

    colleague = tmp_path / "colleague"
    _git(tmp_path, "clone", "--quiet", str(remote), str(colleague))

    return World(remote=remote, workspace=workspace, colleague=colleague)


def test_local_change_beats_a_remote_deletion(world):
    """Test a file deleted remotely but changed locally is pushed back."""
    world.colleague_pushes(lambda tree: (tree / "model.txt").unlink())
    _write(world.workspace.work_tree / "model.txt", "local work\n")

    assert sync_once(world.workspace) is True

    assert world.remote_file("model.txt") == "local work\n"


def test_local_deletion_beats_a_remote_change(world):
    """Test a file deleted locally but changed remotely stays deleted."""
    world.colleague_pushes(lambda tree: _write(tree / "old.txt", "old v2\n"))
    (world.workspace.work_tree / "old.txt").unlink()

    assert sync_once(world.workspace) is True

    assert world.remote_file("old.txt") is None
    assert not (world.workspace.work_tree / "old.txt").exists()


def test_conflicted_file_keeps_no_remote_lines(world):
    """Test the whole local file wins, not only its conflicting lines."""
    world.colleague_pushes(lambda tree: _write(
        tree / "notebook.txt",
        NOTEBOOK.replace("line1\n", "COLLEAGUE1\n").replace(
            "line8\n", "COLLEAGUE8\n"),
    ))
    local = NOTEBOOK.replace("line1\n", "LOCAL1\n")
    _write(world.workspace.work_tree / "notebook.txt", local)

    sync_once(world.workspace)

    assert world.remote_file("notebook.txt") == local
    assert (world.workspace.work_tree / "notebook.txt").read_bytes() == (
        local.encode()
    )


def test_files_without_a_conflict_get_the_remote_changes(world):
    """Test the colleague's work in other files survives a conflict."""
    def change(tree):
        _write(tree / "notebook.txt", NOTEBOOK.replace("line1\n", "COLLEAGUE1\n"))
        _write(tree / "other.txt", "other v2\n")

    world.colleague_pushes(change)
    _write(
        world.workspace.work_tree / "notebook.txt",
        NOTEBOOK.replace("line1\n", "LOCAL1\n"),
    )

    sync_once(world.workspace)

    assert (world.workspace.work_tree / "other.txt").read_bytes() == b"other v2\n"
    assert world.remote_file("other.txt") == "other v2\n"


def test_the_next_sync_after_a_conflict_is_clean(world):
    """Test a resolved conflict leaves nothing behind to block later syncs."""
    world.colleague_pushes(lambda tree: (tree / "model.txt").unlink())
    _write(world.workspace.work_tree / "model.txt", "local work\n")
    sync_once(world.workspace)

    assert sync_once(world.workspace) is False
