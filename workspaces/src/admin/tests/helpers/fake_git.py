"""
A stand-in for git, shared by the tests of :mod:`admin.git.sync`.

:class:`FakeGit` replaces :func:`admin.git.runner.run_git`: it records
every command and answers it with canned results, so no test of the sync
logic depends on a real repository.
"""

import subprocess
from dataclasses import dataclass, field

from admin.git import sync


#: Key for each git subcommand that is only ever run one way.
_PLAIN_KEYS = {
    "status": "status",
    "add": "add",
    "commit": "commit",
    "push": "push",
    "fetch": "fetch",
    "rev-parse": "git-dir",
    "symbolic-ref": "head",
    "ls-files": "unmerged",
    "show": "staged-file",
    "reset": "unstage",
    "checkout": "take-local",
    "rm": "remove-local",
    "merge": "merge",
    "diff": "marker-check",
}

#: Subcommands run more than one way, told apart by one of their arguments.
#: Checked before ``_PLAIN_KEYS``.
_ARGUMENT_KEYS = (
    ("ls-files", "-z", "conflicted-files"),
    ("add", "-A", "add"),
    ("add", "--", "mark-resolved"),
    ("reset", "--", "unstage-oversized"),
    ("commit", "--no-edit", "merge-commit"),
    ("merge", "--abort", "merge-abort"),
)


def command_key(command):
    """Name the git command in a way the tests can assert on."""
    if "rev-list" in command:
        return "incoming" if command[-1].startswith("HEAD..") else "outgoing"

    for word, argument, key in _ARGUMENT_KEYS:
        if word in command and argument in command:
            return key

    for word, key in _PLAIN_KEYS.items():
        if word in command:
            return key

    return None


def unmerged_entries(path, stages):
    """What ``git ls-files --unmerged -z`` prints for one conflicted path."""
    return "".join(f"100644 {'0' * 40} {stage}\t{path}\0" for stage in stages)


@dataclass
class FakeGit:  # pylint: disable=too-many-instance-attributes
    """
    Stands in for :func:`admin.git.runner.run_git` and records every git call.

    Args:
        status_output: What ``git status --porcelain -z`` prints. Empty
            means the working tree is clean. A single entry needs no
            trailing NUL.
        incoming: Commits the remote has that the workspace does not.
        outgoing: Commits the workspace has that the remote does not.
        conflicts: Conflicted files the workspace still has. Any conflict
            makes the merge fail, as real git does.
        deleted_locally: Conflicted files the workspace deleted.
        failing: Command key that should fail, for example ``"push"``.
        git_dir: What ``git rev-parse --absolute-git-dir`` prints. The
            default does not exist, so no operation is in progress. Tests
            that leave it so must keep ``info/exclude`` from being written
            there, see :func:`skip_writing_excludes`.
        head: What ``git symbolic-ref --quiet HEAD`` prints. None makes it
            exit with status 1 and print nothing, as for a detached HEAD.
        head_after_fetch: When set, ``head`` becomes this once the fetch
            has run, as if the user switched branches meanwhile.
        unmerged_output: What ``git ls-files --unmerged`` prints.
        marker_output: What ``git diff --cached --check`` prints. Non-empty
            makes it exit with status 2, as real git does.
        staged_files: Staged content per path, read by ``git show :<path>``.
        commands: Every command that was run, filled in as they arrive.
    """

    status_output: str = ""
    incoming: int = 0
    outgoing: int = 0
    conflicts: list = field(default_factory=list)
    deleted_locally: list = field(default_factory=list)
    failing: str | None = None
    git_dir: str = "/nonexistent/git-dir"
    head: str | None = "refs/heads/main"
    head_after_fetch: str | None = None
    unmerged_output: str = ""
    marker_output: str = ""
    staged_files: dict = field(default_factory=dict)
    commands: list = field(default_factory=list)
    timeouts: list = field(default_factory=list)

    def run(self, command, timeout):
        """Record the command and its time limit, and answer it."""
        self.commands.append(command)
        self.timeouts.append(timeout)
        key = command_key(command)

        if key == self.failing:
            return subprocess.CompletedProcess(
                command, 1, "", f"fatal: {key} was rejected"
            )

        if key == "fetch" and self.head_after_fetch:
            self.head = self.head_after_fetch

        if key == "head" and self.head is None:
            return subprocess.CompletedProcess(command, 1, "", "")

        if key == "merge" and (self.conflicts or self.deleted_locally):
            return subprocess.CompletedProcess(
                command, 1, "", "CONFLICT (content): Merge conflict"
            )

        if key == "marker-check" and self.marker_output:
            return subprocess.CompletedProcess(command, 2, self.marker_output, "")

        return subprocess.CompletedProcess(command, 0, self._stdout(command), "")

    def _stdout(self, command):
        """The output git would print for a command."""
        key = command_key(command)
        if key == "staged-file":
            # The last argument is ":<path>".
            return self.staged_files.get(command[-1][1:], "")

        if key == "conflicted-files":
            return "".join(
                [unmerged_entries(path, "123") for path in self.conflicts]
                + [unmerged_entries(path, "13") for path in self.deleted_locally]
            )

        return {
            "status": self.status_output,
            "incoming": str(self.incoming),
            "outgoing": str(self.outgoing),
            "git-dir": self.git_dir,
            "head": self.head,
            "unmerged": self.unmerged_output,
        }.get(key, "")

    def command_for(self, key):
        """Return the recorded command with this key, or None."""
        for command in self.commands:
            if command_key(command) == key:
                return command
        return None

    @property
    def keys(self):
        """The commands that were run, in order."""
        return [command_key(command) for command in self.commands]


def install_fake_git(monkeypatch, fake_git):
    """Make admin.git.sync use the fake instead of real git."""
    monkeypatch.setattr(sync, "run_git", fake_git.run)
    return fake_git


def skip_writing_excludes(monkeypatch):
    """
    Keep :mod:`admin.git.sync` from writing ``info/exclude``.

    :class:`FakeGit` reports a git directory that does not exist; writing
    the excludes there would create it on the real disk.
    """
    monkeypatch.setattr(sync, "_write_default_excludes", lambda _repo, _git_dir: None)
