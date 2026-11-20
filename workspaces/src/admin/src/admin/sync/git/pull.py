"""
Fetching the configured branch and merging it into the working tree.

``sync`` repositories merge (:func:`pull_changes`), ``pull`` repositories
only fast-forward (:func:`pull_only`). Neither resolves a conflict.
"""

import logging

from admin.config.config import RepoConfig
from admin.sync.git.busy import ensure_on_branch, ensure_safe_to_sync, unmerged_stages
from admin.sync.git.command import count_commits, output_of, ref_exists, run_local, run_remote
from admin.sync.git.commit import identity_options
from admin.sync.git.errors import SyncError, SyncErrorKind, merge_failure_kind
from admin.sync.git.refs import (
    fetch_refspec,
    local_branch,
    remote_ref,
    remote_tracking_ref,
)

logger = logging.getLogger(__name__)

#: git's error when the remote does not have the branch.
MISSING_REMOTE_REF = "couldn't find remote ref"

def _fetch(repo: RepoConfig) -> bool:
    """
    Fetch the branch into ``origin/<branch>``.

    A branch this workspace has never fetched and the remote lacks is new:
    the next push creates it. One that has disappeared is an error.

    Returns:
        False if the remote does not have the branch yet.

    Raises:
        SyncError: If the fetch fails otherwise.
    """
    try:
        run_remote(
            repo, ["fetch", "--", "origin", fetch_refspec(repo)], "fetch from the remote"
        )
    except SyncError as error:
        if MISSING_REMOTE_REF in error.git_output.lower() and not ref_exists(
            repo, remote_tracking_ref(repo)
        ):
            logger.info(
                "The remote of repository '%s' has no branch %s yet; the next "
                "push creates it",
                repo.name,
                repo.branch,
            )
            return False
        raise
    return True


def _incoming(repo: RepoConfig) -> int:
    """Count fetched commits the branch lacks; all of them on an unborn branch."""
    if not ref_exists(repo, local_branch(repo)):
        return count_commits(repo, remote_ref(repo), "count incoming commits")
    return count_commits(repo, f"HEAD..{remote_ref(repo)}", "count incoming commits")


def _fetch_for_merge(repo: RepoConfig) -> int:
    """
    Fetch, count the incoming commits and check the branch before a merge.

    The branch is checked after the fetch, which can take long enough for
    the user to switch branches.

    Returns:
        The number of commits to merge; 0 for none.

    Raises:
        SyncError: If a step fails or another branch is checked out.
    """
    if not _fetch(repo):
        return 0
    incoming = _incoming(repo)
    if not incoming:
        logger.info("Repository '%s' is already up to date", repo.name)
        return 0
    ensure_on_branch(repo)
    return incoming


def _merge_failure(repo: RepoConfig, output: str) -> SyncError:
    """
    Describe a merge that stopped.

    Returns:
        ``merge_conflict`` naming the conflicted files; without any,
        ``local_changes`` if a local file is in the way, else ``git_failed``.

    Raises:
        SyncError: If the conflicted files cannot be listed.
    """
    conflicted = list(unmerged_stages(repo))
    if conflicted:
        return SyncError(
            f"Repository '{repo.name}': the remote changed "
            f"{', '.join(conflicted)} as well",
            SyncErrorKind.MERGE_CONFLICT,
        )
    return SyncError(
        f"Failed to merge the remote's changes for repository '{repo.name}': {output}",
        merge_failure_kind(output, SyncErrorKind.GIT_FAILED),
    )


def pull_changes(repo: RepoConfig) -> bool:
    """
    Merge the remote's changes, or abort and change nothing.

    Returns:
        True if commits were merged.

    Raises:
        SyncError: If a step fails, a conflict included.
    """
    incoming = _fetch_for_merge(repo)
    if not incoming:
        return False

    attempt = run_local(
        repo,
        ["merge", remote_ref(repo)],
        "merge the remote's changes",
        options=identity_options(),
        check=False,
    )
    if attempt.returncode != 0:
        try:
            error = _merge_failure(repo, output_of(attempt))
        finally:
            run_local(repo, ["merge", "--abort"], "abort the failed merge", check=False)
        raise error

    logger.info("Merged %d incoming commit(s) into repository '%s'", incoming, repo.name)
    return True


def remote_changed_paths(repo: RepoConfig) -> set[str]:
    """
    Return the files the remote changed since the branches last met.

    ``-z`` prints each name as it is, never quoted or escaped.

    Raises:
        SyncError: If the files cannot be listed.
    """
    result = run_local(
        repo,
        ["diff", "--name-only", "-z", f"HEAD...{remote_ref(repo)}", "--"],
        "list the files the remote changed",
    )
    return {path for path in result.stdout.split("\0") if path}


def pull_only(repo: RepoConfig) -> bool:
    """
    Fast-forward a ``pull`` repository; never commit or push.

    Returns:
        True if the working tree moved forward.

    Raises:
        SyncError: ``local_changes`` if a local change is in the way,
            ``pull_failed`` if the workspace has commits of its own, or as
            any other step fails.
    """
    ensure_safe_to_sync(repo)
    incoming = _fetch_for_merge(repo)
    if not incoming:
        return False

    attempt = run_local(
        repo,
        ["merge", "--ff-only", remote_ref(repo)],
        "fast-forward to the remote",
        check=False,
    )
    if attempt.returncode != 0:
        reason = output_of(attempt)
        raise SyncError(
            f"Not updating pull-only repository '{repo.name}': {reason}",
            merge_failure_kind(reason, SyncErrorKind.PULL_FAILED),
        )

    logger.info(
        "Updated pull-only repository '%s' with %d incoming commit(s)", repo.name, incoming
    )
    return True
