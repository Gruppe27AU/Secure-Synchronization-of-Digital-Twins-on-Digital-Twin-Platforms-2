"""
Integration tests for what the git backup commits, with real git.

A bare repository in ``tmp_path`` stands in for the remote, and the
workspace is cloned with :func:`admin.git.clone.clone_asset` and
synchronized with :func:`admin.git.sync.sync_once`. The size limit is
lowered to a few bytes, so no test needs large files.
"""

import subprocess
from pathlib import Path

import pytest

from admin.git import clone, sync
from admin.git.staging import EXCLUDES_BEGIN
from tests.helpers.repo_factory import make_repo

#: The size limit for these tests, in bytes.
LIMIT = 100

SMALL = b"small\n"
LARGE = b"x" * (LIMIT + 1)


def _git(cwd, *args):
    """Run git and return its output."""
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _write(path, content):
    """Create a file and any directories above it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.fixture(name="remote")
def fixture_remote(tmp_path, monkeypatch):
    """A bare remote holding one small file on main."""
    # Keep the user's own git config, such as autocrlf, out of the test.
    global_config = tmp_path / "gitconfig"
    global_config.write_text(
        "[user]\n\tname = seed\n\temail = seed@example.com\n", encoding="utf-8"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    # The remote is a local path, which the https-only options would refuse.
    for module in (clone, sync):
        monkeypatch.setattr(module, "remote_options", lambda repo: [])
    monkeypatch.setattr(sync, "MAX_FILE_SIZE_BYTES", LIMIT)

    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(remote))

    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "--quiet", str(remote), str(seed))
    _write(seed / "tracked.txt", SMALL)
    _git(seed, "add", "-A")
    _git(seed, "commit", "--quiet", "-m", "base")
    _git(seed, "push", "--quiet", "origin", "main")
    return remote


@pytest.fixture(name="workspace")
def fixture_workspace(tmp_path, remote):
    """The workspace's clone of the remote."""
    workspace = make_repo(
        repo_url=str(remote), git_dir=tmp_path / "git-dir", work_tree=tmp_path / "tree"
    )
    clone.clone_asset(workspace)
    return workspace


def _remote_files(remote):
    """The paths on the remote's main branch."""
    return set(_git(remote, "ls-tree", "-r", "--name-only", "main").splitlines())


def _stored(workspace, path):
    """Whether the workspace's git directory holds the file's content."""
    tree = Path(workspace.work_tree)
    object_id = _git(tree, "hash-object", "--", path).strip()
    result = subprocess.run(
        ["git", "cat-file", "-e", object_id],
        cwd=tree, capture_output=True, check=False,
    )
    return result.returncode == 0


def test_default_excludes_keep_clutter_and_secrets_out(workspace, remote):
    """Test caches, environments and credential files are never pushed."""
    tree = Path(workspace.work_tree)
    for clutter in (
        ".ipynb_checkpoints/notebook-checkpoint.ipynb",
        "__pycache__/model.cpython-312.pyc",
        "venv/lib/site.py",
        ".env",
        "deploy.pem",
    ):
        _write(tree / clutter, SMALL)
    _write(tree / ".env.example", SMALL)
    _write(tree / "results.csv", SMALL)

    assert sync.sync_once(workspace) is True

    assert _remote_files(remote) == {"tracked.txt", ".env.example", "results.csv"}
    exclude_file = Path(workspace.git_dir) / "info" / "exclude"
    assert EXCLUDES_BEGIN in exclude_file.read_text(encoding="utf-8")


def test_an_oversized_new_file_is_left_out_and_never_stored(workspace, remote):
    """Test the big file stays local, unstored, while the rest is pushed."""
    tree = Path(workspace.work_tree)
    _write(tree / "runs" / "output 1.bin", LARGE)
    _write(tree / "notes.txt", SMALL)

    assert sync.sync_once(workspace) is True

    assert _remote_files(remote) == {"tracked.txt", "notes.txt"}
    assert (tree / "runs" / "output 1.bin").read_bytes() == LARGE
    assert not _stored(workspace, "runs/output 1.bin")


def test_a_wildcard_in_an_oversized_name_leaves_out_only_that_file(
    workspace, remote
):
    """Test 'a*.bin' does not also leave out 'ab.bin'."""
    tree = Path(workspace.work_tree)
    try:
        _write(tree / "a*.bin", LARGE)
    except OSError:
        pytest.skip("this file system does not allow * in a file name")
    _write(tree / "ab.bin", SMALL)

    assert sync.sync_once(workspace) is True

    assert _remote_files(remote) == {"tracked.txt", "ab.bin"}


def test_a_tracked_file_that_grows_too_large_keeps_its_last_version(
    workspace, remote
):
    """Test the remote keeps the small version and the other change goes out."""
    tree = Path(workspace.work_tree)
    _write(tree / "tracked.txt", LARGE)
    _write(tree / "notes.txt", SMALL)

    assert sync.sync_once(workspace) is True

    assert _git(remote, "show", "main:tracked.txt") == SMALL.decode()
    assert "notes.txt" in _remote_files(remote)


def test_an_oversized_file_staged_by_hand_is_unstaged(workspace, remote):
    """Test the commit, which takes everything staged, does not take it."""
    tree = Path(workspace.work_tree)
    _write(tree / "big.bin", LARGE)
    _git(tree, "add", "big.bin")
    _write(tree / "notes.txt", SMALL)

    assert sync.sync_once(workspace) is True

    assert _remote_files(remote) == {"tracked.txt", "notes.txt"}
    assert _git(tree, "status", "--porcelain", "--", "big.bin") == "?? big.bin\n"


def test_only_oversized_changes_make_no_commit(workspace, remote, caplog):
    """Test a cycle with nothing committable commits and pushes nothing."""
    tree = Path(workspace.work_tree)
    _write(tree / "big.bin", LARGE)
    head_before = _git(remote, "rev-parse", "main")

    with caplog.at_level("WARNING"):
        assert sync.sync_once(workspace) is False

    assert _git(remote, "rev-parse", "main") == head_before
    assert "big.bin" in caplog.text
