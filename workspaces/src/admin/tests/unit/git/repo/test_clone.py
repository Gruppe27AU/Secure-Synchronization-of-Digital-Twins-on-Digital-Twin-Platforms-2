"""
Unit tests for cloning git-backed workspace assets.

Covers :mod:`admin.git.repo.clone`: skipping an already-cloned repository,
performing a successful clone, and surfacing a failed clone as
:class:`admin.git.run.errors.SyncError`. Real git is never invoked -
:func:`admin.git.run.runner.run_git` is replaced for every test.
"""

import subprocess
from dataclasses import replace

import pytest

from admin.git.repo.clone import clone_asset, is_cloned, restore_git_file
from admin.git.run import command as command_module
from admin.git.run.errors import SyncError, SyncErrorKind
from admin.git.run.runner import GitTimeoutError, REMOTE_TIMEOUT_SECONDS
from tests.helpers.repo_factory import make_repo


@pytest.fixture(name="repo")
def fixture_repo(tmp_path):
    """A repository not cloned yet: neither path exists."""
    return make_repo(
        repo_url="https://example.com/org/common.git",
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

    def fake_run(command, _timeout, **_kwargs):
        captured_command["command"] = command
        return _fake_run_success()

    monkeypatch.setattr(command_module, "run_git", fake_run)

    assert clone_asset(repo) is True
    assert repo.work_tree.is_dir()
    command = captured_command["command"]
    assert command[0] == "git"
    assert "clone" in command
    assert command[command.index("--branch") + 1] == repo.branch
    assert f"--separate-git-dir={repo.git_dir}" in command
    assert command[-3:] == ["--", repo.repo_url, str(repo.work_tree)]


def test_clone_asset_allows_only_https(monkeypatch, repo):
    """Test the clone refuses every transport except https."""
    captured_command = {}

    def fake_run(command, _timeout, **_kwargs):
        captured_command["command"] = command
        return _fake_run_success()

    monkeypatch.setattr(command_module, "run_git", fake_run)

    clone_asset(repo)

    command = captured_command["command"]
    options = command[: command.index("clone")]
    assert "protocol.allow=never" in options
    assert "protocol.https.allow=always" in options


def test_clone_asset_skips_when_already_cloned(monkeypatch, repo):
    """Test clone_asset skips cloning and does not invoke git."""
    repo.git_dir.mkdir(parents=True)
    (repo.git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    repo.work_tree.mkdir(parents=True)
    (repo.work_tree / ".git").write_text(f"gitdir: {repo.git_dir}\n", encoding="utf-8")
    (repo.work_tree / "notes.md").write_text("work\n", encoding="utf-8")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("subprocess.run should not be called when already cloned")

    monkeypatch.setattr(command_module, "run_git", fail_if_called)

    assert clone_asset(repo) is False


def test_clone_asset_raises_on_failure(monkeypatch, repo):
    """Test clone_asset raises SyncError when git clone fails."""
    monkeypatch.setattr(command_module, "run_git", lambda *a, **k: _fake_run_failure())

    with pytest.raises(SyncError, match="repository not found"):
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


@pytest.mark.parametrize(
    ("token", "hinted"),
    [("invalid-token", True), ("expired-token", True), ("", False)],
)
def test_a_rejected_clone_gets_the_hint_only_with_a_token(monkeypatch, repo, token, hinted):
    """Test a rejected token is auth_failed, with a hint but never the token."""
    monkeypatch.setattr(command_module, "run_git", lambda *a, **k: _fake_run_auth_failure())

    with pytest.raises(SyncError) as excinfo:
        clone_asset(replace(repo, token=token))

    assert excinfo.value.kind is SyncErrorKind.AUTH_FAILED
    assert ("invalid or expired" in str(excinfo.value)) is hinted
    assert not token or token not in str(excinfo.value)


def test_clone_asset_adds_auth_header_when_credentials_present(monkeypatch, repo):
    """Test the header goes in the environment, scoped to the repository, not on argv."""
    repo = replace(repo, token="secret-token")
    captured = {}

    def fake_run(command, _timeout, env=None):
        captured["command"] = command
        captured["env"] = env
        return _fake_run_success()

    monkeypatch.setattr(command_module, "run_git", fake_run)

    clone_asset(repo)

    assert not any("extraHeader" in part for part in captured["command"])
    assert captured["env"]["GIT_CONFIG_KEY_0"] == (
        "http.https://example.com/org/common.git.extraHeader"
    )
    assert captured["env"]["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")
    # The token must never appear in plain text anywhere.
    assert "secret-token" not in " ".join(captured["command"])
    assert "secret-token" not in captured["env"]["GIT_CONFIG_VALUE_0"]


def test_clone_asset_uses_the_remote_time_limit(monkeypatch, repo):
    """Test the clone gets the long time limit meant for network commands."""
    captured = {}

    def fake_run(_command, timeout, **_kwargs):
        captured["timeout"] = timeout
        return _fake_run_success()

    monkeypatch.setattr(command_module, "run_git", fake_run)

    clone_asset(repo)

    assert captured["timeout"] == REMOTE_TIMEOUT_SECONDS


def test_clone_asset_raises_clone_error_on_timeout(monkeypatch, repo):
    """Test a clone that hangs is reported as a failed clone."""

    def fake_run(_command, _timeout, **_kwargs):
        raise GitTimeoutError("git did not finish within 900 seconds and was stopped")

    monkeypatch.setattr(command_module, "run_git", fake_run)

    with pytest.raises(SyncError, match="did not finish within 900 seconds") as excinfo:
        clone_asset(repo)

    assert excinfo.value.kind is SyncErrorKind.TIMEOUT


def _cloned(repo):
    """Lay out a cloned repository whose working tree has lost its .git file."""
    repo.git_dir.mkdir(parents=True)
    (repo.git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    repo.work_tree.mkdir(parents=True)
    return repo.work_tree / ".git"


def test_restore_git_file_writes_a_lost_git_file_back(repo, caplog):
    """Test the user's own git commands find the backup repository again."""
    git_file = _cloned(repo)

    with caplog.at_level("WARNING"):
        restore_git_file(repo)

    assert git_file.read_text(encoding="utf-8") == f"gitdir: {repo.git_dir}\n"
    assert "written back" in caplog.text


def test_restore_git_file_leaves_an_existing_git_file_alone(repo):
    """Test whatever is already at .git is never overwritten."""
    git_file = _cloned(repo)
    git_file.write_text("gitdir: elsewhere\n", encoding="utf-8")

    restore_git_file(repo)

    assert git_file.read_text(encoding="utf-8") == "gitdir: elsewhere\n"


def test_restore_git_file_does_nothing_before_the_clone(repo):
    """Test a working tree is not pointed at a git directory that is not there."""
    repo.work_tree.mkdir(parents=True)

    restore_git_file(repo)

    assert not (repo.work_tree / ".git").exists()


def test_a_work_tree_that_cannot_be_restored_is_logged(monkeypatch, repo, caplog):
    """Test a failed restore of a deleted work tree does not stop the startup."""
    repo.git_dir.mkdir(parents=True)
    (repo.git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    monkeypatch.setattr(command_module, "run_git", lambda *a, **k: _fake_run_failure())

    assert clone_asset(repo) is False

    assert "could not be restored" in caplog.text
