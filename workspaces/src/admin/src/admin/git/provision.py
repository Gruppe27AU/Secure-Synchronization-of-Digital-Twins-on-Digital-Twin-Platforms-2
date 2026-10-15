"""
Setup of the git config directory.

A workspace without a config file gets a copy to fill in.
The directory also gets a ``.gitignore`` that ignores everything in it,
so a repository the user creates further up never commits the token.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

GITIGNORE_NAME = ".gitignore"

GITIGNORE_CONTENT = b"*\n"

#: The config file holds tokens, so only its owner may read it.
CONFIG_FILE_MODE = 0o600


def _write_new_file(path: Path, data: bytes, mode: int) -> None:
    """
    Write ``data`` to a new file.

    Raises:
        FileExistsError: If ``path`` already exists.
        OSError: If the file cannot be created or written.
    """
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "wb") as file:
        file.write(data)


def _ensure_gitignore(config_dir: Path) -> None:
    """Add the ignore file to ``config_dir`` unless one is already there."""
    try:
        _write_new_file(config_dir / GITIGNORE_NAME, GITIGNORE_CONTENT, 0o644)
    except FileExistsError:
        pass


def provision_config(config_path: Path, template_path: Path | None) -> bool:
    """
    Create the config file from the template when it does not exist yet.

    Never raises: a failure is logged, and the git backup then stays
    disabled as it would without a config file.

    Args:
        config_path: Where the service reads its config file.
        template_path: The template to copy, or None when none is installed,
            as when the service runs outside the workspace image.

    Returns:
        True if the config file was created now, so it still holds only
        placeholders. False if it already existed or could not be created.
    """
    try:
        if config_path.exists():
            if config_path.parent.is_dir():
                _ensure_gitignore(config_path.parent)
            return False
        if template_path is None:
            return False

        template = template_path.read_bytes()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        _ensure_gitignore(config_path.parent)
        _write_new_file(config_path, template, CONFIG_FILE_MODE)
    except OSError as error:
        logger.error(
            "Could not create %s from %s: %s", config_path, template_path, error
        )
        return False

    logger.warning(
        "Created %s; fill in your repositories and restart the workspace",
        config_path,
    )
    return True
