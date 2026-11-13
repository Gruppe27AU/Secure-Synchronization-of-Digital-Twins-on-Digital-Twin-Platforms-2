"""
Unit tests for :mod:`admin.git.setup.validate`: the URL and branch handed to
git, two sections on one remote, and a work tree holding the config file.
"""

import json

import pytest

from admin.git.run.runner import GitTimeoutError
from admin.git.setup import validate as validate_module
from admin.git.setup.config import ConfigError
from admin.git.setup.load import load_config
from tests.unit.git.setup.samples import ROOT, VALID_CONFIG, write_config


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

    monkeypatch.setattr(validate_module, "run_git", fake_run)

    with pytest.raises(ConfigError, match="could not run git to check"):
        load_config(write_config(tmp_path, VALID_CONFIG), ROOT)


@pytest.mark.parametrize(
    "other_spelling",
    [
        "https://gitlab.com/user/private",
        "https://gitlab.com/user/private/",
        "https://GitLab.com/user/private.git",
        "https://gitlab.com/user/private.git/",
        "https://gitlab.com:443/user/private.git",
    ],
)
def test_two_spellings_of_one_remote_are_rejected(tmp_path, other_spelling):
    """Test .git, a trailing slash, letter case or :443 do not make a second remote."""
    content = VALID_CONFIG.replace(
        "https://gitlab.com/user/common.git", other_spelling
    ).replace('GIT_REPO_BRANCH = "develop"', 'GIT_REPO_BRANCH = "main"')

    with pytest.raises(ConfigError, match="both use"):
        load_config(write_config(tmp_path, content), ROOT)


def test_paths_that_differ_in_case_are_different_remotes(tmp_path):
    """Test the path keeps its case, since some hosts tell paths apart by it."""
    content = VALID_CONFIG.replace(
        "https://gitlab.com/user/common.git", "https://gitlab.com/User/Private.git"
    ).replace('GIT_REPO_BRANCH = "develop"', 'GIT_REPO_BRANCH = "main"')

    assert len(load_config(write_config(tmp_path, content), ROOT)) == 2


def test_another_port_is_another_remote(tmp_path):
    """Test a port other than 443 names another server, so it is allowed."""
    content = VALID_CONFIG.replace(
        "https://gitlab.com/user/common.git", "https://gitlab.com:8443/user/private.git"
    ).replace('GIT_REPO_BRANCH = "develop"', 'GIT_REPO_BRANCH = "main"')

    assert len(load_config(write_config(tmp_path, content), ROOT)) == 2
