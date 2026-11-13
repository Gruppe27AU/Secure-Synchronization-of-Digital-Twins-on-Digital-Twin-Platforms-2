"""
Unit tests for :mod:`admin.git.repo.refs`, and how remote commands use them.

Real git is never invoked: :class:`tests.helpers.fake_git.FakeGit` answers
every command.
"""

from dataclasses import replace

import pytest

from admin.git.repo.pull import pull_changes
from admin.git.repo.push import push_if_ahead
from admin.git.repo.refs import fetch_refspec, local_branch, push_refspec, remote_ref
from tests.helpers.fake_git import FakeGit, install_fake_git


@pytest.mark.parametrize("step", ["fetch", "push"])
def test_remote_commands_allow_only_https(monkeypatch, repo, step):
    """Test fetch and push refuse every transport except https."""
    fake_git = install_fake_git(monkeypatch, FakeGit(incoming=0, outgoing=1))

    pull_changes(repo)
    push_if_ahead(repo)

    command = fake_git.command_for(step)
    options = command[: command.index(step)]
    assert "protocol.allow=never" in options
    assert "protocol.https.allow=always" in options


@pytest.mark.parametrize(
    "branch",
    [
        "--upload-pack=touch /tmp/pwned;git-upload-pack",
        "--receive-pack=touch /tmp/pwned;git-receive-pack",
    ],
)
def test_a_dash_led_branch_is_never_passed_as_an_option(monkeypatch, repo, branch):
    """Test an option-shaped branch only ever appears after ``--``.

    ``load_config`` refuses such a branch, but sync must not depend on
    that: a value git reads as ``--upload-pack`` would run a command.
    """
    fake_git = install_fake_git(monkeypatch, FakeGit(incoming=0, outgoing=1))
    repo = replace(repo, branch=branch)

    pull_changes(repo)
    push_if_ahead(repo)

    for step in ("fetch", "push"):
        command = fake_git.command_for(step)
        separator = command.index("--")
        assert not any(part.startswith("-") for part in command[separator + 1 :])
        assert not any(branch in part for part in command[:separator])


def test_the_refs_name_the_configured_branch(repo):
    """Test each ref and refspec spells out the branch in full."""
    assert remote_ref(repo) == "origin/main"
    assert local_branch(repo) == "refs/heads/main"
    assert fetch_refspec(repo) == "+refs/heads/main:refs/remotes/origin/main"
    assert push_refspec(repo) == "refs/heads/main:refs/heads/main"
