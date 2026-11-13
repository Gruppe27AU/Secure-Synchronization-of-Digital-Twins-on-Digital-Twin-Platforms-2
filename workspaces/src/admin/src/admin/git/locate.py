"""
Finding the config file of the git backup.

:func:`discover_config` decides which ``config.toml`` the service uses,
checking in order:

1. ``$WORKSPACE_ADMIN_CONFIG``, when it is set and not empty
2. ``workspace-admin/config.toml`` in the config base directory:
   ``$XDG_CONFIG_HOME`` when it is an absolute path, otherwise ``~/.config``

It only chooses a path and never reads the file, so a variable naming a
file that does not exist is still the answer: nothing falls back past it.
Where the repositories are kept does not depend on the answer; that stays
below ``$PERSISTENT_DIR``.
"""

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

#: Environment variable naming the config file.
CONFIG_ENV_VAR = "WORKSPACE_ADMIN_CONFIG"

#: Filename of the config file.
DEFAULT_CONFIG_FILE = "config.toml"

#: Environment variable naming the base directory of user config files, as
#: in the XDG Base Directory Specification.
XDG_CONFIG_HOME_VAR = "XDG_CONFIG_HOME"

#: Directory holding the config file, below the config base directory.
APP_DIR = "workspace-admin"


class ConfigRule(StrEnum):
    """
    The rule that chose the config file.

    ``env`` is ``$WORKSPACE_ADMIN_CONFIG``; ``default`` is the file in the
    default directory, ``${XDG_CONFIG_HOME:-~/.config}/workspace-admin``.
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


def _config_home() -> Path:
    """
    Return the base directory of user config files.

    ``$XDG_CONFIG_HOME`` when it is set to an absolute path, otherwise
    ``~/.config``. The XDG specification has a relative value ignored, and
    an empty one counts as unset. With the variable set, the home directory
    is never looked up, so it need not be known.
    """
    xdg_config_home = os.environ.get(XDG_CONFIG_HOME_VAR)
    if xdg_config_home and Path(xdg_config_home).is_absolute():
        return Path(xdg_config_home)
    return Path.home() / ".config"


def default_config_path() -> Path:
    """Return ``${XDG_CONFIG_HOME:-~/.config}/workspace-admin/config.toml``."""
    return _config_home() / APP_DIR / DEFAULT_CONFIG_FILE


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
