"""
Integration tests for the token, with real git and a real HTTP server.

A local server answers every request with 401, as a forge does for an
expired token. With prompts switched off git does not say "Authentication
failed" then, but that it could not ask for a username, and that has to be
recognised as a rejected token too.
"""

import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from admin.git.run import auth
from admin.git.repo.clone import clone_asset
from admin.git.run.errors import SyncError, SyncErrorKind
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

    with pytest.raises(SyncError) as excinfo:
        clone_asset(repo)

    assert excinfo.value.kind is SyncErrorKind.AUTH_FAILED
    assert "invalid or expired" in str(excinfo.value)
    assert "expired-token" not in str(excinfo.value)


class _Recording(_Unauthorized):
    """Refuses every request, noting which paths carried a token."""

    seen: list[tuple[str, bool]] = []

    def do_GET(self):  # pylint: disable=invalid-name
        """Note the path and whether it was authenticated, then refuse."""
        self.seen.append((self.path, "Authorization" in self.headers))
        super().do_GET()


def test_the_token_goes_to_the_repository_only(tmp_path, monkeypatch):
    """Test another repository on the same host never receives the token."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    server = HTTPServer(("127.0.0.1", 0), _Recording)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **auth.auth_environment(
        make_repo(repo_url=f"{base}/me/priv.git", token="secret-token")
    )}
    try:
        for path in ("/me/priv.git", "/someone/else.git"):
            subprocess.run(["git", "-c", "credential.helper=", "ls-remote", base + path],
                           env=env, capture_output=True, check=False, timeout=30)
    finally:
        server.shutdown()
        server.server_close()

    sent = {path.split("/info/")[0]: has_token for path, has_token in _Recording.seen}
    assert sent == {"/me/priv.git": True, "/someone/else.git": False}
