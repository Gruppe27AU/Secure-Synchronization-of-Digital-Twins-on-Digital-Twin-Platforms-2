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


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("GIT_DIR", "/etc"),
        ("GIT_WORK_TREE", "/etc"),
        ("GIT_DIR", "."),
        ("GIT_WORK_TREE", "."),
        ("GIT_WORK_TREE", "./"),
    ],
)
def test_path_outside_or_equal_to_its_root_is_rejected(tmp_path, key, value):
    """Test that an absolute fragment or one naming the root itself is rejected."""
    original = {"GIT_DIR": '"private"', "GIT_WORK_TREE": '"assets/private"'}[key]
    content = VALID_CONFIG.replace(f"{key} = {original}", f'{key} = "{value}"')

    with pytest.raises(
        ConfigError, match=rf"'{key}' in \[assets\.private\] must name a subdirectory"
    ):
        load_config(write_config(tmp_path, content))


@pytest.mark.parametrize("value", ["../private", "assets/../../etc", "a/.."])
def test_parent_reference_is_rejected(tmp_path, value):
    """Test that '..' is rejected even when it would stay inside the root."""
    content = VALID_CONFIG.replace(
        'GIT_WORK_TREE = "assets/private"', f'GIT_WORK_TREE = "{value}"'
    )

    with pytest.raises(ConfigError, match="must not contain '..'"):
        load_config(write_config(tmp_path, content))


def test_redundant_path_components_are_normalised(tmp_path):
    """Test that '.' components and trailing slashes do not change the path."""
    content = VALID_CONFIG.replace(
        'GIT_WORK_TREE = "assets/private"', 'GIT_WORK_TREE = "./assets/./private/"'
    )

    private = load_config(write_config(tmp_path, content))[0]

    assert private.work_tree == Path("/home/dtaas-user/.workspace/assets/private")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('GIT_WORK_TREE = "assets/common"', 'GIT_WORK_TREE = "assets/private"'),
        ('GIT_WORK_TREE = "assets/private"', 'GIT_WORK_TREE = "assets"'),
        ('GIT_DIR = "common"', 'GIT_DIR = "private"'),
        ('GIT_DIR = "common"', 'GIT_DIR = "private/common"'),
    ],
)
def test_overlapping_directories_are_rejected(tmp_path, old, new):
    """Test that equal or nested directories across repositories are rejected."""
    content = VALID_CONFIG.replace(old, new)

    with pytest.raises(ConfigError, match="overlap"):
        load_config(write_config(tmp_path, content))


def test_git_dir_inside_a_work_tree_is_rejected(tmp_path):
    """Test that a git directory cannot be committed as work tree content."""
    content = VALID_CONFIG.replace(
        'WORKSPACE_APP_DIR = ".workspace"', 'WORKSPACE_APP_DIR = "/workspace"'
    ).replace('GIT_DIR = "private"', 'GIT_DIR = "assets/private/.git-private"')

    with pytest.raises(
        ConfigError,
        match=r"GIT_DIR in \[assets\.private\].*GIT_WORK_TREE in \[assets\.private\]",
    ):
        load_config(write_config(tmp_path, content))


def test_work_tree_containing_the_config_file_is_rejected(tmp_path):
    """Test that the config file, and with it the token, can never be pushed."""
    content = VALID_CONFIG.replace(
        'HOME_DIR = "/home/dtaas-user"', f'HOME_DIR = "{tmp_path.as_posix()}"'
    )
    config_dir = tmp_path / ".workspace" / "assets" / "common"
    config_dir.mkdir(parents=True)

    with pytest.raises(
        ConfigError, match=r"\[assets\.common\].*contains the config file"
    ):
        load_config(write_config(config_dir, content))
