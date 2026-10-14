"""
Unit tests for git backup configuration parsing.

Covers :mod:`admin.git.config`: reading the TOML config file, placing the
repositories below the root, validating required keys, keeping the token out of the
representation, and refusing placeholders copied from the bundled template.
"""

import json
from pathlib import Path

import pytest

from admin.git import config as config_module
from admin.git.config import ASSET_NAMES, ConfigError, load_config
from admin.git.runner import GitTimeoutError
from tests.helpers.templates import EXAMPLE_CONFIG_PATH

#: Root passed to every load_config() call, as $PERSISTENT_DIR in the image.
ROOT = Path("/workspace")

VALID_CONFIG = """\
[assets.private]
GIT_REPO_URL = "https://gitlab.com/user/private.git"
GIT_REPO_BRANCH = "main"
GIT_REPO_TOKEN = "private-token"

[assets.common]
GIT_REPO_URL = "https://gitlab.com/user/common.git"
GIT_REPO_BRANCH = "develop"
GIT_REPO_TOKEN = "common-token"
"""

PRIVATE_ONLY_CONFIG = """\
[assets.private]
GIT_REPO_URL = "https://gitlab.com/user/private.git"
GIT_REPO_BRANCH = "main"
GIT_REPO_TOKEN = "private-token"
"""


def write_config(tmp_path: Path, content: str) -> Path:
    """Write ``content`` to a config file and return its path."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(content, encoding="utf-8")
    return config_path


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

    git_dirs = ROOT / ".local" / "share" / "workspaceadmin"
    assert private.git_dir == git_dirs / "private"
    assert private.work_tree == ROOT / "assets" / "private"
    assert common.git_dir == git_dirs / "common"
    assert common.work_tree == ROOT / "assets" / "common"


def test_load_config_places_repositories_below_the_given_root(tmp_path):
    """Test that the root comes from the caller, not from the file."""
    private = load_config(write_config(tmp_path, VALID_CONFIG), Path("/srv"))[0]

    assert private.git_dir == Path("/srv/.local/share/workspaceadmin/private")
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
    content = VALID_CONFIG.replace('GIT_REPO_TOKEN = "common-token"\n', "")

    with pytest.raises(ConfigError, match=r"GIT_REPO_TOKEN.*\[assets\.common\]"):
        load_config(write_config(tmp_path, content), ROOT)


def test_empty_value_is_rejected(tmp_path):
    """Test that a present but blank value is treated as missing."""
    content = VALID_CONFIG.replace(
        'GIT_REPO_URL = "https://gitlab.com/user/private.git"',
        'GIT_REPO_URL = "   "',
    )

    with pytest.raises(ConfigError, match="is empty"):
        load_config(write_config(tmp_path, content), ROOT)


def test_wrong_type_is_rejected(tmp_path):
    """Test that a non-string value is reported as such."""
    content = VALID_CONFIG.replace('GIT_REPO_BRANCH = "main"', "GIT_REPO_BRANCH = 5")

    with pytest.raises(ConfigError, match="must be a quoted string"):
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


def test_missing_file_does_not_fall_back_to_the_template(tmp_path):
    """Test that a missing config file is an error, never the template."""
    with pytest.raises(ConfigError, match="could not be read"):
        load_config(tmp_path / "does-not-exist.toml", ROOT)


def test_bundled_template_is_rejected():
    """Test that an unedited copy of the template can never reach a remote."""
    with pytest.raises(ConfigError, match="placeholder"):
        load_config(EXAMPLE_CONFIG_PATH, ROOT)


def test_template_with_only_private_filled_in_is_accepted(tmp_path):
    """Test that the commented-out common section does not block private."""
    content = (
        EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8")
        .replace(
            'GIT_REPO_URL = "https://gitlab.com/username/repository.git"',
            'GIT_REPO_URL = "https://gitlab.com/user/private.git"',
            1,
        )
        .replace('GIT_REPO_TOKEN = "gitlab-api-token"', 'GIT_REPO_TOKEN = "t"', 1)
    )

    repos = load_config(write_config(tmp_path, content), ROOT)

    assert [repo.name for repo in repos] == ["private"]


@pytest.mark.parametrize(
    ("key", "real_value", "placeholder"),
    [
        (
            "GIT_REPO_URL",
            "https://gitlab.com/user/private.git",
            "https://gitlab.com/username/repository.git",
        ),
        ("GIT_REPO_TOKEN", "private-token", "gitlab-api-token"),
    ],
)
def test_placeholder_value_is_rejected(tmp_path, key, real_value, placeholder):
    """Test that each placeholder is refused, naming the key and section."""
    content = PRIVATE_ONLY_CONFIG.replace(
        f'{key} = "{real_value}"', f'{key} = "{placeholder}"'
    )

    with pytest.raises(ConfigError, match=rf"{key}.*\[assets\.private\].*placeholder"):
        load_config(write_config(tmp_path, content), ROOT)


def test_sections_sharing_a_remote_branch_are_rejected(tmp_path):
    """Test that private and common cannot back up to the same branch."""
    content = VALID_CONFIG.replace(
        "https://gitlab.com/user/common.git", "https://gitlab.com/user/private.git"
    ).replace('GIT_REPO_BRANCH = "develop"', 'GIT_REPO_BRANCH = "main"')

    with pytest.raises(ConfigError, match="both use"):
        load_config(write_config(tmp_path, content), ROOT)


def test_sections_sharing_a_url_on_different_branches_are_allowed(tmp_path):
    """Test that one remote may hold both repositories on separate branches."""
    content = VALID_CONFIG.replace(
        "https://gitlab.com/user/common.git", "https://gitlab.com/user/private.git"
    )

    repos = load_config(write_config(tmp_path, content), ROOT)

    assert [repo.branch for repo in repos] == ["main", "develop"]


def test_work_tree_containing_the_config_file_is_rejected(tmp_path):
    """Test that the config file, and with it the token, can never be pushed."""
    config_dir = tmp_path / "assets" / "common"
    config_dir.mkdir(parents=True)

    with pytest.raises(
        ConfigError, match=r"\[assets\.common\].*contains the config file"
    ):
        load_config(write_config(config_dir, VALID_CONFIG), tmp_path)


# --------------------------------------------------------------------------
# Values that end up on git's command line
# --------------------------------------------------------------------------


def _with_private(field: str, value: str) -> str:
    """
    Return VALID_CONFIG with one [assets.private] value replaced.

    ``json.dumps`` quotes the value as a TOML basic string, so a newline in
    ``value`` reaches the parser as a real newline.
    """
    original = {
        "GIT_REPO_URL": '"https://gitlab.com/user/private.git"',
        "GIT_REPO_BRANCH": '"main"',
    }[field]
    # The private section comes first, so count=1 leaves [assets.common] alone.
    return VALID_CONFIG.replace(
        f"{field} = {original}", f"{field} = {json.dumps(value)}", 1
    )


@pytest.mark.parametrize(
    "branch", ["main", "develop", "feature/new-model", "release-1.2"]
)
def test_valid_branch_is_accepted(tmp_path, branch):
    """Test that ordinary branch names pass validation unchanged."""
    content = _with_private("GIT_REPO_BRANCH", branch)

    private = load_config(write_config(tmp_path, content), ROOT)[0]

    assert private.branch == branch


@pytest.mark.parametrize(
    "branch",
    [
        "--upload-pack=touch /tmp/pwned;git-upload-pack",
        "--receive-pack=touch /tmp/pwned;git-receive-pack",
        "-x",
        "a..b",
        "has space",
        "a:b",
        "x~1",
        "HEAD",
        "@{-1}",
        "branch.lock",
    ],
)
def test_invalid_branch_is_rejected(tmp_path, branch):
    """Test that a branch git would not accept, or read as an option, is refused."""
    content = _with_private("GIT_REPO_BRANCH", branch)

    with pytest.raises(ConfigError, match="GIT_REPO_BRANCH.*not a valid branch"):
        load_config(write_config(tmp_path, content), ROOT)


@pytest.mark.parametrize(
    "url",
    [
        "http://gitlab.com/user/private.git",
        "ssh://git@gitlab.com/user/private.git",
        "git@gitlab.com:user/private.git",
        "file:///srv/git/private.git",
        "/srv/git/private.git",
        "ext::sh -c touch% /tmp/pwned",
        "--upload-pack=touch /tmp/pwned",
        "https://gitlab.com/user/private.git\nx",
        "https://gitlab.com/user/pri vate.git",
    ],
)
def test_non_https_url_is_rejected(tmp_path, url):
    """Test that only an https:// URL is accepted."""
    content = _with_private("GIT_REPO_URL", url)

    with pytest.raises(ConfigError, match="GIT_REPO_URL.*must be an https:// URL"):
        load_config(write_config(tmp_path, content), ROOT)


@pytest.mark.parametrize(
    "url",
    [
        "https://user@gitlab.com/user/private.git",
        "https://user:leaked-token@gitlab.com/user/private.git",
        "https://:leaked-token@gitlab.com/user/private.git",
    ],
)
def test_url_with_credentials_is_rejected(tmp_path, url):
    """Test that a URL with userinfo is refused, without echoing the token."""
    content = _with_private("GIT_REPO_URL", url)

    with pytest.raises(ConfigError, match="must not contain a username") as excinfo:
        load_config(write_config(tmp_path, content), ROOT)

    assert "leaked-token" not in str(excinfo.value)


def test_url_without_host_is_rejected(tmp_path):
    """Test that an https:// URL with no host is refused."""
    content = _with_private("GIT_REPO_URL", "https:///user/private.git")

    with pytest.raises(ConfigError, match="has no host"):
        load_config(write_config(tmp_path, content), ROOT)


def test_branch_check_that_hangs_is_reported(tmp_path, monkeypatch):
    """Test a git that never answers the branch check fails the config load."""

    def fake_run(_command, _timeout):
        raise GitTimeoutError("git did not finish within 300 seconds and was stopped")

    monkeypatch.setattr(config_module, "run_git", fake_run)

    with pytest.raises(ConfigError, match="could not run git to check"):
        load_config(write_config(tmp_path, VALID_CONFIG), ROOT)
