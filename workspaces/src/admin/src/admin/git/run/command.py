"""
Running git commands for one repository.

Every command in a cloned repository names ``--git-dir`` and ``--work-tree``,
so a lost ``.git`` file cannot make git use an enclosing repository.

A failure raises :class:`admin.git.run.errors.SyncError` with a kind the user
can act on. The caller logs it.
"""

import subprocess

from admin.git.run.auth import AUTH_FAILURE_HINT, auth_environment, remote_options
from admin.git.setup.config import RepoConfig
from admin.git.run.errors import SyncError, SyncErrorKind, failure_kind
from admin.git.run.runner import (
    LOCAL_TIMEOUT_SECONDS,
    REMOTE_TIMEOUT_SECONDS,
    GitTimeoutError,
    run_git,
)


def _repository_options(repo: RepoConfig) -> list[str]:
    """Return the ``-C``, ``--git-dir`` and ``--work-tree`` options."""
    return [
        "-C",
        str(repo.work_tree),
        f"--git-dir={repo.git_dir}",
        f"--work-tree={repo.work_tree}",
    ]


def output_of(result: subprocess.CompletedProcess[str]) -> str:
    """Return what git printed: stderr, or stdout when stderr is empty."""
    return result.stderr.strip() or result.stdout.strip()


def _raise_if_failed(
    repo: RepoConfig, result: subprocess.CompletedProcess[str], action: str
) -> None:
    """Raise :class:`SyncError` of the matching kind if git failed."""
    if result.returncode == 0:
        return
    reason = output_of(result)
    message = f"Failed to {action} for repository '{repo.name}': {reason}"
    kind = failure_kind(reason)
    if kind is SyncErrorKind.AUTH_FAILED and repo.token:
        message += f" ({AUTH_FAILURE_HINT})"
    raise SyncError(message, kind, git_output=reason)


def run(
    repo: RepoConfig,
    args: list[str],
    action: str,
    remote: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """
    Run ``git <args>`` for ``repo``.

    Args:
        args: Everything after ``git``.
        action: What the command does, for the error message.
        remote: Talks to the remote: adds :func:`admin.git.run.auth.remote_options`
            and the credentials, and allows the longer time limit.
        check: Raise if git fails.

    Returns:
        The completed process; a failed one only when ``check`` is False.

    Raises:
        SyncError: If git fails and ``check`` is True, or times out.
    """
    if remote:
        command = ["git", *remote_options(), *args]
        timeout, env = REMOTE_TIMEOUT_SECONDS, auth_environment(repo)
    else:
        command, timeout, env = ["git", *args], LOCAL_TIMEOUT_SECONDS, None
    try:
        result = run_git(command, timeout, env=env)
    except GitTimeoutError as error:
        raise SyncError(
            f"Failed to {action} for repository '{repo.name}': {error}",
            SyncErrorKind.TIMEOUT,
        ) from error
    if check:
        _raise_if_failed(repo, result, action)
    return result


def run_local(
    repo: RepoConfig,
    args: list[str],
    action: str,
    options: list[str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """
    Run a git command in the cloned repository that stays on the local disk.

    Args:
        options: Git options before the subcommand, such as the identity.

    Otherwise as :func:`run`.
    """
    full = [*_repository_options(repo), *(options or []), *args]
    return run(repo, full, action, check=check)


def run_remote(
    repo: RepoConfig, args: list[str], action: str
) -> subprocess.CompletedProcess[str]:
    """Run a git command in the cloned repository that talks to the remote."""
    return run(repo, [*_repository_options(repo), *args], action, remote=True)


def ref_exists(repo: RepoConfig, ref: str) -> bool:
    """
    Check whether ``ref`` points at a commit; False for an unborn branch.

    Raises:
        SyncError: If git times out.
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
    Count the commits in ``revision_range``, for example ``origin/main..HEAD``.

    Raises:
        SyncError: If the count cannot be read.
    """
    result = run_local(repo, ["rev-list", "--count", revision_range], action)
    return int(result.stdout.strip() or "0")


def list_paths(repo: RepoConfig, args: list[str], action: str) -> list[str]:
    """
    List the paths ``git ls-files -z <args>`` prints, relative to the tree.

    Raises:
        SyncError: If the listing cannot be read.
    """
    result = run_local(repo, ["ls-files", "-z", *args], action)
    return [path for path in result.stdout.split("\0") if path]
