"""Fixtures for the scheduler tests; enabled per module with ``pytestmark``."""

import pytest

from admin.git.service import scheduler


@pytest.fixture(name="restored")
def fixture_restored(monkeypatch):
    """Record the working trees whose .git file would be written back."""
    restored = []
    monkeypatch.setattr(
        scheduler, "restore_git_file", lambda repo: restored.append(repo.name)
    )
    return restored


@pytest.fixture(name="not_cloned")
def fixture_not_cloned(monkeypatch):
    """Names of the repositories to treat as not cloned; all others are."""
    not_cloned = set()
    monkeypatch.setattr(
        scheduler, "is_cloned", lambda git_dir: git_dir not in not_cloned
    )
    return not_cloned
