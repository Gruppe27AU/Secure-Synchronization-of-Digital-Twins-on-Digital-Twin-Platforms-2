"""
Unit tests for :mod:`admin.git.setup.validate`: required values, the token,
placeholders, MODE and keys that have no effect.
"""

import pytest

from admin.git.setup.config import ConfigError, SyncMode
from admin.git.setup.load import load_config
from tests.helpers.templates import EXAMPLE_CONFIG_PATH
from tests.unit.git.setup.samples import (
    PRIVATE_ONLY_CONFIG,
    ROOT,
    VALID_CONFIG,
    write_config,
)


@pytest.mark.parametrize("token_line", ["", 'GIT_REPO_TOKEN = ""\n'])
def test_a_pull_repository_needs_no_token(tmp_path, token_line):
    """Test a pull-only common, such as a public one, may leave the token out."""
    content = VALID_CONFIG.replace('GIT_REPO_TOKEN = "common-token"\n', token_line)

    common = load_config(write_config(tmp_path, content), ROOT)[1]

    assert common.mode is SyncMode.PULL
    assert common.token == ""


def test_a_sync_repository_needs_a_token(tmp_path):
    """Test a repository that pushes cannot leave the token out."""
    content = VALID_CONFIG.replace(
        'GIT_REPO_TOKEN = "common-token"\n', 'MODE = "sync"\n'
    )

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


def test_common_is_pull_only_and_private_syncs_by_default(tmp_path):
    """Test a shared repository never has one user's files pushed over another's."""
    repos = load_config(write_config(tmp_path, VALID_CONFIG), ROOT)
    private, common = repos[0], repos[1]

    assert private.mode is SyncMode.SYNC
    assert common.mode is SyncMode.PULL


@pytest.mark.parametrize("mode", ["pull", "sync"])
def test_mode_can_be_set_per_section(tmp_path, mode):
    """Test MODE overrides the section's default."""
    content = VALID_CONFIG.replace(
        'GIT_REPO_TOKEN = "common-token"',
        f'GIT_REPO_TOKEN = "common-token"\nMODE = "{mode}"',
    )

    common = load_config(write_config(tmp_path, content), ROOT)[1]

    assert common.mode is SyncMode(mode)


@pytest.mark.parametrize("value", ['"push"', '"Pull"', "1", "true"])
def test_an_unknown_mode_is_rejected(tmp_path, value):
    """Test a MODE that is neither pull nor sync is named in the error."""
    content = PRIVATE_ONLY_CONFIG + f"MODE = {value}\n"

    with pytest.raises(ConfigError, match=r"'MODE' in \[assets.private\] must be"):
        load_config(write_config(tmp_path, content), ROOT)


@pytest.mark.parametrize(
    ("content", "key", "where"),
    [
        ('HOME_DIR = "/home/dtaas-user"\n' + VALID_CONFIG, "HOME_DIR", "top level"),
        ("SYNC_INTERVAL = 600\n" + VALID_CONFIG, "SYNC_INTERVAL", "top level"),
        (PRIVATE_ONLY_CONFIG + 'GIT_DIR = "somewhere-else"\n', "GIT_DIR",
         "[assets.private]"),
        (VALID_CONFIG + '\n[assets.shared]\nGIT_REPO_URL = "x"\n', "shared",
         "[assets]"),
    ],
)
def test_a_key_with_no_effect_is_warned_about(tmp_path, caplog, content, key, where):
    """Test an old or misspelt key is reported, not silently ignored."""
    with caplog.at_level("WARNING"):
        load_config(write_config(tmp_path, content), ROOT)

    assert f"'{key}' in" in caplog.text
    assert where in caplog.text
    assert "no effect" in caplog.text


def test_known_keys_are_not_warned_about(tmp_path, caplog):
    """Test a complete, current config file loads without a warning."""
    content = "SYNC_INTERVAL_SECONDS = 60\n" + VALID_CONFIG + 'MODE = "pull"\n'
    config_path = write_config(tmp_path, content)
    config_path.chmod(0o600)

    with caplog.at_level("WARNING"):
        load_config(config_path, ROOT)

    assert not caplog.text
