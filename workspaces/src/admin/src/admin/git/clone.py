"""
Cloning of shared git assets into the workspace.

Consumes :class:`admin.git.config.RepoConfig` and clones the repository it
describes with a separated git directory and working tree (comparable to
``git clone --separate-git-dir``), so the two can live in different
locations, as required by the workspace's persistence layout.

A remote with no commits at all, as a new project is unless the forge was
asked to add a README, cannot be cloned. It is started locally instead,
with the same layout and remote, and the first push creates its branch.
"""

import logging
from pathlib import Path

from admin.git.auth import AUTH_FAILURE_HINT, auth_environment, remote_options
from admin.git.config import RepoConfig
from admin.git.errors import SyncErrorKind, failure_kind
from admin.git.runner import (
    LOCAL_TIMEOUT_SECONDS,
    REMOTE_TIMEOUT_SECONDS,
    GitTimeoutError,
    run_git,
)

logger = logging.getLogger(__name__)

#: The file in the working tree that points git at the separate git
#: directory, and what it holds before the path.
GIT_FILE = ".git"
GIT_FILE_PREFIX = "gitdir: "

#: Text of git's error when the remote has no such branch to clone.
MISSING_BRANCH = "not found in upstream"


class CloneError(Exception):
    """
    Raised when cloning a repository fails.

    Attributes:
        kind: Why it failed, safe to expose where the message is not.
        git_output: What git printed, when a git command failed; empty
            otherwise. For telling failures apart, never for display.
    """

    def __init__(
        self,
        message: str,
        kind: SyncErrorKind = SyncErrorKind.GIT_FAILED,
        git_output: str = "",
    ):
        super().__init__(message)
        self.kind = kind
        self.git_output = git_output


def is_cloned(git_dir: Path) -> bool:
    """
    Check whether a repository has already been cloned into ``git_dir``.

    Args:
        git_dir: Candidate git directory (the ``--separate-git-dir``
            target of a previous clone).

    Returns:
        True if ``git_dir`` already holds a git directory.
    """
    return (git_dir / "HEAD").is_file()


def restore_git_file(repo: RepoConfig) -> None:
    """
    Write back a lost ``.git`` file in a cloned working tree.

    The backup does not need it, since :mod:`admin.git.command` names the
    repository explicitly, but the user's own git commands in the working
    tree do: without it they would act on whatever repository encloses the
    working tree. Anything already at that path is left alone, and a
    failure is logged rather than raised for the same reason.

    Args:
        repo: Repository whose working tree to repair.
    """
    git_file = repo.work_tree / GIT_FILE
    if git_file.exists() or not repo.work_tree.is_dir() or not is_cloned(repo.git_dir):
        return

    try:
        git_file.write_text(f"{GIT_FILE_PREFIX}{repo.git_dir}\n", encoding="utf-8")
    except OSError as error:
        logger.warning(
            "Repository '%s' has lost %s, and it could not be written back: %s",
            repo.name,
            git_file,
            error,
        )
        return

    logger.warning("Repository '%s' had lost %s; written back", repo.name, git_file)


def _build_clone_command(repo: RepoConfig) -> list[str]:
    """
    Build the ``git clone`` command for ``repo``, without running it.

    Args:
        repo: Repository configuration to build the command for.

    Returns:
        The ``git`` command as an argument list, ready for
        :func:`admin.git.runner.run_git`.
    """
    # The protocol options restrict the clone to https. The credentials go
    # in the environment (auth_environment), never into .git/config.
    command = ["git", *remote_options()]
    command += [
        "clone",
        "--branch",
        repo.branch,
        "--single-branch",
        f"--separate-git-dir={repo.git_dir}",
        # Ends option parsing, so the URL and path cannot be read as options.
        "--",
        repo.repo_url,
        str(repo.work_tree),
    ]
    return command


def _run(
    repo: RepoConfig, command: list[str], action: str, remote: bool = False
) -> str:
    """
    Run a git command for the start of a repository.

    Args:
        repo: Repository the command is for.
        command: The full command, starting with ``git``.
        action: What the command is doing, used in error messages.
        remote: Whether the command talks to the remote. Only then is it
            given the credentials, and the longer time limit.

    Returns:
        The command's standard output.

    Raises:
        CloneError: If git fails or does not finish in time, with a kind
            that says why.
    """
    try:
        if remote:
            result = run_git(command, REMOTE_TIMEOUT_SECONDS, env=auth_environment(repo))
        else:
            result = run_git(command, LOCAL_TIMEOUT_SECONDS)
    except GitTimeoutError as error:
        raise CloneError(
            f"Failed to {action} for repository '{repo.name}': {error}",
            SyncErrorKind.TIMEOUT,
        ) from error

    if result.returncode != 0:
        stderr = result.stderr.strip()
        kind = failure_kind(stderr)
        message = f"Failed to {action} for repository '{repo.name}': {stderr}"
        if repo.token and kind is SyncErrorKind.AUTH_FAILED:
            message += f" ({AUTH_FAILURE_HINT})"
        raise CloneError(message, kind, git_output=stderr)

    return result.stdout


def _remote_is_empty(repo: RepoConfig) -> bool:
    """
    Check whether the remote has no branches at all.

    Args:
        repo: Repository whose remote to ask.

    Returns:
        True if the remote answered and listed no branch.

    Raises:
        CloneError: If the remote cannot be asked.
    """
    heads = _run(
        repo,
        ["git", *remote_options(), "ls-remote", "--heads", "--", repo.repo_url],
        "list the remote branches",
        remote=True,
    )
    return not heads.strip()


def _start_on_empty_remote(repo: RepoConfig) -> None:
    """
    Start a repository locally for a remote that has no commits yet.

    The result looks like a clone: the same separate git directory, the
    configured branch checked out with no commits on it, and ``origin``
    fetching only that branch. The first push creates the branch on the
    remote.

    Args:
        repo: Repository to start.

    Raises:
        CloneError: If a git command fails.
    """
    _run(
        repo,
        [
            "git",
            "init",
            "--quiet",
            f"--initial-branch={repo.branch}",
            f"--separate-git-dir={repo.git_dir}",
            "--",
            str(repo.work_tree),
        ],
        "start the repository",
    )
    _run(
        repo,
        [
            "git",
            f"--git-dir={repo.git_dir}",
            f"--work-tree={repo.work_tree}",
            "remote",
            "add",
            "-t",
            repo.branch,
            "--",
            "origin",
            repo.repo_url,
        ],
        "add the remote",
    )


def clone_asset(repo: RepoConfig) -> bool:
    """
    Clone ``repo`` if it has not been cloned yet.

    Args:
        repo: Repository configuration, including where the git directory
            and working tree should be placed.

    Returns:
        True if a clone was performed, or the repository started on an
        empty remote. False if it was skipped because ``repo.git_dir``
        already holds a clone.

    Raises:
        CloneError: If ``git clone`` fails or does not finish within
            :data:`admin.git.runner.REMOTE_TIMEOUT_SECONDS`. Its ``kind``
            says why.
    """
    if is_cloned(repo.git_dir):
        logger.info(
            "Repository '%s' already cloned at %s; skipping", repo.name, repo.git_dir
        )
        return False

    repo.git_dir.parent.mkdir(parents=True, exist_ok=True)
    repo.work_tree.mkdir(parents=True, exist_ok=True)

    try:
        _run(repo, _build_clone_command(repo), "clone", remote=True)
    except CloneError as error:
        if MISSING_BRANCH not in error.git_output.lower() or not _remote_is_empty(repo):
            logger.error(
                "Failed to clone repository '%s' (%s, branch %s) into %s: %s",
                repo.name,
                repo.repo_url,
                repo.branch,
                repo.work_tree,
                error,
            )
            raise
        _start_on_empty_remote(repo)
        logger.info(
            "The remote of repository '%s' (%s) is empty; started it in %s, and "
            "the first push creates branch %s",
            repo.name,
            repo.repo_url,
            repo.work_tree,
            repo.branch,
        )
        return True

    logger.info(
        "Cloned repository '%s' (%s, branch %s) into %s",
        repo.name,
        repo.repo_url,
        repo.branch,
        repo.work_tree,
    )
    return True
