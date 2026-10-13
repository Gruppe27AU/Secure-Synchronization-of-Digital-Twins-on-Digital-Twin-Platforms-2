"""
Running git as a child process.

Every git command of the backup goes through :func:`run_git`, so that no
command can wait forever - neither for a person nor for the network:

- Prompts are switched off. ``GIT_TERMINAL_PROMPT=0`` stops git asking on
  a terminal, and an empty ``GIT_ASKPASS`` stops it starting an askpass
  helper, which on the KASM desktop or under VS Code could open a dialog
  nobody answers. An empty value also overrides ``core.askPass`` and
  ``SSH_ASKPASS``. Credential helpers ignore both settings, so
  :func:`admin.git.auth.remote_options` resets those separately.
- Standard input is empty, so nothing can block reading it.
- Each command has a time limit. When it runs out, the command and every
  process it started are stopped, and :class:`GitTimeoutError` is raised.

This module knows nothing about repositories or configuration, so every
other module of :mod:`admin.git` can use it.
"""

import os
import signal
import subprocess

#: Time limit for a command that only works on the local disk, in seconds.
LOCAL_TIMEOUT_SECONDS = 300

#: Time limit for a command that talks to the remote, in seconds. Generous,
#: because a first clone of a large repository legitimately takes a while.
#: Once connected, a transfer that has stalled is stopped much sooner by the
#: ``http.lowSpeed*`` options in :func:`admin.git.auth.remote_options`; a
#: connection that stalls before that, during the TLS handshake for
#: example, is only stopped by this limit.
REMOTE_TIMEOUT_SECONDS = 900

#: Seconds a timed-out command gets to clean up after the polite stop
#: signal, before it is killed outright.
_TERMINATE_GRACE_SECONDS = 5

#: Environment additions that stop git from ever asking for input.
_NO_PROMPT_ENVIRONMENT = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""}

_POSIX = os.name == "posix"

#: Signal that cannot be ignored. Windows has no ``SIGKILL``; there both
#: stop signals end up as ``Popen.kill()`` anyway.
_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


class GitTimeoutError(Exception):
    """Raised when a git command does not finish within its time limit."""


def _environment() -> dict[str, str]:
    """
    Build the environment git runs in.

    Returns:
        The service's own environment with prompting switched off.
    """
    return {**os.environ, **_NO_PROMPT_ENVIRONMENT}


def _signal_group(process: subprocess.Popen[str], signum: int) -> None:
    """
    Send a signal to the command and every process it started.

    Git hands network work to helper processes such as
    ``git-remote-https``. Stopping only ``git`` would leave those running,
    so on POSIX the command runs in its own process group and the whole
    group is signalled. Elsewhere only the command itself can be reached.

    Args:
        process: The running command.
        signum: Signal to send.
    """
    try:
        if _POSIX:
            os.killpg(process.pid, signum)  # pylint: disable=no-member
        else:
            process.kill()
    except ProcessLookupError:
        pass


def _stop(process: subprocess.Popen[str]) -> None:
    """
    Stop a command that ran out of time.

    The polite signal comes first, because git cleans up after itself on
    ``SIGTERM`` - an interrupted clone, for example, removes the directory
    it had started - but cannot on ``SIGKILL``.

    Args:
        process: The running command.
    """
    _signal_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_group(process, _KILL_SIGNAL)
        process.wait()


def run_git(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """
    Run one git command without letting it prompt or hang.

    Args:
        command: The full command, starting with ``git``.
        timeout: Seconds the command may take.

    Returns:
        The completed process, with its output captured as text. A
        non-zero exit status is returned, not raised, as with
        :func:`subprocess.run`.

    Raises:
        GitTimeoutError: If the command did not finish in time. It has
            been stopped by then.
    """
    with subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_environment(),
        start_new_session=_POSIX,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            _stop(process)
            raise GitTimeoutError(
                f"git did not finish within {timeout:g} seconds and was stopped"
            ) from error

    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
