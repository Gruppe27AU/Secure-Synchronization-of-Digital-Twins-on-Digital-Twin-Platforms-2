"""
Unit tests for creating the git backup config directory.

Covers :mod:`admin.git.provision`: copying the template into place, never
overwriting an existing file, the ignore file, and logging failures.
"""

import os
import stat

import pytest

from admin.git.provision import (
    CONFIG_FILE_MODE,
    GITIGNORE_CONTENT,
    GITIGNORE_NAME,
    provision_config,
)

TEMPLATE = b'[assets.private]\nGIT_REPO_TOKEN = "gitlab-api-token"\n'

posix_only = pytest.mark.skipif(
    os.name != "posix", reason="file modes are POSIX-only"
)


@pytest.fixture(name="template")
def fixture_template(tmp_path):
    """A template file outside the config directory."""
    path = tmp_path / "template.toml"
    path.write_bytes(TEMPLATE)
    return path


@pytest.fixture(name="config_path")
def fixture_config_path(tmp_path):
    """Where the config file belongs; its directory does not exist yet."""
    return tmp_path / ".config" / "workspace-admin" / "config.toml"


def test_missing_config_is_created_from_the_template(config_path, template, caplog):
    """Test the template is copied into place and the user is told."""
    with caplog.at_level("WARNING"):
        assert provision_config(config_path, template)

    assert config_path.read_bytes() == TEMPLATE
    assert "fill in" in caplog.text


@posix_only
def test_created_config_is_readable_by_its_owner_only(config_path, template):
    """Test the file that will hold the token is not group or world readable."""
    provision_config(config_path, template)

    assert stat.S_IMODE(config_path.stat().st_mode) == CONFIG_FILE_MODE


def test_created_directory_ignores_everything(config_path, template):
    """Test the ignore file keeps the config directory out of any repository."""
    provision_config(config_path, template)

    gitignore = config_path.parent / GITIGNORE_NAME
    assert gitignore.read_bytes() == GITIGNORE_CONTENT


def test_existing_config_is_never_overwritten(config_path, template):
    """Test a filled-in config file survives every restart."""
    config_path.parent.mkdir(parents=True)
    config_path.write_bytes(b"filled in")

    assert not provision_config(config_path, template)

    assert config_path.read_bytes() == b"filled in"


def test_existing_config_still_gets_the_ignore_file(config_path, template):
    """Test a config placed by the operator is protected too."""
    config_path.parent.mkdir(parents=True)
    config_path.write_bytes(b"filled in")

    provision_config(config_path, template)

    assert (config_path.parent / GITIGNORE_NAME).read_bytes() == GITIGNORE_CONTENT


def test_existing_ignore_file_is_kept(config_path, template):
    """Test an ignore file the user has edited is left alone."""
    config_path.parent.mkdir(parents=True)
    gitignore = config_path.parent / GITIGNORE_NAME
    gitignore.write_bytes(b"custom\n")

    provision_config(config_path, template)

    assert gitignore.read_bytes() == b"custom\n"


def test_nothing_is_created_without_a_template(config_path):
    """Test that outside the image the backup simply stays disabled."""
    assert not provision_config(config_path, None)

    assert not config_path.parent.exists()


def test_missing_template_is_logged_and_creates_nothing(config_path, tmp_path, caplog):
    """Test an unreadable template is reported instead of raised."""
    with caplog.at_level("ERROR"):
        assert not provision_config(config_path, tmp_path / "missing.toml")

    assert not config_path.exists()
    assert "Could not create" in caplog.text
