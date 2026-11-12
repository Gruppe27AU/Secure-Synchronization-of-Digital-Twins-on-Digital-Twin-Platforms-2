"""
Cloning a repository with its git directory apart from its working tree.

A remote without commits cannot be cloned; it is started locally with the
same layout and remote instead, and the first push creates the branch.
"""

import logging
from pathlib import Path

from admin.git.command import run
from admin.git.config import RepoConfig
from admin.git.errors import SyncError

logger = logging.getLogger(__name__)

#: The file in the working tree pointing at the git directory, and its prefix.
GIT_FILE = ".git"
GIT_FILE_PREFIX = "gitdir: "

#: git's error when the remote has no such branch to clone.
MISSING_BRANCH = "not found in upstream"


class CloneError(SyncError):
    """Raised when cloning a repository fails."""


def is_cloned(git_dir: Path) -> bool:
    """Check whether ``git_dir`` holds a git directory."""
    return (git_dir / "HEAD").is_file()


def restore_git_file(repo: RepoConfig) -> None:
    """
    Write back a lost ``.git`` file, for the user's own git commands.

    Anything at that path is left alone. A failure is logged, not raised.
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


def _run(repo: RepoConfig, args: list[str], action: str, remote: bool = False) -> str:
    """
    Run ``git <args>`` and return its output.

    Raises:
        CloneError: If git fails or times out.
    """
    try:
        return run(repo, args, action, remote=remote).stdout
    except SyncError as error:
        raise CloneError(str(error), error.kind, error.git_output) from error


def _clone_args(repo: RepoConfig) -> list[str]:
    """Return the ``clone`` arguments; ``--`` keeps the URL from being an option."""
    return [
        "clone",
        "--branch",
        repo.branch,
        "--single-branch",
        f"--separate-git-dir={repo.git_dir}",
        "--",
        repo.repo_url,
        str(repo.work_tree),
    ]


def _remote_is_empty(repo: RepoConfig) -> bool:
    """Check whether the remote has no branches."""
    heads = _run(
        repo,
        ["ls-remote", "--heads", "--", repo.repo_url],
        "list the remote branches",
        remote=True,
    )
    return not heads.strip()


def _start_on_empty_remote(repo: RepoConfig) -> None:
    """Start the repository locally as a clone of an empty remote would be."""
    _run(
        repo,
        ["init", "--quiet", f"--initial-branch={repo.branch}",
         f"--separate-git-dir={repo.git_dir}", "--", str(repo.work_tree)],
        "start the repository",
    )
    _run(
        repo,
        [f"--git-dir={repo.git_dir}", f"--work-tree={repo.work_tree}",
         "remote", "add", "-t", repo.branch, "--", "origin", repo.repo_url],
        "add the remote",
    )
    logger.info(
        "The remote of repository '%s' is empty; started it in %s, and the "
        "first push creates branch %s",
        repo.name,
        repo.work_tree,
        repo.branch,
    )


def _clone_or_start(repo: RepoConfig) -> None:
    """
    Clone the repository, or start it if the remote is empty.

    Raises:
        CloneError: If neither works.
    """
    try:
        _run(repo, _clone_args(repo), "clone", remote=True)
    except CloneError as error:
        if MISSING_BRANCH not in error.git_output.lower() or not _remote_is_empty(repo):
            raise
        _start_on_empty_remote(repo)
        return
    logger.info(
        "Cloned repository '%s' (branch %s) into %s",
        repo.name,
        repo.branch,
        repo.work_tree,
    )


def clone_asset(repo: RepoConfig) -> bool:
    """
    Clone ``repo`` unless it is cloned already.

    Returns:
        False if it was already cloned.

    Raises:
        CloneError: If the clone fails or times out.
    """
    if is_cloned(repo.git_dir):
        logger.info(
            "Repository '%s' already cloned at %s; skipping", repo.name, repo.git_dir
        )
        return False
    repo.git_dir.parent.mkdir(parents=True, exist_ok=True)
    repo.work_tree.mkdir(parents=True, exist_ok=True)
    _clone_or_start(repo)
    return True
