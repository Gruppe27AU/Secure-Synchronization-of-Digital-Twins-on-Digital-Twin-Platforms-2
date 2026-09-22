"""
Committing and pushing workspace changes back to the remote.

Consumes :class:`admin.git.config.RepoConfig` and keeps one already-cloned
repository in sync with its remote: stage everything, commit with a
timestamped message and push. Pulling remote changes is deliberately not
done here.

Two settings that :mod:`admin.git.clone` intentionally leaves out of the
repository's own config are supplied per command instead, so neither is
ever written to disk in the workspace:

- the commit identity, without which ``git commit`` refuses to run,
- the ``Authorization`` header, without which ``git push`` is anonymous.
"""

import base64
import logging
import subprocess
import time

from admin.git.config import RepoConfig

logger = logging.getLogger(__name__)

#: Identity used for automatic backup commits. Passed on the commit command
#: itself, so it never ends up in the repository's ``.git/config``.
COMMIT_AUTHOR_NAME = "workspace-admin"
COMMIT_AUTHOR_EMAIL = "workspace-admin@localhost"

#: Prefix of the generated commit messages, followed by a UTC timestamp.
COMMIT_MESSAGE_PREFIX = "workspace backup"


class SyncError(Exception):
    """Raised when committing or pushing a repository fails."""


def _basic_auth_value(credentials: str) -> str:
    """
    Base64-encode ``username:token`` for an HTTP Basic auth header.

    Args:
        credentials: A ``username:token`` pair.

    Returns:
        The value to place after ``Authorization: Basic``.
    """
    return base64.b64encode(credentials.encode("utf-8")).decode("ascii")


def _auth_options(repo: RepoConfig) -> list[str]:
    """
    Build the one-off git options that authenticate a push.

    Mirrors the header :mod:`admin.git.clone` builds for cloning. Issue #5
    is expected to move both into one shared helper; until then the two
    copies must stay in step.

    Args:
        repo: Repository configuration, possibly without credentials.

    Returns:
        The ``-c http.extraHeader=...`` pair, or an empty list when no
        credentials are configured.
    """
    if not repo.username or not repo.token:
        return []

    credentials = f"{repo.username}:{repo.token}"
    return [
        "-c",
        f"http.extraHeader=Authorization: Basic "
        f"{_basic_auth_value(credentials)}",
    ]


def _identity_options() -> list[str]:
    """
    Build the git options that give the backup commits an author.

    Returns:
        The ``-c user.name=...`` and ``-c user.email=...`` pairs.
    """
    return [
        "-c",
        f"user.name={COMMIT_AUTHOR_NAME}",
        "-c",
        f"user.email={COMMIT_AUTHOR_EMAIL}",
    ]


def _run_git(
    repo: RepoConfig,
    args: list[str],
    action: str,
    options: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run one git command inside the repository's working tree.

    Args:
        repo: Repository to run the command for.
        args: The git subcommand and its arguments, for example
            ``["push", "origin", "main"]``.
        action: What the command is doing, used in error messages.
        options: Git options placed before the subcommand, such as the
            credentials header.

    Returns:
        The completed process, when the command succeeded.

    Raises:
        SyncError: If git exits with a non-zero status.
    """
    command = ["git", "-C", str(repo.work_tree), *(options or []), *args]
    result = subprocess.run(command, capture_output=True, text=True, check=False)

    if result.returncode != 0:
        reason = result.stderr.strip() or result.stdout.strip()
        logger.error(
            "Failed to %s for repository '%s': %s", action, repo.name, reason
        )
        raise SyncError(f"Failed to {action} for repository '{repo.name}': {reason}")

    return result


def has_changes(repo: RepoConfig) -> bool:
    """
    Check whether the working tree holds anything worth committing.

    Args:
        repo: Repository to inspect.

    Returns:
        True if there are modified, deleted or untracked files.

    Raises:
        SyncError: If the status command fails.
    """
    result = _run_git(repo, ["status", "--porcelain"], "read the status")
    return bool(result.stdout.strip())


def build_commit_message() -> str:
    """
    Build the commit message for a backup commit.

    Returns:
        The message, ending in a UTC timestamp so every commit is
        distinguishable, for example ``workspace backup
        2026-09-22T12:30:00Z``.
    """
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return f"{COMMIT_MESSAGE_PREFIX} {timestamp}"


def sync_once(repo: RepoConfig) -> bool:
    """
    Commit and push the repository's changes, if it has any.

    Assumes the repository has already been cloned by
    :func:`admin.git.clone.clone_asset`.

    Args:
        repo: Repository to synchronize.

    Returns:
        True if a commit was made and pushed, False if the working tree
        was clean and nothing had to be done.

    Raises:
        SyncError: If staging, committing or pushing fails. A push is
            rejected when the remote has moved ahead; resolving that is
            the job of the pull step, not this one.
    """
    if not has_changes(repo):
        logger.info("No changes in repository '%s'; nothing to commit", repo.name)
        return False

    _run_git(repo, ["add", "-A"], "stage changes")
    _run_git(
        repo,
        ["commit", "-m", build_commit_message()],
        "commit changes",
        options=_identity_options(),
    )
    logger.info("Committed changes in repository '%s'", repo.name)

    _run_git(
        repo,
        ["push", "origin", repo.branch],
        "push changes",
        options=_auth_options(repo),
    )
    logger.info(
        "Pushed repository '%s' to branch %s", repo.name, repo.branch
    )

    return True
