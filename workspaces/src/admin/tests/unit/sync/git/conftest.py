"""Fixtures shared by the unit tests of admin.sync.git."""

from dataclasses import replace

import pytest

from admin.sync.git import scheduler
from tests.helpers.repo_factory import make_repo


@pytest.fixture(name="repo")
def fixture_repo(tmp_path):
    """A cloned repository without credentials; its work tree exists."""
    repo = make_repo(
        git_dir=tmp_path / "workspace" / "common",
        work_tree=tmp_path / "app" / "assets" / "common",
    )
    repo.work_tree.mkdir(parents=True)
    return repo


@pytest.fixture(name="repo_with_credentials")
def fixture_repo_with_credentials(repo):
    """The same repository, with a token."""
    return replace(repo, token="secret-token")


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
