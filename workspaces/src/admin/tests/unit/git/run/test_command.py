"""
Unit tests for :mod:`admin.git.run.command`, the one runner for git commands.
:func:`admin.git.run.runner.run_git` is replaced, so git never runs.
"""

import subprocess
from dataclasses import replace

import pytest

from admin.git.run import command
from admin.git.run.auth import AUTH_FAILURE_HINT
from admin.git.run.command import (
    count_commits,
    list_paths,
    output_of,
    ref_exists,
    run,
    run_local,
    run_remote,
)
from admin.git.run.errors import SyncError, SyncErrorKind
from admin.git.run.runner import LOCAL_TIMEOUT_SECONDS, REMOTE_TIMEOUT_SECONDS, GitTimeoutError
from tests.helpers.repo_factory import make_repo


@pytest.fixture(name="repo")
def fixture_repo(tmp_path):
    """A repository without credentials."""
    return make_repo(git_dir=tmp_path / "git", work_tree=tmp_path / "tree")


@pytest.fixture(name="calls")
def fixture_calls(monkeypatch):
    """Records each call to run_git; ``calls.answer`` sets what it returns."""

    class Calls(list):
        """The recorded (command, timeout, env) calls."""

        answer = subprocess.CompletedProcess([], 0, "", "")

    calls = Calls()

    def fake_run_git(cmd, timeout, env=None):
        calls.append((cmd, timeout, env))
        return calls.answer

    monkeypatch.setattr(command, "run_git", fake_run_git)
    return calls


def _failed(stderr="", stdout=""):
    return subprocess.CompletedProcess([], 1, stdout, stderr)


def test_a_local_command_names_the_repository_before_the_subcommand(repo, calls):
    """Test -C, --git-dir, --work-tree and options come before the subcommand."""
    run_local(repo, ["status"], "read the status", options=["-c", "a=b"])

    cmd, timeout, env = calls[0]
    assert cmd == [
        "git", "-C", str(repo.work_tree), f"--git-dir={repo.git_dir}",
        f"--work-tree={repo.work_tree}", "-c", "a=b", "status",
    ]
    assert timeout == LOCAL_TIMEOUT_SECONDS
    assert env is None


def test_a_remote_command_gets_the_remote_options_and_credentials(repo, calls):
    """Test https only, the token in the environment and the long time limit."""
    run_remote(replace(repo, token="secret-token"), ["fetch"], "fetch")

    cmd, timeout, env = calls[0]
    assert cmd[:3] == ["git", "-c", "protocol.allow=never"]
    assert cmd[-1] == "fetch"
    assert timeout == REMOTE_TIMEOUT_SECONDS
    assert env["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")
    assert "secret-token" not in " ".join(cmd)


def test_a_failure_is_raised_with_its_kind_and_output(repo, calls):
    """Test the message names the action and repository; git_output is kept."""
    calls.answer = _failed("fatal: something broke")

    with pytest.raises(SyncError, match="Failed to merge for repository 'common'") as excinfo:
        run(repo, ["merge"], "merge")

    assert excinfo.value.kind is SyncErrorKind.GIT_FAILED
    assert excinfo.value.git_output == "fatal: something broke"


@pytest.mark.parametrize(("token", "hinted"), [("secret-token", True), ("", False)])
def test_a_rejected_token_gets_the_hint_only_with_a_token(repo, calls, token, hinted):
    """Test the token hint is only given when a token was sent."""
    calls.answer = _failed("fatal: Authentication failed for 'https://x/'")

    with pytest.raises(SyncError) as excinfo:
        run(replace(repo, token=token), ["fetch"], "fetch", remote=True)

    assert excinfo.value.kind is SyncErrorKind.AUTH_FAILED
    assert (AUTH_FAILURE_HINT in str(excinfo.value)) is hinted


def test_an_unchecked_failure_is_returned(repo, calls):
    """Test check=False hands back a failed command instead of raising."""
    calls.answer = _failed("CONFLICT")

    assert run_local(repo, ["merge"], "merge", check=False).returncode == 1


def test_a_time_out_is_its_own_kind(repo, monkeypatch):
    """Test a hung command is a timeout, not a git failure."""

    def hang(*_args, **_kwargs):
        raise GitTimeoutError("git did not finish within 1 seconds")

    monkeypatch.setattr(command, "run_git", hang)

    with pytest.raises(SyncError, match="did not finish") as excinfo:
        run_local(repo, ["status"], "read the status", check=False)

    assert excinfo.value.kind is SyncErrorKind.TIMEOUT


@pytest.mark.parametrize(
    ("stderr", "stdout", "expected"),
    [(" err \n", "out", "err"), ("", " out\n", "out"), ("", "", "")],
)
def test_output_of_prefers_stderr(stderr, stdout, expected):
    """Test what git printed is read from stderr first, then stdout."""
    assert output_of(_failed(stderr, stdout)) == expected


def test_the_small_helpers_read_git_output(repo, calls):
    """Test ref_exists, count_commits and list_paths read git's answer."""
    calls.answer = subprocess.CompletedProcess([], 0, "a\0dir/b c\0", "")
    assert list_paths(repo, ["--cached"], "list") == ["a", "dir/b c"]

    calls.answer = subprocess.CompletedProcess([], 0, "", "")
    assert count_commits(repo, "HEAD..origin/main", "count") == 0
    assert ref_exists(repo, "refs/heads/main") is True

    calls.answer = _failed()
    assert ref_exists(repo, "refs/heads/main") is False
