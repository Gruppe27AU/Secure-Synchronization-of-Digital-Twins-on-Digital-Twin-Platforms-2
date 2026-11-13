"""
Cloning a repository with its git directory apart from its working tree.

A remote without commits cannot be cloned; it is started locally with the
same layout and remote instead, and the first push creates the branch.
"""

import logging
from pathlib import Path

from admin.git.run.command import list_paths, run, run_local
from admin.git.run.errors import SyncError
from admin.git.setup.config import RepoConfig

logger = logging.getLogger(__name__)

#: The file in the working tree pointing at the git directory, and its prefix.
GIT_FILE = ".git"
GIT_FILE_PREFIX = "gitdir: "

#: git's error when the remote has no such branch to clone.
MISSING_BRANCH = "not found in upstream"


def is_cloned(git_dir: Path) -> bool:
    """Check whether ``git_dir`` holds a git directory."""
    return (git_dir / "HEAD").is_file()


def _is_missing_or_empty(work_tree: Path) -> bool:
    """Check whether ``work_tree`` is gone or holds nothing but a ``.git`` file."""
    if not work_tree.exists():
        return True
    return all(entry.name == GIT_FILE for entry in work_tree.iterdir())


def _restore_files(repo: RepoConfig) -> None:
    """
    Recreate a missing or empty work tree from the index, as of the last cycle.

    An empty index, as on a remote with no files yet, is left alone. Files
    deleted from a tree that still holds others are left to
    :func:`admin.git.repo.checks.ensure_no_mass_deletion`. A failure is
    logged, not raised.
    """
    if not is_cloned(repo.git_dir) or not _is_missing_or_empty(repo.work_tree):
        return
    try:
        repo.work_tree.mkdir(parents=True, exist_ok=True)
        if not list_paths(repo, [], "list the tracked files"):
            return
        run_local(repo, ["checkout-index", "--all"], "restore the work tree")
    except (OSError, SyncError) as error:
        logger.warning(
            "Repository '%s' has lost the files in %s, and they could not be "
            "restored: %s",
            repo.name,
            repo.work_tree,
            error,
        )
        return
    logger.warning(
        "Repository '%s' had lost the files in %s; restored from its git directory",
        repo.name,
        repo.work_tree,
    )


def restore_work_tree(repo: RepoConfig) -> None:
    """
    Restore a missing or empty work tree: its files, then its ``.git`` file.

    Called on startup only, so a folder deleted while the service runs stays
    deleted. Failures are logged.
    """
    _restore_files(repo)
    restore_git_file(repo)


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
    heads = run(
        repo,
        ["ls-remote", "--heads", "--", repo.repo_url],
        "list the remote branches",
        remote=True,
    ).stdout
    return not heads.strip()


def _start_on_empty_remote(repo: RepoConfig) -> None:
    """Start the repository locally as a clone of an empty remote would be."""
    run(
        repo,
        ["init", "--quiet", f"--initial-branch={repo.branch}",
         f"--separate-git-dir={repo.git_dir}", "--", str(repo.work_tree)],
        "start the repository",
    )
    run(
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
        SyncError: If neither works.
    """
    try:
        run(repo, _clone_args(repo), "clone", remote=True)
    except SyncError as error:
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
    Clone ``repo``, or restore its work tree if it is cloned already.

    Returns:
        False if it was already cloned.

    Raises:
        SyncError: If the clone fails or times out.
    """
    if is_cloned(repo.git_dir):
        logger.info(
            "Repository '%s' already cloned at %s; skipping", repo.name, repo.git_dir
        )
        restore_work_tree(repo)
        return False
    repo.git_dir.parent.mkdir(parents=True, exist_ok=True)
    repo.work_tree.mkdir(parents=True, exist_ok=True)
    _clone_or_start(repo)
    return True
