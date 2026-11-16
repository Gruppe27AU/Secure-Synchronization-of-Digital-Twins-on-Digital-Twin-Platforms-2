"""
Stand-in for git in the sync tests.

:class:`FakeGit` replaces :func:`admin.git.run.runner.run_git` where
:mod:`admin.git.run.command` uses it, records every command and answers it with
canned results.
"""

import subprocess
from dataclasses import dataclass, field

from admin.git.run import command as command_module


#: Key for each git subcommand that is only ever run one way.
_PLAIN_KEYS = {
    "status": "status",
    "commit": "commit",
    "push": "push",
    "fetch": "fetch",
    "symbolic-ref": "head",
    "rev-parse": "ref",
    "show": "staged-file",
    "reset": "unstage",
    "merge": "merge",
    "diff": "marker-check",
}

#: Keys of the commands that talk to the remote.
_REMOTE_KEYS = {"fetch", "push", "backup-push"}

#: Subcommands run more than one way, told apart by one of their arguments.
#: Checked before ``_PLAIN_KEYS``.
_ARGUMENT_KEYS = (
    ("ls-files", "--deleted", "deleted-files"),
    ("ls-files", "--cached", "tracked-files"),
    ("ls-files", "--others", "untracked-files"),
    ("ls-files", "--modified", "modified-files"),
    ("ls-files", "--unmerged", "unmerged"),
    ("add", "-A", "add"),
    ("diff", "--quiet", "staged-check"),
    ("merge", "--abort", "merge-abort"),
    ("merge", "--ff-only", "fast-forward"),
)


def command_key(command):
    """Name the git command in a way the tests can assert on."""
    if "rev-list" in command:
        return "incoming" if command[-1].startswith("HEAD..") else "outgoing"

    if "push" in command and command[-1].startswith("+"):
        return "backup-push"

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


def _listing(paths):
    """What ``git ls-files -z`` prints for these paths."""
    return "".join(f"{path}\0" for path in paths)


@dataclass
class FakeGit:  # pylint: disable=too-many-instance-attributes
    """
    Stands in for :func:`admin.git.run.runner.run_git` and records every git call.

    Args:
        status_output: What ``git status --porcelain`` prints. Empty means
            the working tree is clean.
        incoming: Commits the remote has that the workspace does not.
        outgoing: Commits the workspace has that the remote does not.
        conflicts: Conflicted files the workspace still has. Any conflict
            makes the merge fail, as real git does.
        deleted_locally: Conflicted files the workspace deleted.
        failing: Command key that should fail, for example ``"push"``.
        failure_message: What a failing command prints. By default it names
            the command.
        remote_failure: What every command talking to the remote prints as
            it fails, as with a rejected token. None lets them succeed.
        head: What ``git symbolic-ref --quiet HEAD`` prints. None makes it
            exit with status 1 and print nothing, as for a detached HEAD.
        head_after_fetch: When set, ``head`` becomes this once the fetch
            has run, as if the user switched branches meanwhile.
        unmerged_output: What ``git ls-files --unmerged -z`` prints before
            any merge. Once a merge has failed, the conflicts are listed.
        marker_output: What ``git diff --cached --check`` prints. Non-empty
            makes it exit with status 2, as real git does.
        staged_files: Staged content per path, read by ``git show :<path>``.
        staged: Whether ``git diff --cached --quiet`` finds anything staged.
        deleted_files: How many tracked files ``git ls-files --deleted``
            lists as missing from the working tree.
        tracked_files: How many files ``git ls-files --cached`` lists.
        untracked: What ``git ls-files --others`` lists.
        modified: What ``git ls-files --modified`` lists.
        missing_refs: Refs ``git rev-parse --verify`` does not find.
        commands: Every command that was run, filled in as they arrive.
        timeouts: The time limit of each command.
        envs: The extra environment of each command.
    """

    status_output: str = ""
    incoming: int = 0
    outgoing: int = 0
    conflicts: list = field(default_factory=list)
    deleted_locally: list = field(default_factory=list)
    failing: str | None = None
    failure_message: str | None = None
    remote_failure: str | None = None
    head: str | None = "refs/heads/main"
    head_after_fetch: str | None = None
    unmerged_output: str = ""
    marker_output: str = ""
    staged_files: dict = field(default_factory=dict)
    staged: bool = True
    deleted_files: int = 0
    tracked_files: int = 0
    untracked: list = field(default_factory=list)
    modified: list = field(default_factory=list)
    missing_refs: set = field(default_factory=set)
    commands: list = field(default_factory=list)
    timeouts: list = field(default_factory=list)
    envs: list = field(default_factory=list)
    _merge_failed: bool = False

    def run(self, command, timeout, env=None):
        """Record the command, its time limit and environment, and answer it."""
        self.commands.append(command)
        self.timeouts.append(timeout)
        self.envs.append(env or {})
        code, stdout, stderr = self._answer(command_key(command), command)
        return subprocess.CompletedProcess(command, code, stdout, stderr)

    def _answer(self, key, command):  # pylint: disable=too-many-return-statements
        """Return git's exit status, standard output and standard error."""
        if key is not None and key == self.failing:
            return 1, "", self.failure_message or f"fatal: {key} was rejected"
        if self.remote_failure and key in _REMOTE_KEYS:
            return 128, "", self.remote_failure
        if key == "fetch" and self.head_after_fetch:
            self.head = self.head_after_fetch
        if key == "head" and self.head is None:
            return 1, "", ""
        if key == "ref" and command[-1].removesuffix("^{commit}") in self.missing_refs:
            return 1, "", ""
        if key == "merge" and (self.conflicts or self.deleted_locally):
            self._merge_failed = True
            return 1, "", "CONFLICT (content): Merge conflict"
        if key == "marker-check" and self.marker_output:
            return 2, self.marker_output, ""
        if key == "staged-check":
            return (1 if self.staged else 0), "", ""
        return 0, self._stdout(command), ""

    def _stdout(self, command):
        """The output git would print for a command."""
        key = command_key(command)
        if key == "staged-file":
            # The last argument is ":<path>".
            return self.staged_files.get(command[-1][1:], "")
        if key == "unmerged":
            if not self._merge_failed:
                return self.unmerged_output
            return "".join(
                [unmerged_entries(path, "123") for path in self.conflicts]
                + [unmerged_entries(path, "13") for path in self.deleted_locally]
            )
        if key in ("deleted-files", "tracked-files"):
            count = self.deleted_files if key == "deleted-files" else self.tracked_files
            return _listing(f"file{number}" for number in range(count))
        return {
            "status": self.status_output,
            "incoming": str(self.incoming),
            "outgoing": str(self.outgoing),
            "head": self.head,
            "untracked-files": _listing(self.untracked),
            "modified-files": _listing(self.modified),
        }.get(key, "")

    def command_for(self, key):
        """Return the recorded command with this key, or None."""
        for command in self.commands:
            if command_key(command) == key:
                return command
        return None

    def env_for(self, key):
        """Return the extra environment of the recorded command with this key."""
        for command, env in zip(self.commands, self.envs):
            if command_key(command) == key:
                return env
        return None

    @property
    def keys(self):
        """The commands that were run, in order."""
        return [command_key(command) for command in self.commands]


def install_fake_git(monkeypatch, fake_git):
    """Make admin.git.repo.sync use the fake instead of real git."""
    monkeypatch.setattr(command_module, "run_git", fake_git.run)
    return fake_git
