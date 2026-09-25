"""
Unit tests for git backup configuration parsing.

Covers :mod:`admin.git.config`: reading the TOML config file, resolving the
two path roots, validating required keys, keeping the token out of the
representation, and falling back to the bundled template.
"""

from pathlib import Path

import pytest

from admin.git import config as config_module
from admin.git.config import ASSET_NAMES, ConfigError, load_config

VALID_CONFIG = """\
HOME_DIR = "/home/dtaas-user"
WORKSPACE_DIR = "/workspace"
WORKSPACE_APP_DIR = ".workspace"

[assets.private]
GIT_REPO_URL = "https://gitlab.com/user/private.git"
GIT_REPO_BRANCH = "main"
GIT_REPO_USERNAME = "user"
GIT_REPO_TOKEN = "private-token"
GIT_DIR = "private"
GIT_WORK_TREE = "assets/private"

[assets.common]
GIT_REPO_URL = "https://gitlab.com/user/common.git"
GIT_REPO_BRANCH = "develop"
GIT_REPO_USERNAME = "user"
GIT_REPO_TOKEN = "common-token"
GIT_DIR = "common"
GIT_WORK_TREE = "assets/common"
"""

PRIVATE_ONLY_CONFIG = """\
HOME_DIR = "/home/dtaas-user"
WORKSPACE_DIR = "/workspace"
WORKSPACE_APP_DIR = ".workspace"

[assets.private]
GIT_REPO_URL = "https://gitlab.com/user/private.git"
GIT_REPO_BRANCH = "main"
GIT_REPO_USERNAME = "user"
GIT_REPO_TOKEN = "private-token"
GIT_DIR = "private"
GIT_WORK_TREE = "assets/private"
"""


def write_config(tmp_path: Path, content: str) -> Path:
    """Write ``content`` to a config file and return its path."""
    config_path = tmp_path / "config.env"
    config_path.write_text(content, encoding="utf-8")
    return config_path


def test_load_config_returns_one_entry_per_section(tmp_path):
    """Test that both configured sections become repositories."""
    repos = load_config(write_config(tmp_path, VALID_CONFIG))

    assert [repo.name for repo in repos] == list(ASSET_NAMES)


def test_load_config_copies_plain_fields(tmp_path):
    """Test that the non-path fields are taken from the file unchanged."""
    repos = load_config(write_config(tmp_path, VALID_CONFIG))
    private, common = repos[0], repos[1]

    assert private.repo_url == "https://gitlab.com/user/private.git"
    assert private.branch == "main"
    assert private.username == "user"
    assert private.token == "private-token"
    assert common.branch == "develop"


def test_load_config_resolves_each_path_against_its_own_root(tmp_path):
    """Test that GIT_DIR and GIT_WORK_TREE use different roots."""
    private = load_config(write_config(tmp_path, VALID_CONFIG))[0]

    assert private.git_dir == Path("/workspace/private")
    assert private.work_tree == Path("/home/dtaas-user/.workspace/assets/private")


def test_load_config_accepts_absolute_app_dir(tmp_path):
    """Test that an absolute WORKSPACE_APP_DIR is not joined onto HOME_DIR."""
    content = VALID_CONFIG.replace(
        'WORKSPACE_APP_DIR = ".workspace"',
        'WORKSPACE_APP_DIR = "/srv/workspace-admin"',
    )

    private = load_config(write_config(tmp_path, content))[0]

    assert private.work_tree == Path("/srv/workspace-admin/assets/private")


def test_load_config_allows_a_single_section(tmp_path):
    """Test that configuring only [assets.private] is valid."""
    repos = load_config(write_config(tmp_path, PRIVATE_ONLY_CONFIG))

    assert [repo.name for repo in repos] == ["private"]


def test_repr_hides_the_token(tmp_path):
    """Test that printing a repository cannot leak its token."""
    private = load_config(write_config(tmp_path, VALID_CONFIG))[0]

    assert "private-token" not in repr(private)
    assert "private" in repr(private)


def test_missing_top_level_key_names_the_key(tmp_path):
    """Test that a missing top-level key is reported by name."""
    content = VALID_CONFIG.replace('WORKSPACE_DIR = "/workspace"\n', "")

    with pytest.raises(ConfigError, match="WORKSPACE_DIR"):
        load_config(write_config(tmp_path, content))


def test_missing_asset_key_names_key_and_section(tmp_path):
    """Test that a missing repository key names both the key and section."""
    content = VALID_CONFIG.replace('GIT_REPO_TOKEN = "common-token"\n', "")

    with pytest.raises(ConfigError, match=r"GIT_REPO_TOKEN.*\[assets\.common\]"):
        load_config(write_config(tmp_path, content))


def test_empty_value_is_rejected(tmp_path):
    """Test that a present but blank value is treated as missing."""
    content = VALID_CONFIG.replace(
        'GIT_REPO_URL = "https://gitlab.com/user/private.git"',
        'GIT_REPO_URL = "   "',
    )

    with pytest.raises(ConfigError, match="is empty"):
        load_config(write_config(tmp_path, content))


def test_wrong_type_is_rejected(tmp_path):
    """Test that a non-string value is reported as such."""
    content = VALID_CONFIG.replace('GIT_REPO_BRANCH = "main"', "GIT_REPO_BRANCH = 5")

    with pytest.raises(ConfigError, match="must be a quoted string"):
        load_config(write_config(tmp_path, content))


def test_malformed_toml_is_reported(tmp_path):
    """Test that a syntax error names the file instead of raising TOMLDecodeError."""
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(write_config(tmp_path, 'HOME_DIR = "/home\n'))


def test_missing_assets_section_is_reported(tmp_path):
    """Test that a config without any [assets] table is rejected."""
    content = """\
HOME_DIR = "/home/dtaas-user"
WORKSPACE_DIR = "/workspace"
WORKSPACE_APP_DIR = ".workspace"
"""

    with pytest.raises(ConfigError, match=r"no \[assets\] section"):
        load_config(write_config(tmp_path, content))


def test_empty_assets_section_is_reported(tmp_path):
    """Test that [assets] without private or common is rejected."""
    content = """\
HOME_DIR = "/home/dtaas-user"
WORKSPACE_DIR = "/workspace"
WORKSPACE_APP_DIR = ".workspace"

[assets]
"""

    with pytest.raises(ConfigError, match="no repositories configured"):
        load_config(write_config(tmp_path, content))


def test_unreadable_file_is_reported(tmp_path):
    """Test that an unreadable path is reported instead of raising OSError."""
    with pytest.raises(ConfigError, match="could not be read"):
        load_config(tmp_path)


def test_asset_that_is_not_a_section_is_reported(tmp_path):
    """Test that a scalar where a section is expected is rejected."""
    content = """\
HOME_DIR = "/home/dtaas-user"
WORKSPACE_DIR = "/workspace"
WORKSPACE_APP_DIR = ".workspace"

[assets]
private = "not-a-section"
"""

    with pytest.raises(ConfigError, match=r"\[assets\.private\] is not a section"):
        load_config(write_config(tmp_path, content))


def test_missing_file_falls_back_to_the_template(tmp_path):
    """Test that a missing config file is served from the bundled template."""
    repos = load_config(tmp_path / "does-not-exist.env")

    assert [repo.name for repo in repos] == list(ASSET_NAMES)


def test_missing_file_and_missing_template_is_reported(tmp_path, monkeypatch):
    """Test that losing both the config and the template is an error."""
    monkeypatch.setattr(
        config_module,
        "EXAMPLE_CONFIG_PATH",
        tmp_path / "no-template.example",
    )

    with pytest.raises(ConfigError, match="does not exist"):
        load_config(tmp_path / "does-not-exist.env")


def test_bundled_template_is_usable():
    """Test that the template shipped with the package parses and validates."""
    repos = load_config(config_module.EXAMPLE_CONFIG_PATH)

    assert [repo.name for repo in repos] == list(ASSET_NAMES)
    for repo in repos:
        assert repo.git_dir == Path("/workspace") / repo.name
        assert repo.work_tree == Path("/home/username/.workspace/assets") / repo.name
        assert repo.token
