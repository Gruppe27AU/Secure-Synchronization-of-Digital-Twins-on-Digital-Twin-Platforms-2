"""
Running git commands against one cloned repository.

Every command names the git directory and working tree explicitly rather
than letting git find them through the ``.git`` file in the working tree.
That file is easily lost - a clean-up of hidden files, a folder restored
from a zip - and git would then walk up and commit into, and push from,
whatever repository encloses the workspace, credentials and all.

A failure becomes a :class:`admin.git.errors.SyncError` whose kind says what
the user can do about it: a rejected token, an unreachable remote, a
command that ran out of time, or anything else.
"""

import logging
import subprocess

from admin.git.auth import (
    AUTH_FAILURE_HINT,
    auth_environment,
    remote_options,
)
from admin.git.config import RepoConfig
from admin.git.errors import SyncError, SyncErrorKind, failure_kind
from admin.git.runner import (
    LOCAL_TIMEOUT_SECONDS,
    REMOTE_TIMEOUT_SECONDS,
    GitTimeoutError,
    run_git,
)

logger = logging.getLogger(__name__)


def repository_options(repo: RepoConfig) -> list[str]:
    """
    Build the git options that name the repository explicitly.

    Without them git finds the repository through the ``.git`` file in the
    working tree, and when that file is gone, through whatever repository
    encloses the working tree.

    Args:
        repo: Repository the command is for.

    Returns:
        The ``--git-dir=...`` and ``--work-tree=...`` options.
    """
    return [f"--git-dir={repo.git_dir}", f"--work-tree={repo.work_tree}"]


def _run(
    repo: RepoConfig,
    command: list[str],
    action: str,
    timeout: float,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run a built git command, turning a time-out into :class:`SyncError`.

    Args:
        repo: Repository the command is for.
        command: The full command, starting with ``git``.
        action: What the command is doing, used in error messages.
        timeout: Seconds the command may take.
        env: Variables added to git's environment for this command.

    Returns:
        The completed process, which may describe a failed command.

    Raises:
        SyncError: With :attr:`SyncErrorKind.TIMEOUT` if the command does not
            finish in time.
    """
    try:
        return run_git(command, timeout, env=env)
    except GitTimeoutError as error:
        logger.error(
            "Failed to %s for repository '%s': %s", action, repo.name, error
        )
        raise SyncError(
            f"Failed to {action} for repository '{repo.name}': {error}",
            SyncErrorKind.TIMEOUT,
        ) from error


def _raise_if_failed(
    repo: RepoConfig, result: subprocess.CompletedProcess[str], action: str
) -> None:
    """
    Turn a failed git command into :class:`SyncError` of the right kind.

    Args:
        repo: Repository the command was for.
        result: The completed process.
        action: What the command was doing, used in error messages.

    Raises:
        SyncError: If git exited with a non-zero status. Its ``git_output``
            is what git printed.
    """
    if result.returncode == 0:
        return

    reason = result.stderr.strip() or result.stdout.strip()
    logger.error("Failed to %s for repository '%s': %s", action, repo.name, reason)
    message = f"Failed to {action} for repository '{repo.name}': {reason}"
    kind = failure_kind(reason)
    if kind is SyncErrorKind.AUTH_FAILED and repo.token:
        message += f" ({AUTH_FAILURE_HINT})"
    raise SyncError(message, kind, git_output=reason)


def run_local(
    repo: RepoConfig,
    args: list[str],
    action: str,
    options: list[str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """
    Run one git command that only works on the local disk.

    Args:
        repo: Repository to run the command for.
        args: The git subcommand and its arguments, for example
            ``["status", "--porcelain"]``.
        action: What the command is doing, used in error messages.
        options: Git options placed before the subcommand, such as the
            commit identity.
        check: Raise when git fails. Pass False for a command whose
            failure is an expected outcome rather than an error, such as
            the merge that is used to detect conflicts.

    Returns:
        The completed process. When ``check`` is False, this may describe
        a failed command.

    Raises:
        SyncError: If git exits with a non-zero status and ``check`` is
            True, or does not finish within
            :data:`admin.git.runner.LOCAL_TIMEOUT_SECONDS` whatever
            ``check`` is.
    """
    command = [
        "git",
        "-C",
        str(repo.work_tree),
        *repository_options(repo),
        *(options or []),
        *args,
    ]
    result = _run(repo, command, action, LOCAL_TIMEOUT_SECONDS)
    if check:
        _raise_if_failed(repo, result, action)
    return result


def run_remote(
    repo: RepoConfig,
    args: list[str],
    action: str,
) -> subprocess.CompletedProcess[str]:
    """
    Run one git command that talks to the remote.

    Always adds :func:`admin.git.auth.remote_options` - https only, no
    credential helpers, stalled transfers abandoned - and the credentials
    from :func:`admin.git.auth.auth_environment`, and allows
    :data:`admin.git.runner.REMOTE_TIMEOUT_SECONDS`.

    Args:
        repo: Repository to run the command for.
        args: The git subcommand and its arguments, for example
            ``["push", "--", "origin", "refs/heads/main:refs/heads/main"]``.
        action: What the command is doing, used in error messages.

    Returns:
        The completed, successful process.

    Raises:
        SyncError: If git fails or does not finish in time.
    """
    command = [
        "git",
        "-C",
        str(repo.work_tree),
        *repository_options(repo),
        *remote_options(),
        *args,
    ]
    result = _run(
        repo, command, action, REMOTE_TIMEOUT_SECONDS, auth_environment(repo)
    )
    _raise_if_failed(repo, result, action)
    return result


def ref_exists(repo: RepoConfig, ref: str) -> bool:
    """
    Check whether a ref points at a commit.

    Args:
        repo: Repository to look in.
        ref: Full ref name, for example ``refs/remotes/origin/main``.

    Returns:
        True if the ref exists. False for an unborn branch too.

    Raises:
        SyncError: If git does not finish in time.
    """
    result = run_local(
        repo,
        ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        "look up a ref",
        check=False,
    )
    return result.returncode == 0


def count_commits(repo: RepoConfig, revision_range: str, action: str) -> int:
    """
    Count the commits in a revision range.

    Args:
        repo: Repository to count in.
        revision_range: Range to count, for example ``origin/main..HEAD``.
        action: What the count is for, used in error messages.

    Returns:
        The number of commits in the range.

    Raises:
        SyncError: If the count cannot be read.
    """
    result = run_local(repo, ["rev-list", "--count", revision_range], action)
    return int(result.stdout.strip() or "0")


def list_paths(repo: RepoConfig, args: list[str], action: str) -> list[str]:
    """
    List the paths an ``ls-files -z`` command prints.

    Args:
        repo: Repository to list in.
        args: The ``ls-files`` options selecting the files.
        action: What the list is for, used in error messages.

    Returns:
        The paths, relative to the working tree, unquoted.

    Raises:
        SyncError: If the listing cannot be read.
    """
    result = run_local(repo, ["ls-files", "-z", *args], action)
    return [path for path in result.stdout.split("\0") if path]
