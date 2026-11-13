"""
Unit tests for :mod:`admin.git.setup.load` and :mod:`admin.git.setup.validate`:
SYNC_INTERVAL_SECONDS, and a config file others can read.
"""

import os

import pytest

from admin.git.setup.config import ConfigError, DEFAULT_SYNC_INTERVAL_SECONDS
from admin.git.setup.load import load_config, load_sync_interval
from tests.unit.git.setup.samples import ROOT, VALID_CONFIG, write_config


def _with_interval(value: str) -> str:
    """VALID_CONFIG with a top-level SYNC_INTERVAL_SECONDS line."""
    return f"SYNC_INTERVAL_SECONDS = {value}\n{VALID_CONFIG}"


def test_sync_interval_defaults_when_not_set(tmp_path):
    """Test a config file without the key keeps the old five minutes."""
    config_path = write_config(tmp_path, VALID_CONFIG)

    assert load_sync_interval(config_path) == DEFAULT_SYNC_INTERVAL_SECONDS == 300


@pytest.mark.parametrize("seconds", [1, 60, 86400])
def test_sync_interval_is_read_from_the_file(tmp_path, seconds):
    """Test any whole number of seconds from 1 to a day is used as given."""
    config_path = write_config(tmp_path, _with_interval(str(seconds)))

    assert load_sync_interval(config_path) == seconds


@pytest.mark.parametrize(
    "value, problem",
    [
        ("0", "between 1 and 86400 seconds, got 0"),
        ("-5", "between 1 and 86400 seconds, got -5"),
        ("86401", "between 1 and 86400 seconds, got 86401"),
        ("300.0", "a whole number of seconds, got float"),
        ('"300"', "a whole number of seconds, got str"),
        ("true", "a whole number of seconds, got bool"),
    ],
)
def test_unusable_sync_interval_falls_back_with_a_warning(
    tmp_path, caplog, value, problem
):
    """Test a bad value means 300 seconds and a warning, not a stopped backup."""
    config_path = write_config(tmp_path, _with_interval(value))

    with caplog.at_level("WARNING"):
        assert load_sync_interval(config_path) == DEFAULT_SYNC_INTERVAL_SECONDS

    assert f"'SYNC_INTERVAL_SECONDS' must be {problem}" in caplog.text
    assert "using the default of 300 seconds" in caplog.text
    # The repositories are still usable, so cloning goes ahead.
    assert len(load_config(config_path, ROOT)) == 2


def test_sync_interval_of_an_unreadable_file_is_reported(tmp_path):
    """Test a missing file is an error here; bootstrap decides what it means."""
    with pytest.raises(ConfigError, match="could not be read"):
        load_sync_interval(tmp_path / "missing.toml")


def test_sync_interval_inside_a_section_falls_back_with_a_warning(
    tmp_path, caplog
):
    """Test a key appended at the end of the file is not silently ignored."""
    config_path = write_config(tmp_path, VALID_CONFIG + "SYNC_INTERVAL_SECONDS = 60\n")

    with caplog.at_level("WARNING"):
        assert load_sync_interval(config_path) == DEFAULT_SYNC_INTERVAL_SECONDS

    assert "is in [assets.common], where it has no effect; move it" in caplog.text
    assert len(load_config(config_path, ROOT)) == 2


def test_a_valid_sync_interval_logs_no_warning(tmp_path, caplog):
    """Test the warning is only for values that cannot be used."""
    config_path = write_config(tmp_path, _with_interval("60"))

    with caplog.at_level("WARNING"):
        load_sync_interval(config_path)

    assert not caplog.text


@pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX-only")
def test_a_config_file_others_can_read_is_warned_about(tmp_path, caplog):
    """Test a supplied config file holding tokens readable by all is reported."""
    config_path = write_config(tmp_path, VALID_CONFIG)
    config_path.chmod(0o644)

    with caplog.at_level("WARNING"):
        load_config(config_path, ROOT)

    assert "chmod 600" in caplog.text
    assert "private-token" not in caplog.text


@pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX-only")
def test_a_private_config_file_is_not_warned_about(tmp_path, caplog):
    """Test the mode the template is provisioned with passes quietly."""
    config_path = write_config(tmp_path, VALID_CONFIG)
    config_path.chmod(0o600)

    with caplog.at_level("WARNING"):
        load_config(config_path, ROOT)

    assert "chmod" not in caplog.text
