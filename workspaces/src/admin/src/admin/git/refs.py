"""
Ref names and refspecs for the configured branch.

Commands that name the branch put ``--`` before these, so a branch value is
never read as a git option.
"""

from admin.git.config import RepoConfig


def remote_ref(repo: RepoConfig) -> str:
    """Return the remote-tracking ref, for example ``origin/main``."""
    return f"origin/{repo.branch}"


def local_branch(repo: RepoConfig) -> str:
    """Return the configured branch's full name, ``refs/heads/<branch>``."""
    return f"refs/heads/{repo.branch}"


def fetch_refspec(repo: RepoConfig) -> str:
    """
    Return the refspec that fetches the branch into ``origin/<branch>``.

    The ``+`` lets a fetch follow a force-push, as ``git clone`` does.
    """
    return f"+refs/heads/{repo.branch}:refs/remotes/origin/{repo.branch}"


def push_refspec(repo: RepoConfig) -> str:
    """Return the refspec that pushes the branch to the same remote branch."""
    return f"{local_branch(repo)}:{local_branch(repo)}"
