"""Fixtures shared by the unit tests of admin.git.repo."""

from dataclasses import replace

import pytest

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
