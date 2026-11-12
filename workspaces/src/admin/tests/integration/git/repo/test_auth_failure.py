"""
Integration test for a rejected token, with real git and a real HTTP server.

A local server answers every request with 401, as a forge does for an
expired token. With prompts switched off git does not say "Authentication
failed" then, but that it could not ask for a username, and that has to be
recognised as a rejected token too.
"""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from admin.git.run import auth
from admin.git.repo.clone import CloneError, clone_asset
from admin.git.run.errors import SyncErrorKind
from tests.helpers.repo_factory import make_repo


class _Unauthorized(BaseHTTPRequestHandler):
    """Answers every request with 401 and a Basic challenge."""

    def do_GET(self):  # pylint: disable=invalid-name
        """Refuse the request."""
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="x"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args):  # pylint: disable=arguments-differ
        """Keep the test output quiet."""


@pytest.fixture(name="server_url")
def fixture_server_url():
    """The base URL of a server that rejects every request."""
    server = HTTPServer(("127.0.0.1", 0), _Unauthorized)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_an_expired_token_is_reported_as_an_auth_failure(
    server_url, tmp_path, monkeypatch
):
    """Test the clone of a 401 remote fails as auth_failed, with the hint."""
    global_config = tmp_path / "gitconfig"
    global_config.touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # The test server speaks plain http, which the https-only options refuse.
    monkeypatch.setattr(auth, "PROTOCOL_OPTIONS", [])
    repo = make_repo(
        "private",
        repo_url=f"{server_url}/me/priv.git",
        token="expired-token",
        git_dir=tmp_path / "git",
        work_tree=tmp_path / "tree",
    )

    with pytest.raises(CloneError) as excinfo:
        clone_asset(repo)

    assert excinfo.value.kind is SyncErrorKind.AUTH_FAILED
    assert "invalid or expired" in str(excinfo.value)
    assert "expired-token" not in str(excinfo.value)
