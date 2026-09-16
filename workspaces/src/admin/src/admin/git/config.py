"""
Git backup configuration.

Reads the git backup config file and turns it into objects,
so the other modules can use the values without parsing anything.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RepoConfig:
    """Configuration for a single repository."""

    name: str
    repo_url: str
    branch: str
    username: str
    token: str
    git_dir: Path
    work_tree: Path


def load_config(config_path: Path) -> list[RepoConfig]:
    """Load the git backup config file and return a list of objects"""
    with open(config_path, "rb") as config_file:
        config = tomllib.load(config_file)

    home = Path(config["HOME_DIR"])
    workspace = Path(config["WORKSPACE_DIR"])
    app_dir = home / config["WORKSPACE_APP_DIR"]

    repos = []

    for name in ("private", "common"):
        asset = config["assets"][name]
        repos.append(
            RepoConfig(
                name=name,
                repo_url=asset["GIT_REPO_URL"],
                branch=asset["GIT_REPO_BRANCH"],
                username=asset["GIT_REPO_USERNAME"],
                token=asset["GIT_REPO_TOKEN"],
                git_dir=workspace / asset["GIT_DIR"],
                work_tree=app_dir / asset["GIT_WORK_TREE"],
            )
        )

    return repos