"""
Unit tests for :mod:`admin.git.setup.load`: reading config.toml into
repositories, and errors in the file itself.
"""

from pathlib import Path

import pytest

from admin.git.setup.config import ASSET_NAMES, ConfigError
from admin.git.setup.load import load_config
from tests.unit.git.setup.samples import (
    PRIVATE_ONLY_CONFIG,
    ROOT,
    VALID_CONFIG,
    write_config,
)


def test_load_config_returns_one_entry_per_section(tmp_path):
    """Test that both configured sections become repositories."""
    repos = load_config(write_config(tmp_path, VALID_CONFIG), ROOT)

    assert [repo.name for repo in repos] == list(ASSET_NAMES)


def test_load_config_copies_plain_fields(tmp_path):
    """Test that the non-path fields are taken from the file unchanged."""
    repos = load_config(write_config(tmp_path, VALID_CONFIG), ROOT)
    private, common = repos[0], repos[1]

    assert private.repo_url == "https://gitlab.com/user/private.git"
    assert private.branch == "main"
    assert private.token == "private-token"
    assert common.branch == "develop"


def test_load_config_derives_paths_from_the_section_name(tmp_path):
    """Test that each repository's paths are built from its section name."""
    repos = load_config(write_config(tmp_path, VALID_CONFIG), ROOT)
    private, common = repos[0], repos[1]

    git_dirs = ROOT / ".local" / "share" / "workspace-admin"
    assert private.git_dir == git_dirs / "private"
    assert private.work_tree == ROOT / "assets" / "private"
    assert common.git_dir == git_dirs / "common"
    assert common.work_tree == ROOT / "assets" / "common"


def test_load_config_places_repositories_below_the_given_root(tmp_path):
    """Test that the root comes from the caller, not from the file."""
    private = load_config(write_config(tmp_path, VALID_CONFIG), Path("/srv"))[0]

    assert private.git_dir == Path("/srv/.local/share/workspace-admin/private")
    assert private.work_tree == Path("/srv/assets/private")


def test_old_top_level_paths_are_ignored(tmp_path):
    """Test that a file still holding the removed path keys keeps the layout."""
    content = 'WORKSPACE_DIR = "/elsewhere"\n' + VALID_CONFIG

    private = load_config(write_config(tmp_path, content), ROOT)[0]

    assert private.work_tree == ROOT / "assets" / "private"


def test_load_config_allows_a_single_section(tmp_path):
    """Test that configuring only [assets.private] is valid."""
    repos = load_config(write_config(tmp_path, PRIVATE_ONLY_CONFIG), ROOT)

    assert [repo.name for repo in repos] == ["private"]


def test_repr_hides_the_token(tmp_path):
    """Test that printing a repository cannot leak its token."""
    private = load_config(write_config(tmp_path, VALID_CONFIG), ROOT)[0]

    assert "private-token" not in repr(private)
    assert "private" in repr(private)


def test_missing_asset_key_names_key_and_section(tmp_path):
    """Test that a missing repository key names both the key and section."""
    content = VALID_CONFIG.replace('GIT_REPO_BRANCH = "develop"\n', "")

    with pytest.raises(ConfigError, match=r"GIT_REPO_BRANCH.*\[assets\.common\]"):
        load_config(write_config(tmp_path, content), ROOT)


def test_malformed_toml_is_reported(tmp_path):
    """Test that a syntax error names the file instead of raising TOMLDecodeError."""
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(write_config(tmp_path, '[assets\n'), ROOT)


def test_missing_assets_section_is_reported(tmp_path):
    """Test that a config without any [assets] table is rejected."""
    content = "# nothing configured yet\n"

    with pytest.raises(ConfigError, match=r"no \[assets\] section"):
        load_config(write_config(tmp_path, content), ROOT)


def test_empty_assets_section_is_reported(tmp_path):
    """Test that [assets] without private or common is rejected."""
    content = """\
[assets]
"""

    with pytest.raises(ConfigError, match="no repositories configured"):
        load_config(write_config(tmp_path, content), ROOT)


def test_unreadable_file_is_reported(tmp_path):
    """Test that an unreadable path is reported instead of raising OSError."""
    with pytest.raises(ConfigError, match="could not be read"):
        load_config(tmp_path, ROOT)


def test_asset_that_is_not_a_section_is_reported(tmp_path):
    """Test that a scalar where a section is expected is rejected."""
    content = """\
[assets]
private = "not-a-section"
"""

    with pytest.raises(ConfigError, match=r"\[assets\.private\] is not a section"):
        load_config(write_config(tmp_path, content), ROOT)
