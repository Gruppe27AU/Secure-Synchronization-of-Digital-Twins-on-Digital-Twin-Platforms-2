"""
Cloning of shared git assets into the workspace.

Consumes :class:`admin.git.config.RepoConfig` and clones the repository it
describes with a separated git directory and working tree (comparable to
``git clone --separate-git-dir``), so the two can live in different
locations, as required by the workspace's persistence layout.
"""

import logging
import subprocess
from pathlib import Path

from admin.git.auth import AUTH_FAILURE_HINT, auth_header_options, is_auth_failure
from admin.git.config import RepoConfig

logger = logging.getLogger(__name__)


class CloneError(Exception):
    """Raised when cloning a repository fails."""


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


def _build_clone_command(repo: RepoConfig) -> list[str]:
    """
    Build the ``git clone`` command for ``repo``, without running it.

    Args:
        repo: Repository configuration to build the command for.

    Returns:
        The ``git`` command as an argument list, ready for
        :func:`subprocess.run`.
    """
    # The auth header, when present, is a one-off ``-c`` override: applies
    # to this clone only and, unlike embedding credentials in the URL, is
    # never written into the resulting .git/config.
    command = ["git", *auth_header_options(repo)]
    command += [
        "clone",
        "--branch",
        repo.branch,
        "--single-branch",
        f"--separate-git-dir={repo.git_dir}",
        repo.repo_url,
        str(repo.work_tree),
    ]
    return command


def clone_asset(repo: RepoConfig) -> bool:
    """
    Clone ``repo`` if it has not been cloned yet.

    Args:
        repo: Repository configuration, including where the git directory
            and working tree should be placed.

    Returns:
        True if a clone was performed, False if it was skipped because
        ``repo.git_dir`` already holds a clone.

    Raises:
        CloneError: If ``git clone`` fails.
    """
    if is_cloned(repo.git_dir):
        logger.info(
            "Repository '%s' already cloned at %s; skipping", repo.name, repo.git_dir
        )
        return False

    repo.git_dir.parent.mkdir(parents=True, exist_ok=True)
    repo.work_tree.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        _build_clone_command(repo),
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.error(
            "Failed to clone repository '%s' (%s, branch %s) into %s: %s",
            repo.name,
            repo.repo_url,
            repo.branch,
            repo.work_tree,
            stderr,
        )
        message = f"git clone failed for repository '{repo.name}': {stderr}"
        if repo.username and repo.token and is_auth_failure(stderr):
            message += f" ({AUTH_FAILURE_HINT})"
        raise CloneError(message)

    logger.info(
        "Cloned repository '%s' (%s, branch %s) into %s",
        repo.name,
        repo.repo_url,
        repo.branch,
        repo.work_tree,
    )
    return True
