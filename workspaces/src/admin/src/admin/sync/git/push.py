"""
Pushing to the configured branch, and replacing the remote's branch with the
workspace's after a merge conflict.
"""

import logging
import os
import re
from datetime import datetime, timezone

from admin.sync.git.command import count_commits, ref_exists, resolve_commit, run_remote
from admin.config.config import RepoConfig
from admin.sync.git.errors import SyncError, SyncErrorKind
from admin.sync.git.refs import local_branch, push_refspec, remote_ref

logger = logging.getLogger(__name__)

#: Prefix of the branches keeping the remote's version after a conflict,
#: followed by the workspace's user and the time.
REMOTE_BACKUP_BRANCH_PREFIX = "remote-backup/"

#: Names the workspace's user in the branch; set in every workspace image.
USER_VARIABLE = "MAIN_USER"

#: Used when ``MAIN_USER`` is unset or not safe in a branch name.
FALLBACK_USER = "workspace"

#: A user name safe as one part of a branch name.
_SAFE_USER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")

#: git's reason when ``--force-with-lease`` finds the remote has moved.
STALE_LEASE_MARKER = "stale info"


def push_if_ahead(repo: RepoConfig) -> bool:
    """
    Push the branch if it has commits the remote lacks.

    The branch is counted by name, not ``HEAD``, so the count matches what
    is pushed. A branch the remote lacks counts in full.

    Returns:
        True if a push was made.

    Raises:
        SyncError: If the push fails.
    """
    local = local_branch(repo)
    if not ref_exists(repo, local):
        return False
    revision_range = local
    if ref_exists(repo, f"refs/remotes/{remote_ref(repo)}"):
        revision_range = f"{remote_ref(repo)}..{local}"

    outgoing = count_commits(repo, revision_range, "count outgoing commits")
    if not outgoing:
        return False
    run_remote(repo, ["push", "--", "origin", push_refspec(repo)], "push changes")
    logger.info(
        "Pushed %d commit(s) from repository '%s' to branch %s",
        outgoing,
        repo.name,
        repo.branch,
    )
    return True


def _utc_now() -> datetime:
    """Return the current time in UTC. Replaceable in tests."""
    return datetime.now(timezone.utc)


def workspace_user() -> str:
    """Return ``$MAIN_USER``, or :data:`FALLBACK_USER` if unset or unsafe."""
    user = os.environ.get(USER_VARIABLE, "")
    if _SAFE_USER.fullmatch(user):
        return user
    if user:
        logger.warning(
            "%s=%r cannot be part of a branch name; using '%s'",
            USER_VARIABLE,
            user,
            FALLBACK_USER,
        )
    return FALLBACK_USER


def remote_backup_branch() -> str:
    """
    Return a new branch for the remote's version, for example
    ``remote-backup/user1/20261119T101530Z``.

    Every conflict gets a branch of its own, so no earlier one is overwritten.
    """
    stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    return f"{REMOTE_BACKUP_BRANCH_PREFIX}{workspace_user()}/{stamp}"


def overwrite_remote(repo: RepoConfig) -> str:
    """
    Replace the remote's branch with the workspace's, keeping the remote's
    version on a new branch.

    Called after a merge conflict was aborted, so the branch holds exactly
    the workspace's work. One atomic push makes both changes, or neither:
    the remote's last fetched commit goes to :func:`remote_backup_branch`,
    and the branch is force-pushed over the remote's. ``--force-with-lease``
    refuses the push if the remote moved since the fetch, so a commit nobody
    has backed up is never overwritten.

    Returns:
        The branch holding the remote's version.

    Raises:
        SyncError: ``remote_changed`` if the remote moved since the fetch;
            ``auth_failed``, ``network`` or ``timeout`` as git's failure is
            sorted; otherwise ``overwrite_refused``, as when the forge
            protects the branch from force-pushes.
    """
    local = local_branch(repo)
    try:
        remote_commit = resolve_commit(repo, f"refs/remotes/{remote_ref(repo)}")
        branch = remote_backup_branch()
        run_remote(
            repo,
            [
                "push",
                "--atomic",
                f"--force-with-lease={local}:{remote_commit}",
                "--",
                "origin",
                f"{remote_commit}:refs/heads/{branch}",
                f"{local}:{local}",
            ],
            "replace the remote's branch",
        )
    except (SyncError, OSError) as error:
        raise _overwrite_failure(repo, error) from error

    logger.warning(
        "Repository '%s': the workspace's version replaced branch %s on the "
        "remote; the remote's version is on branch %s",
        repo.name,
        repo.branch,
        branch,
    )
    return branch


def _overwrite_failure(repo: RepoConfig, error: Exception) -> SyncError:
    """Sort a failed :func:`overwrite_remote` by what the user can do about it."""
    kind = getattr(error, "kind", SyncErrorKind.GIT_FAILED)
    output = getattr(error, "git_output", "")
    if kind is SyncErrorKind.GIT_FAILED:
        if STALE_LEASE_MARKER in output.lower():
            return SyncError(
                f"Did not replace branch {repo.branch} of repository "
                f"'{repo.name}': the remote changed meanwhile; the next cycle "
                "tries again",
                SyncErrorKind.REMOTE_CHANGED,
                git_output=output,
            )
        return SyncError(
            f"Could not replace branch {repo.branch} of repository "
            f"'{repo.name}' on the remote: {error}; allow force-pushes to "
            f"{repo.branch}, or merge origin/{repo.branch} by hand",
            SyncErrorKind.OVERWRITE_REFUSED,
            git_output=output,
        )
    return SyncError(
        f"Could not replace branch {repo.branch} of repository '{repo.name}' "
        f"on the remote: {error}",
        kind,
        git_output=output,
    )
