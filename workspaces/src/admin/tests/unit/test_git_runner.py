"""
Unit tests for running git as a child process.

Covers :mod:`admin.git.runner`: prompts switched off, empty standard input,
failures returned rather than raised, and a command that overruns its time
limit being stopped - politely first, then by force, together with every
process it started. The commands are small Python scripts rather than git,
so each behaviour can be provoked on purpose.
"""

import os
import sys
import time

import pytest

from admin.git import runner
from admin.git.runner import GitTimeoutError, run_git

posix_only = pytest.mark.skipif(
    os.name != "posix", reason="process groups and SIGTERM are POSIX-only"
)
linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="reads process state from /proc"
)


def _python(code):
    """Build a command that runs ``code`` in a fresh Python interpreter."""
    return [sys.executable, "-c", code]


def test_prompts_are_switched_off(monkeypatch):
    """Test git can neither prompt on a terminal nor start an askpass helper."""
    # Set the way VS Code sets it, which is what the empty value must beat.
    monkeypatch.setenv("GIT_ASKPASS", "/path/to/vscode/askpass.sh")

    result = run_git(
        _python(
            "import os; "
            "print(repr(os.environ['GIT_TERMINAL_PROMPT'])); "
            "print(repr(os.environ['GIT_ASKPASS']))"
        ),
        timeout=30,
    )

    assert result.stdout.split() == ["'0'", "''"]


def test_standard_input_is_empty():
    """Test a command reading standard input gets end-of-file at once."""
    result = run_git(
        _python("import sys; print(repr(sys.stdin.read()))"), timeout=30
    )

    assert result.stdout.strip() == "''"


def test_a_failure_is_returned_not_raised():
    """Test a non-zero exit comes back as a result, as with subprocess.run."""
    result = run_git(
        _python("import sys; sys.stderr.write('fatal: nope'); sys.exit(128)"),
        timeout=30,
    )

    assert result.returncode == 128
    assert result.stderr == "fatal: nope"


def test_an_overrunning_command_is_stopped_and_reported():
    """Test a command past its time limit is stopped and raises."""
    started = time.monotonic()

    with pytest.raises(GitTimeoutError, match="within 0.5 seconds"):
        run_git(_python("import time; time.sleep(60)"), timeout=0.5)

    assert time.monotonic() - started < 30


@posix_only
def test_the_command_may_clean_up_before_it_is_killed(tmp_path):
    """Test SIGTERM comes first, so git can remove a half-finished clone."""
    marker = tmp_path / "cleaned-up"
    code = (
        "import signal, sys, time\n"
        "def stop(*_):\n"
        f"    open({str(marker)!r}, 'w').close()\n"
        "    sys.exit(1)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "time.sleep(60)\n"
    )

    with pytest.raises(GitTimeoutError):
        run_git(_python(code), timeout=1)

    assert marker.exists()


@posix_only
def test_a_command_ignoring_sigterm_is_killed(monkeypatch):
    """Test a command that will not stop politely is still stopped."""
    monkeypatch.setattr(runner, "_TERMINATE_GRACE_SECONDS", 0.5)
    code = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()

    with pytest.raises(GitTimeoutError):
        run_git(_python(code), timeout=1)

    assert time.monotonic() - started < 30


@linux_only
def test_processes_the_command_started_are_stopped_too(tmp_path):
    """Test helpers such as git-remote-https do not outlive the command."""
    pid_file = tmp_path / "child.pid"
    code = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )

    with pytest.raises(GitTimeoutError):
        run_git(_python(code), timeout=2)

    child_pid = int(pid_file.read_text(encoding="utf-8"))
    # The child is an orphan once its parent is gone, so nothing reaps it:
    # it is gone when it no longer exists or is only a zombie.
    for _ in range(50):
        if not _is_running(child_pid):
            break
        time.sleep(0.1)
    assert not _is_running(child_pid)


def _is_running(pid):
    """Return whether ``pid`` is alive and not just waiting to be reaped."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stat:
            return stat.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False
