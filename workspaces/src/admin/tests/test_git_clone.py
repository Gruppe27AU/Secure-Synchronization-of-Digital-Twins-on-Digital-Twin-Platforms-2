"""
Unit tests for cloning git-backed workspace assets.

Covers :mod:`admin.git.clone`: skipping an already-cloned repository,
performing a successful clone, and surfacing a failed clone as
:class:`admin.git.clone.CloneError`.
"""

import subprocess

import pytest

from admin.git.clone import CloneError, clone_asset, is_cloned
from admin.git.config import RepoConfig


@pytest.fixture(name="repo")
def fixture_repo(tmp_path):
    """A RepoConfig pointing at not-yet-existing git_dir/work_tree paths."""
    return RepoConfig(
        name="common",
        repo_url="https://example.com/org/common.git",
        branch="main",
        username="",
        token="",
        git_dir=tmp_path / "workspace" / "common",
        work_tree=tmp_path / "app" / "assets" / "common",
    )


def _fake_run_success(*_args, **_kwargs):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def _fake_run_failure(*_args, **_kwargs):
    return subprocess.CompletedProcess(
        args=[], returncode=128, stdout="", stderr="fatal: repository not found"
    )


def test_is_cloned_false_when_git_dir_missing(repo):
    """Test is_cloned returns False when the git directory doesn't exist."""
    assert is_cloned(repo.git_dir) is False


def test_is_cloned_true_when_head_present(repo):
    """Test is_cloned returns True once a HEAD file exists in git_dir."""
    repo.git_dir.mkdir(parents=True)
    (repo.git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    assert is_cloned(repo.git_dir) is True


def test_clone_asset_success(monkeypatch, repo):
    """Test a successful clone creates directories and returns True."""
    captured_command = {}

    def fake_run(command, **_kwargs):
        captured_command["command"] = command
        return _fake_run_success()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert clone_asset(repo) is True
    assert repo.work_tree.is_dir()
    command = captured_command["command"]
    assert command[0:2] == ["git", "clone"]
    assert "--branch" in command
    assert repo.branch in command
    assert f"--separate-git-dir={repo.git_dir}" in command
    assert repo.repo_url in command
    assert str(repo.work_tree) in command


def test_clone_asset_skips_when_already_cloned(monkeypatch, repo):
    """Test clone_asset skips cloning and does not invoke git."""
    repo.git_dir.mkdir(parents=True)
    (repo.git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("subprocess.run should not be called when already cloned")

    monkeypatch.setattr(subprocess, "run", fail_if_called)

    assert clone_asset(repo) is False


def test_clone_asset_raises_on_failure(monkeypatch, repo):
    """Test clone_asset raises CloneError when git clone fails."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run_failure())

    with pytest.raises(CloneError, match="repository not found"):
        clone_asset(repo)


def _fake_run_auth_failure(*_args, **_kwargs):
    return subprocess.CompletedProcess(
        args=[],
        returncode=128,
        stdout="",
        stderr=(
            "remote: HTTP Basic: Access denied. The provided password or "
            "token is incorrect or your account has 2FA enabled\n"
            "fatal: Authentication failed for "
            "'https://example.com/org/common.git/'"
        ),
    )


def test_clone_asset_raises_clear_error_on_invalid_token(monkeypatch, repo):
    """Test an invalid token surfaces a CloneError with a clear hint, no token."""
    repo = RepoConfig(
        name=repo.name,
        repo_url=repo.repo_url,
        branch=repo.branch,
        username="git-user",
        token="invalid-token",
        git_dir=repo.git_dir,
        work_tree=repo.work_tree,
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run_auth_failure())

    with pytest.raises(CloneError) as excinfo:
        clone_asset(repo)

    message = str(excinfo.value)
    assert "invalid or expired" in message
    assert "invalid-token" not in message


def test_clone_asset_raises_clear_error_on_expired_token(monkeypatch, repo):
    """Test an expired token gets the same clear hint as an invalid one."""
    repo = RepoConfig(
        name=repo.name,
        repo_url=repo.repo_url,
        branch=repo.branch,
        username="git-user",
        token="expired-token",
        git_dir=repo.git_dir,
        work_tree=repo.work_tree,
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run_auth_failure())

    with pytest.raises(CloneError) as excinfo:
        clone_asset(repo)

    message = str(excinfo.value)
    assert "invalid or expired" in message
    assert "expired-token" not in message


def test_clone_asset_raises_plain_error_without_credentials(monkeypatch, repo):
    """Test an auth-shaped failure without configured credentials gets no hint."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run_auth_failure())

    with pytest.raises(CloneError) as excinfo:
        clone_asset(repo)

    assert "invalid or expired" not in str(excinfo.value)


def test_clone_asset_adds_auth_header_when_credentials_present(monkeypatch, repo):
    """Test clone_asset passes a one-off Authorization header, not a stored URL."""
    repo = RepoConfig(
        name=repo.name,
        repo_url=repo.repo_url,
        branch=repo.branch,
        username="git-user",
        token="secret-token",
        git_dir=repo.git_dir,
        work_tree=repo.work_tree,
    )
    captured_command = {}

    def fake_run(command, **_kwargs):
        captured_command["command"] = command
        return _fake_run_success()

    monkeypatch.setattr(subprocess, "run", fake_run)

    clone_asset(repo)

    command = captured_command["command"]
    assert "-c" in command
    header_index = command.index("-c") + 1
    assert command[header_index].startswith("http.extraHeader=Authorization: Basic ")
    # The token must never appear in plain text in the command.
    assert "secret-token" not in " ".join(command)
