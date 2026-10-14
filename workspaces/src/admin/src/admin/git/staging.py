"""
Deciding what the git backup stages for a commit.

Everything pushed stays in the repository's history for good, so the
backup does not commit everything it finds in a working tree:

- files matching :data:`DEFAULT_EXCLUDES` - caches, virtual environments
  and common credential files - are written into the repository's
  ``info/exclude``, so git does not see them at all,
- files larger than :data:`MAX_FILE_SIZE_BYTES` are left out of each
  commit, picked out before anything is staged.

Neither changes the repository for anyone else: ``info/exclude`` lives in
the git directory and is never pushed, and a skipped file stays in the
working tree untouched. Used by :mod:`admin.git.sync`, which runs git and
hands its output here; nothing in this module runs git itself.
"""

import os
import stat
from pathlib import Path

#: Largest file the backup commits, in bytes (50 MiB). A larger file is left
#: out of every backup commit and named in the log instead: each version of
#: a file stays in the history, and a push over the remote's size limit
#: would fail every interval.
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024

#: Patterns the backup writes into ``<git dir>/info/exclude``. They add to
#: whatever ``.gitignore`` the repository has, and only affect files git
#: does not track yet.
DEFAULT_EXCLUDES = (
    "# Python and Jupyter",
    "__pycache__/",
    "*.py[cod]",
    ".ipynb_checkpoints/",
    "# Virtual environments and installed packages",
    ".venv/",
    "venv/",
    "node_modules/",
    "# Operating system clutter",
    ".DS_Store",
    "Thumbs.db",
    "# Credentials and keys",
    ".env",
    ".env.*",
    "!.env.example",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ecdsa",
    "id_ed25519",
    ".git-credentials",
    ".netrc",
)

#: Lines around the backup's own part of ``info/exclude``, so it can be
#: replaced without touching anything the user wrote there.
EXCLUDES_BEGIN = "# BEGIN workspace-admin default excludes (rewritten every sync)"
EXCLUDES_END = "# END workspace-admin default excludes"


def write_default_excludes(git_dir: Path) -> bool:
    """
    Make sure ``info/exclude`` holds the current :data:`DEFAULT_EXCLUDES`.

    The backup's block is replaced every time, so a workspace cloned before
    a change to the list picks it up too. Every other line in the file is
    the user's and is kept as it is.

    Args:
        git_dir: The repository's git directory.

    Returns:
        True if the file was written, False if it was already up to date.

    Raises:
        OSError: If the file cannot be read or written.
        UnicodeDecodeError: If the file is not UTF-8.
    """
    exclude_file = git_dir / "info" / "exclude"
    block = "\n".join((EXCLUDES_BEGIN, *DEFAULT_EXCLUDES, EXCLUDES_END)) + "\n"

    try:
        current = exclude_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""

    kept = []
    inside = False
    for line in current.splitlines():
        if line == EXCLUDES_BEGIN:
            inside = True
        elif line == EXCLUDES_END:
            inside = False
        elif not inside:
            kept.append(line)

    user_part = "\n".join(kept).rstrip("\n")
    wanted = f"{user_part}\n\n{block}" if user_part else block
    if wanted == current:
        return False

    exclude_file.parent.mkdir(parents=True, exist_ok=True)
    exclude_file.write_text(wanted, encoding="utf-8")
    return True


def parse_status(output: str) -> list[tuple[str, str]]:
    """
    Read the output of ``git status --porcelain -z``.

    Args:
        output: What git printed: NUL-separated entries of a two-letter
            status code, a space and a path. A rename or copy is followed
            by one more entry, the path it had before, which is skipped.

    Returns:
        One ``(code, path)`` pair per file, for example ``(" M", "a.py")``
        or ``("??", "new dir/b.csv")``.
    """
    entries = iter(output.split("\0"))
    changed = []
    for entry in entries:
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        changed.append((code, path))
        if code[0] in "RC":
            next(entries, None)
    return changed


def oversized_files(
    work_tree: Path, paths: list[str], limit_bytes: int
) -> list[str]:
    """
    Pick out the files larger than ``limit_bytes``.

    The size is read from the working tree, before anything is staged, so
    git never stores a copy of a file that is going to be left out.

    Args:
        work_tree: The working tree the paths are relative to.
        paths: Changed paths, as git listed them.
        limit_bytes: Largest size committed, normally
            :data:`MAX_FILE_SIZE_BYTES`.

    Returns:
        The oversized paths, in the order given. A path that no longer
        exists, or is not a regular file, is never oversized.
    """
    oversized = []
    for path in paths:
        try:
            info = os.lstat(work_tree / path)
        except OSError:
            # Deleted, or gone since git listed it: nothing to store.
            continue
        if stat.S_ISREG(info.st_mode) and info.st_size > limit_bytes:
            oversized.append(path)
    return oversized


def literal_excludes(paths: list[str]) -> list[str]:
    """
    Turn paths into pathspecs that leave exactly those files out.

    ``literal`` makes git take ``*``, ``?`` and ``[`` in a file name as
    ordinary characters, so no other file is left out by accident.

    Args:
        paths: Paths relative to the working tree.

    Returns:
        One ``:(exclude,literal)<path>`` pathspec per path.
    """
    return [f":(exclude,literal){path}" for path in paths]
