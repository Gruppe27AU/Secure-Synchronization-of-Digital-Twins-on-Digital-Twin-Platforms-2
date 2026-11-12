"""
Fetching the configured branch and merging it into the working tree.

``sync`` repositories merge (:func:`pull_changes`), ``pull`` repositories
only fast-forward (:func:`pull_only`). Neither resolves a conflict.
"""

import logging

from admin.git.checks import ensure_on_branch, ensure_safe_to_sync, unmerged_stages
from admin.git.command import count_commits, output_of, ref_exists, run_local, run_remote
from admin.git.commit import identity_options
from admin.git.config import RepoConfig
from admin.git.errors import SyncError, SyncErrorKind
from admin.git.refs import fetch_refspec, local_branch, remote_ref

logger = logging.getLogger(__name__)

#: git's error when the remote does not have the branch.
MISSING_REMOTE_REF = "couldn't find remote ref"

#: git's errors when a merge would overwrite a local file.
LOCAL_CHANGES_MARKERS = ("would be overwritten", "local changes")


def _in_the_way(output: str) -> bool:
    """Check whether git refused because a local file is in the way."""
    return any(marker in output.lower() for marker in LOCAL_CHANGES_MARKERS)


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
            repo, f"refs/remotes/{remote_ref(repo)}"
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
            f"{', '.join(conflicted)} as well; merge them by hand",
            SyncErrorKind.MERGE_CONFLICT,
        )
    return SyncError(
        f"Failed to merge the remote's changes for repository '{repo.name}': {output}",
        SyncErrorKind.LOCAL_CHANGES if _in_the_way(output) else SyncErrorKind.GIT_FAILED,
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
        kind = SyncErrorKind.LOCAL_CHANGES if _in_the_way(reason) else SyncErrorKind.PULL_FAILED
        raise SyncError(f"Not updating pull-only repository '{repo.name}': {reason}", kind)

    logger.info(
        "Updated pull-only repository '%s' with %d incoming commit(s)", repo.name, incoming
    )
    return True
