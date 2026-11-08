"""Fixtures shared by the integration tests."""

import pytest

from tests.helpers.git_world import make_world


@pytest.fixture(name="world")
def fixture_world(tmp_path, monkeypatch):
    """A remote holding four files, cloned by the workspace and a colleague."""
    return make_world(tmp_path, monkeypatch)
