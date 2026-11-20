"""
Finding the config file of the git backup.

:func:`discover_config` decides which ``config.toml`` the service uses,
checking in order:

1. ``$WORKSPACE_ADMIN_CONFIG``, when it is set and not empty
2. ``.config/workspace-admin/config.toml`` below :func:`persistent_root`:
   ``$PERSISTENT_DIR``, or the home directory when it is unset

It only chooses a path and never reads the file, so a variable naming a
file that does not exist is still the answer: nothing falls back past it.
The repositories are kept below :func:`persistent_root` whatever the answer,
so by default the config file and the repositories share one root.
"""

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

#: Environment variable naming the config file.
CONFIG_ENV_VAR = "WORKSPACE_ADMIN_CONFIG"

#: Filename of the config file.
DEFAULT_CONFIG_FILE = "config.toml"

#: Environment variable naming the root of the config file and repositories.
PERSISTENT_DIR_ENV_VAR = "PERSISTENT_DIR"

#: Directory holding the config file, below :func:`persistent_root`.
CONFIG_DIR = Path(".config", "workspace-admin")


class ConfigRule(StrEnum):
    """
    The rule that chose the config file.

    ``env`` is ``$WORKSPACE_ADMIN_CONFIG``; ``default`` is the file in the
    default directory below :func:`persistent_root`.
    """

    ENV = "env"
    DEFAULT = "default"


@dataclass(frozen=True)
class ConfigLocation:
    """
    The config file the service uses, and why.

    Attributes:
        path: Absolute path of the config file. It need not exist.
        rule: The rule that chose it.
    """

    path: Path
    rule: ConfigRule


def persistent_root() -> Path:
    """Return ``$PERSISTENT_DIR``, or the home directory when it is unset."""
    persistent_dir = os.environ.get(PERSISTENT_DIR_ENV_VAR)
    return Path(persistent_dir) if persistent_dir else Path.home()


def default_config_path() -> Path:
    """Return ``.config/workspace-admin/config.toml`` below the root."""
    return persistent_root() / CONFIG_DIR / DEFAULT_CONFIG_FILE


def discover_config() -> ConfigLocation:
    """
    Choose the config file from the environment.

    An empty ``$WORKSPACE_ADMIN_CONFIG`` counts as unset. A relative one is
    resolved against the current directory now, so the result does not
    change if the directory does later.

    Returns:
        The chosen config file, as an absolute path, and the rule that
        chose it.
    """
    configured = os.environ.get(CONFIG_ENV_VAR)
    if configured:
        return ConfigLocation(Path(configured).absolute(), ConfigRule.ENV)
    return ConfigLocation(default_config_path(), ConfigRule.DEFAULT)
