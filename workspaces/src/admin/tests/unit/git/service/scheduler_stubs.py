"""Stand-ins shared by the tests of admin.git.service.scheduler."""

from tests.helpers.repo_factory import make_repo


class StubStopEvent:
    """
    A stop event that lets the loop run a fixed number of times.

    Args:
        allowed_rounds: How many times ``wait`` returns False before it
            returns True and ends the loop.
    """

    def __init__(self, allowed_rounds):
        self.allowed_rounds = allowed_rounds
        self.waits = []

    def wait(self, timeout=None):
        """Record the interval and report whether the loop should stop."""
        self.waits.append(timeout)
        return len(self.waits) > self.allowed_rounds

    def set(self):
        """Stop the loop at its next wait, like threading.Event.set."""
        self.allowed_rounds = 0


def named_repo(name):
    """A repository whose git_dir is its name, so not_cloned can name it."""
    return make_repo(name, git_dir=name)
