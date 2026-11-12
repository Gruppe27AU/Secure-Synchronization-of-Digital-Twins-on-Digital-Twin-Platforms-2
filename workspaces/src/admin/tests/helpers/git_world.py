"""
A remote, a workspace and a colleague for the integration tests.

A bare repository in ``tmp_path`` stands in for the remote. The workspace
is cloned with :func:`admin.git.repo.clone.clone_asset`; a second, ordinary
clone plays a "colleague" who pushes changes of their own.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from admin.git.run import command as command_module
from admin.git.repo.clone import clone_asset
from admin.git.setup.config import RepoConfig
from tests.helpers.repo_factory import make_repo

#: Content of the remote's ``notebook.txt``, eight numbered lines.
NOTEBOOK = "".join(f"line{number}\n" for number in range(1, 9))

#: The files the remote starts with, and their content.
SEED_FILES = {
    "notebook.txt": NOTEBOOK,
    "model.txt": "model v1\n",
    "old.txt": "old v1\n",
    "other.txt": "other v1\n",
}


def git(cwd, *args):
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


def write(path, content):
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
        git(self.colleague, "add", "-A")
        git(self.colleague, "commit", "-m", "colleague")
        git(self.colleague, "push", "origin", "main")

    def remote_file(self, path):
        """Return a file on the remote's main branch, or None if absent."""
        try:
            return git(self.remote, "show", f"main:{path}")
        except subprocess.CalledProcessError:
            return None


def make_world(tmp_path, monkeypatch):
    """
    Build a remote holding :data:`SEED_FILES`, cloned by the workspace and
    a colleague.

    Args:
        tmp_path: Directory everything is created in.
        monkeypatch: Used to isolate git from the user's own config and to
            allow a local remote.

    Returns:
        The world.
    """
    # Keep the user's own git config, such as autocrlf, out of the test.
    global_config = tmp_path / "gitconfig"
    global_config.touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    # The remote is a local path, which the https-only options would refuse.
    monkeypatch.setattr(command_module, "remote_options", lambda: [])

    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(remote))

    seed = tmp_path / "seed"
    git(tmp_path, "clone", "--quiet", str(remote), str(seed))
    for name, content in SEED_FILES.items():
        write(seed / name, content)
    git(seed, "add", "-A")
    git(seed, "commit", "-m", "base")
    git(seed, "push", "--quiet", "origin", "main")

    workspace = make_repo(
        repo_url=str(remote),
        git_dir=tmp_path / "workspace" / "git",
        work_tree=tmp_path / "app" / "tree",
    )
    clone_asset(workspace)

    colleague = tmp_path / "colleague"
    git(tmp_path, "clone", "--quiet", str(remote), str(colleague))

    return World(remote=remote, workspace=workspace, colleague=colleague)
