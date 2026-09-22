"""
Shared :class:`admin.git.config.RepoConfig` builder for the git tests.

Keeps the test modules from each repeating the same seven-field
construction, and lets every test spell out only the fields it actually
cares about.
"""

from admin.git.config import RepoConfig

#: Field values used by every test repository unless overridden.
DEFAULTS = {
    "repo_url": "https://example.com/org/repo.git",
    "branch": "main",
    "username": "",
    "token": "",
    "git_dir": None,
    "work_tree": None,
}


def make_repo(name="common", **overrides):
    """
    Build a repository configuration for a test.

    Args:
        name: Name of the ``[assets.<name>]`` section.
        overrides: Any field to set instead of its default.

    Returns:
        The repository configuration.
    """
    return RepoConfig(name=name, **{**DEFAULTS, **overrides})
