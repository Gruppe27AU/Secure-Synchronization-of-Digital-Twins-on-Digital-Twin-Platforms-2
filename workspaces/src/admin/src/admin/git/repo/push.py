"""
Pushing to the configured branch and to the workspace's backup branch.
"""

import logging
import secrets

from admin.git.run.command import count_commits, ref_exists, run_remote
from admin.git.setup.config import RepoConfig
from admin.git.run.errors import SyncError, SyncErrorKind
from admin.git.repo.refs import local_branch, push_refspec, remote_ref

logger = logging.getLogger(__name__)

#: Prefix of the backup branch, followed by the workspace's id.
BACKUP_BRANCH_PREFIX = "backup/"

#: File in the git directory holding the workspace's id.
WORKSPACE_ID_FILE = "workspace-admin-id"


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


def workspace_id(repo: RepoConfig) -> str:
    """
    Return the workspace's id: twelve hex characters, made once and kept in
    the git directory.

    Raises:
        OSError: If a new id cannot be stored.
    """
    id_file = repo.git_dir / WORKSPACE_ID_FILE
    try:
        stored = id_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        stored = ""
    if len(stored) == 12 and all(char in "0123456789abcdef" for char in stored):
        return stored

    new_id = secrets.token_hex(6)
    id_file.write_text(f"{new_id}\n", encoding="utf-8")
    return new_id


def backup_branch(repo: RepoConfig) -> str:
    """Return the backup branch, for example ``backup/3f2a9c01d4e5``."""
    return f"{BACKUP_BRANCH_PREFIX}{workspace_id(repo)}"


def push_to_backup_branch(repo: RepoConfig) -> str | None:
    """
    Force-push the branch to the workspace's backup branch.

    Skipped until the configured branch is on the remote, so on an empty
    remote that branch is pushed first and the forge makes it the default.
    Only this workspace writes the backup branch, so forcing it is safe.

    Returns:
        The backup branch, or None if skipped.

    Raises:
        SyncError: ``auth_failed``, ``network`` or ``timeout`` as git's
            failure is sorted, otherwise ``backup_failed``.
    """
    local = local_branch(repo)
    if not ref_exists(repo, local) or not ref_exists(
        repo, f"refs/remotes/{remote_ref(repo)}"
    ):
        return None
    try:
        branch = backup_branch(repo)
        run_remote(
            repo,
            ["push", "--", "origin", f"+{local}:refs/heads/{branch}"],
            "push to the backup branch",
        )
    except (SyncError, OSError) as error:
        kind = getattr(error, "kind", SyncErrorKind.GIT_FAILED)
        raise SyncError(
            f"Could not push repository '{repo.name}' to its backup branch: {error}",
            SyncErrorKind.BACKUP_FAILED if kind is SyncErrorKind.GIT_FAILED else kind,
        ) from error

    logger.info("Backed up repository '%s' to branch %s", repo.name, branch)
    return branch
