"""
What is checked in the files about to be committed.

An emptied working tree is not committed (:func:`ensure_no_mass_deletion`).
A file too large (:func:`large_files`) or holding conflict markers
(:func:`staged_conflict_markers`) is held back on its own. Rebuilt folders
such as virtual environments are never staged (:func:`ensure_default_excludes`).
"""

import logging

from admin.git.run.command import list_paths, run_local
from admin.git.setup.config import RepoConfig
from admin.git.run.errors import SyncError, SyncErrorKind

logger = logging.getLogger(__name__)

#: Share of the tracked files that may go missing before a commit is refused.
MASS_DELETION_FRACTION = 0.5

#: Fewer deleted files than this are never refused.
MASS_DELETION_MIN_FILES = 10

#: End of each ``git diff --check`` line about a conflict marker.
CONFLICT_MARKER_WARNING = "leftover conflict marker"

#: Lines that make a file conflicted. A bare ``=======`` is left out: it is
#: also a Markdown heading underline.
CONFLICT_MARKER_PREFIXES = ("<<<<<<<", ">>>>>>>")

#: Largest file committed, in bytes. Forges refuse files around 100 MB.
MAX_FILE_BYTES = 50 * 1024 * 1024

#: Patterns never staged: folders that are rebuilt, not kept.
DEFAULT_EXCLUDES = (
    ".venv/",
    "venv/",
    "__pycache__/",
    "*.py[cod]",
    ".ipynb_checkpoints/",
    ".pytest_cache/",
    ".mypy_cache/",
    "node_modules/",
    ".DS_Store",
    "Thumbs.db",
    "*.swp",
)

#: Lines framing the backup's block in ``info/exclude``; the rest is the user's.
EXCLUDE_BLOCK_START = "# >>> workspace-admin defaults (rewritten by the backup)"
EXCLUDE_BLOCK_END = "# <<< workspace-admin defaults"


def ensure_no_mass_deletion(repo: RepoConfig) -> None:
    """
    Refuse to commit when most tracked files are missing.

    An emptied tree is more often an accident than a clean-up. Deleting that
    much on purpose takes one commit by hand.

    Raises:
        SyncError: ``mass_deletion``, or if the files cannot be counted.
    """
    deleted = len(list_paths(repo, ["--deleted"], "count the deleted files"))
    if deleted < MASS_DELETION_MIN_FILES:
        return
    tracked = len(list_paths(repo, ["--cached"], "count the tracked files"))
    if deleted <= tracked * MASS_DELETION_FRACTION:
        return
    raise SyncError(
        f"Not committing in repository '{repo.name}': {deleted} of {tracked} "
        f"tracked files are missing from {repo.work_tree}. If that is "
        f"intended, commit the deletion there by hand to resume the backup",
        SyncErrorKind.MASS_DELETION,
    )


def large_files(repo: RepoConfig) -> list[str]:
    """
    List new or changed files over :data:`MAX_FILE_BYTES`.

    Checked before staging, since staging a huge file can hit the time limit.

    Raises:
        SyncError: If the files cannot be listed.
    """
    candidates = list_paths(
        repo, ["--others", "--exclude-standard"], "list the new files"
    ) + list_paths(repo, ["--modified"], "list the changed files")
    too_large = []
    for path in dict.fromkeys(candidates):
        try:
            size = (repo.work_tree / path).lstat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            too_large.append(path)
    return too_large


def _has_conflict_marker(repo: RepoConfig, path: str) -> bool:
    """Check whether the staged ``path`` has a ``<<<<<<<`` or ``>>>>>>>`` line."""
    result = run_local(repo, ["show", f":{path}"], "read a staged file")
    return any(
        line.startswith(CONFLICT_MARKER_PREFIXES) for line in result.stdout.splitlines()
    )


def staged_conflict_markers(repo: RepoConfig) -> list[str]:
    """
    List the staged files holding conflict markers.

    ``git diff --check`` finds candidates, respecting ``conflict-marker-size``
    in ``.gitattributes``; each is then read for a real marker line.

    Raises:
        SyncError: If the check or a staged file cannot be read.
    """
    result = run_local(
        repo,
        ["diff", "--cached", "--check"],
        "check the staged changes for conflict markers",
        check=False,
    )
    if result.returncode not in (0, 2):
        raise SyncError(
            f"Failed to check the staged changes for conflict markers in "
            f"repository '{repo.name}': {result.stderr.strip()}"
        )
    candidates = dict.fromkeys(
        line.rsplit(":", 2)[0]
        for line in result.stdout.splitlines()
        if line.endswith(CONFLICT_MARKER_WARNING)
    )
    return [path for path in candidates if _has_conflict_marker(repo, path)]


def _exclude_block() -> str:
    """Return the backup's block of ``info/exclude``."""
    return "\n".join([EXCLUDE_BLOCK_START, *DEFAULT_EXCLUDES, EXCLUDE_BLOCK_END]) + "\n"


def _without_exclude_block(text: str) -> str:
    """Return ``info/exclude`` without the backup's block."""
    kept = []
    inside = False
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if stripped == EXCLUDE_BLOCK_START:
            inside = True
        elif stripped == EXCLUDE_BLOCK_END:
            inside = False
        elif not inside:
            kept.append(line)
    result = "".join(kept)
    return result if not result or result.endswith("\n") else result + "\n"


def ensure_default_excludes(repo: RepoConfig) -> None:
    """
    Keep :data:`DEFAULT_EXCLUDES` in the git directory's ``info/exclude``.

    Unlike ``.gitignore`` it is neither committed nor visible. A file that
    cannot be read or written is logged, not raised.
    """
    exclude = repo.git_dir / "info" / "exclude"
    try:
        current = exclude.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""
    except OSError as error:
        logger.warning("Cannot read %s: %s", exclude, error)
        return
    wanted = _without_exclude_block(current) + _exclude_block()
    if wanted == current:
        return
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(wanted, encoding="utf-8")
    except OSError as error:
        logger.warning("Cannot write the default excludes to %s: %s", exclude, error)
