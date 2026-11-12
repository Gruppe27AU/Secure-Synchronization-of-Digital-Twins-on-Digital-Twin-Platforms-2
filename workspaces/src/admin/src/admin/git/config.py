"""
Types and settings of the git backup configuration.

Reading the file is :mod:`admin.git.load`; checking its values is
:mod:`admin.git.validate`.

Every path follows from one root, normally ``$PERSISTENT_DIR``: git
directories in ``<root>/.local/share/workspace-admin/<name>``, work trees in
``<root>/assets/<name>``.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

#: Keys every ``[assets.*]`` section must hold.
REQUIRED_ASSET_KEYS = ("GIT_REPO_URL", "GIT_REPO_BRANCH", "GIT_REPO_TOKEN")

#: Template values that were never filled in. A repository holding one is
#: refused, so an unedited template never reaches a remote.
PLACEHOLDER_VALUES = {
    "GIT_REPO_URL": frozenset({
        "https://gitlab.com/username/repository.git",
        "https://gitlab.com/username/private-repo.git",
        "https://gitlab.com/username/common-repo.git",
    }),
    "GIT_REPO_TOKEN": frozenset({"gitlab-api-token", "gitlab-access-token"}),
}

#: Optional key choosing the repository's :class:`SyncMode`.
MODE_KEY = "MODE"

#: Every key an ``[assets.*]`` section may hold; others are warned about.
KNOWN_ASSET_KEYS = frozenset({*REQUIRED_ASSET_KEYS, MODE_KEY})

#: Sections recognised under ``[assets]``; at least one is required.
ASSET_NAMES = ("private", "common")

#: Directory below the root holding every git directory.
GIT_DIR_PARENT = Path(".local", "share", "workspace-admin")

#: Directory below the root holding every work tree.
WORK_TREE_PARENT = Path("assets")

#: Optional top-level key: seconds between two backups.
SYNC_INTERVAL_KEY = "SYNC_INTERVAL_SECONDS"

#: Every key the top level may hold.
KNOWN_TOP_LEVEL_KEYS = frozenset({SYNC_INTERVAL_KEY, "assets"})

#: Seconds between two backups when ``SYNC_INTERVAL_SECONDS`` is not set.
DEFAULT_SYNC_INTERVAL_SECONDS = 300

#: Interval bounds. Far above a day the wait itself fails
#: (``threading.TIMEOUT_MAX`` is about 49 days on Windows).
MIN_SYNC_INTERVAL_SECONDS = 1
MAX_SYNC_INTERVAL_SECONDS = 86400


class SyncMode(StrEnum):
    """
    How a repository follows its remote.

    ``sync`` commits, merges and pushes. ``pull`` only fast-forwards, so a
    read-only token is enough and users sharing it never overwrite each other.
    """

    PULL = "pull"
    SYNC = "sync"


#: Mode of a section without ``MODE``. ``common`` is shared, so it only pulls.
DEFAULT_MODES = {"private": SyncMode.SYNC, "common": SyncMode.PULL}


class ConfigError(Exception):
    """Raised when the configuration cannot be used; names file, section and key."""


class PlaceholderError(ConfigError):
    """Raised when a value is still the template's placeholder: setup is pending."""


@dataclass
class RepoConfig:
    """One repository, with absolute paths. ``token`` is kept out of ``repr``."""

    name: str
    repo_url: str
    branch: str
    token: str = field(repr=False)
    git_dir: Path
    work_tree: Path
    mode: SyncMode = SyncMode.SYNC
