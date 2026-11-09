"""Tests for login-relay security logic and API endpoints."""
# conftest.py force-sets env vars before this module is imported, so main.py
# picks up the test values when it runs its module-level initialisation.
import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

from authlib.jose import RSAKey, jwt as authlib_jwt
from fastapi import HTTPException
from fastapi.testclient import TestClient

from main import app
from _helpers import (
    _active_username, _decode_jwt_claims, _is_routable, _proxy_introspect,
    _safe_return_to, _validate_id_token,
)

client = TestClient(app, follow_redirects=False)


def _make_jwt(claims: dict) -> str:
    """Build a fake (unsigned) JWT for testing claim extraction."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.fakesig"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestDecodeJwtClaims:
    """Tests for the _decode_jwt_claims helper."""

    def test_valid_token_returns_claims(self):
        """Valid JWT yields the embedded claims dict."""
        claims = {"sub": "abc", "preferred_username": "user1", "exp": 9999999999}
        assert _decode_jwt_claims(_make_jwt(claims)) == claims

    def test_malformed_returns_empty(self):
        """Token with invalid base64 payload returns empty dict."""
        assert _decode_jwt_claims("not.a.jwt") == {}

    def test_too_few_parts_returns_empty(self):
        """Token with fewer than three dot-separated parts returns empty dict."""
        assert _decode_jwt_claims("onlyone") == {}

    def test_invalid_base64_returns_empty(self):
        """Token whose payload segment is not valid base64 returns empty dict."""
        assert _decode_jwt_claims("a.!!!.c") == {}


class TestIsRoutable:
    """Tests for the _is_routable path-validation helper."""

    def test_root(self):
        """Root path is always routable."""
        assert _is_routable("/") is True

    def test_empty(self):
        """Empty string is treated as root and is routable."""
        assert _is_routable("") is True

    def test_workspace_exact(self):
        """Exact workspace prefix without trailing slash is routable."""
        assert _is_routable("/user1") is True

    def test_workspace_subpath(self):
        """Sub-path beneath a known workspace prefix is routable."""
        assert _is_routable("/user1/tools/vscode") is True

    def test_spa_library(self):
        """SPA library path is routable."""
        assert _is_routable("/library") is True

    def test_spa_subpath(self):
        """Nested SPA path is routable."""
        assert _is_routable("/digitaltwins/mydt") is True

    def test_unknown_path_returns_false(self):
        """Path under an unknown workspace prefix is not routable."""
        assert _is_routable("/user3") is False

    def test_partial_prefix_not_matched(self):
        """Partial prefix match must not be accepted as routable."""
        # /user1extra must NOT match the /user1 prefix
        assert _is_routable("/user1extra") is False


class TestSafeReturnTo:
    """Tests for the _safe_return_to URL-sanitisation helper."""

    def test_root_passthrough(self):
        """Root path passes through unchanged."""
        assert _safe_return_to("/") == "/"

    def test_empty_becomes_root(self):
        """Empty string is normalised to root."""
        assert _safe_return_to("") == "/"

    def test_known_workspace_preserved(self):
        """Known workspace path is preserved as-is."""
        assert _safe_return_to("/user1/lab") == "/user1/lab"

    def test_unknown_workspace_normalised_to_root(self):
        """Path under unknown workspace is normalised to root."""
        assert _safe_return_to("/user3/lab") == "/"

    def test_spa_path_preserved(self):
        """SPA path is preserved as-is."""
        assert _safe_return_to("/library") == "/library"

    def test_query_string_preserved(self):
        """Query string is kept when the path is valid."""
        assert _safe_return_to("/user1/lab?foo=bar") == "/user1/lab?foo=bar"

    def test_cross_origin_blocked(self):
        """Absolute URL to a different origin is blocked and returns root."""
        assert _safe_return_to("https://evil.com/path") == "/"

    def test_same_origin_absolute_extracts_path(self):
        """Absolute URL on the same origin is reduced to its path component."""
        # Oathkeeper passes http:// URLs; SERVER_DNS check uses netloc only
        assert _safe_return_to("http://test.example.com/user1/lab") == "/user1/lab"

    def test_relative_path_gets_leading_slash(self):
        """Relative path without leading slash gets one prepended."""
        assert _safe_return_to("user1/lab") == "/user1/lab"


# ---------------------------------------------------------------------------
# /authz/workspace/<user>
# ---------------------------------------------------------------------------

class TestAuthzWorkspace:
    """Tests for the /authz/workspace/<user> authorisation endpoint."""

    def test_matching_username_returns_200(self):
        """Request whose username matches the path segment returns 200."""
        resp = client.post(
            "/authz/workspace/user1",
            json={"subject": {"extra": {"username": "user1"}}},
        )
        assert resp.status_code == 200

    def test_preferred_username_fallback_returns_200(self):
        """preferred_username claim is accepted as a fallback identifier."""
        resp = client.post(
            "/authz/workspace/user1",
            json={"subject": {"extra": {"preferred_username": "user1"}}},
        )
        assert resp.status_code == 200

    def test_wrong_user_returns_403(self):
        """Request whose username does not match the path segment returns 403."""
        resp = client.post(
            "/authz/workspace/user2",
            json={"subject": {"extra": {"username": "user1"}}},
        )
        assert resp.status_code == 403

    def test_empty_username_returns_403(self):
        """Request with no username claim returns 403."""
        resp = client.post(
            "/authz/workspace/user1",
            json={"subject": {"extra": {}}},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealth:  # pylint: disable=too-few-public-methods
    """Tests for the /health endpoint."""

    def test_returns_200(self):
        """Health endpoint returns HTTP 200."""
        assert client.get("/health").status_code == 200


# ---------------------------------------------------------------------------
# POST /token/introspect
# ---------------------------------------------------------------------------

class TestTokenIntrospect:
    """Tests for the POST /token/introspect OIDC gateway endpoint."""

    def test_missing_token_returns_inactive(self):
        """Request with no token returns {active: false} without calling Keycloak."""
        resp = client.post("/token/introspect", data={})
        assert resp.status_code == 200
        assert resp.json() == {"active": False}

    def test_valid_token_proxied_to_keycloak(self):
        """Valid token is forwarded to Keycloak and the response is returned."""
        keycloak_response = {"active": True, "username": "user1", "preferred_username": "user1"}
        with patch("main._proxy_introspect", new=AsyncMock(return_value=keycloak_response)):
            resp = client.post("/token/introspect", data={"token": "valid-jwt"})
        assert resp.status_code == 200
        assert resp.json() == keycloak_response

    def test_keycloak_unreachable_returns_502(self):
        """Upstream failure raises 502."""
        with patch(
            "main._proxy_introspect",
            new=AsyncMock(side_effect=HTTPException(
                status_code=502, detail="Introspection upstream unreachable."
            )),
        ):
            resp = client.post("/token/introspect", data={"token": "some-token"})
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# /logout
# ---------------------------------------------------------------------------

class TestLogout:
    """Tests for the /logout endpoint."""

    def test_redirects_to_keycloak(self):
        """Logout redirects to the Keycloak logout URL."""
        resp = client.get("/logout")
        assert resp.status_code == 302
        assert "logout" in resp.headers["location"]

    def test_clears_access_token_cookie(self):
        """Logout response expires the dtaas_access_token cookie."""
        resp = client.get("/logout")
        set_cookie = resp.headers.get("set-cookie", "")
        assert "dtaas_access_token" in set_cookie
        assert "max-age=0" in set_cookie.lower()


# ---------------------------------------------------------------------------
# GET /login-relay  (initiate login)
# ---------------------------------------------------------------------------

class TestLogin:
    """Tests for the GET /login-relay login-initiation endpoint."""

    def test_redirects_to_keycloak_auth(self):
        """Login endpoint redirects to the Keycloak authorisation URL."""
        resp = client.get("/login-relay")
        assert resp.status_code == 302
        assert "auth/realms" in resp.headers["location"]

    def test_sets_oauth_state_cookie(self):
        """Login endpoint sets the oauth_state cookie for CSRF protection."""
        resp = client.get("/login-relay")
        assert "oauth_state" in resp.headers.get("set-cookie", "")

    def test_cross_user_redirect_returns_403(self):
        """A valid user1 token redirected to /user2 must get 403, not a new login."""
        future = int(time.time()) + 3600
        token = _make_jwt({"preferred_username": "user1", "exp": future})
        c = TestClient(app, follow_redirects=False, cookies={"dtaas_access_token": token})
        assert c.get("/login-relay?return_to=/user2/lab").status_code == 403

    def test_expired_token_does_not_block_login(self):
        """An expired token for user1 must not trigger 403 — allow re-login."""
        past = int(time.time()) - 1
        token = _make_jwt({"preferred_username": "user1", "exp": past})
        c = TestClient(app, follow_redirects=False, cookies={"dtaas_access_token": token})
        # Expired token: cross-user check is skipped, normal login redirect fires
        assert c.get("/login-relay?return_to=/user2/lab").status_code == 302


# ---------------------------------------------------------------------------
# GET /login-relay/callback
# ---------------------------------------------------------------------------

class TestCallback:
    """Tests for the GET /login-relay/callback OAuth2 callback endpoint."""

    def test_missing_state_cookie_returns_400(self):
        """Callback without an oauth_state cookie returns 400."""
        resp = client.get("/login-relay/callback?code=abc&state=xyz")
        assert resp.status_code == 400

    def test_missing_code_returns_400(self):
        """Callback without an authorisation code returns 400."""
        nonce = "testnonce"
        return_to_b64 = base64.urlsafe_b64encode(b"/user1/lab").decode()
        c = TestClient(
            app, follow_redirects=False, cookies={"oauth_state": f"{nonce}:{return_to_b64}"}
        )
        assert c.get(f"/login-relay/callback?state={nonce}").status_code == 400

    def test_state_mismatch_returns_400(self):
        """Callback with a mismatched state parameter returns 400."""
        return_to_b64 = base64.urlsafe_b64encode(b"/user1/lab").decode()
        c = TestClient(
            app, follow_redirects=False, cookies={"oauth_state": f"correct:{return_to_b64}"}
        )
        assert c.get("/login-relay/callback?code=abc&state=wrong").status_code == 400

    def test_success_sets_cookie_and_redirects(self):
        """Successful callback sets the access-token cookie and redirects."""
        nonce = "testnonce123"
        return_to_b64 = base64.urlsafe_b64encode(b"/user1/lab").decode()
        c = TestClient(
            app, follow_redirects=False, cookies={"oauth_state": f"{nonce}:{return_to_b64}"}
        )

        with patch("main._fetch_tokens", new=AsyncMock(return_value=("fake-token", "", 3600))):
            resp = c.get(f"/login-relay/callback?code=authcode&state={nonce}")

        assert resp.status_code == 302
        assert resp.headers["location"] == "/user1/lab"
        assert "dtaas_access_token" in resp.headers.get("set-cookie", "")

    def test_unknown_return_to_falls_back_to_root(self):
        """Callback with an unknown return_to path redirects to root."""
        nonce = "testnonce456"
        return_to_b64 = base64.urlsafe_b64encode(b"/user3/lab").decode()
        c = TestClient(
            app, follow_redirects=False, cookies={"oauth_state": f"{nonce}:{return_to_b64}"}
        )

        with patch("main._fetch_tokens", new=AsyncMock(return_value=("fake-token", "", 3600))):
            resp = c.get(f"/login-relay/callback?code=authcode&state={nonce}")

        assert resp.status_code == 302
        assert resp.headers["location"] == "/"


# ---------------------------------------------------------------------------
# _validate_id_token (JWKS signature verification)
# ---------------------------------------------------------------------------

# RSA key pair generated once for the whole test session.
_TEST_RSA_KEY = RSAKey.generate_key(2048, is_private=True)
_TEST_JWKS = {"keys": [_TEST_RSA_KEY.as_dict(is_private=False)]}
# Expected issuer matches _helpers._expected_issuer() with test env vars:
#   KEYCLOAK_PUBLIC_URL defaults to "https://localhost/auth", KEYCLOAK_REALM="dtaas"
_TEST_ISSUER = "https://localhost/auth/realms/dtaas"
_TEST_CLIENT_ID = "dtaas-workspace"


def _signed_id_token(claims: dict) -> str:
    """Return a signed RS256 JWT for use in _validate_id_token tests."""
    kid = _TEST_RSA_KEY.as_dict(is_private=False)["kid"]
    return authlib_jwt.encode({"alg": "RS256", "kid": kid}, claims, _TEST_RSA_KEY).decode()


def _mock_jwks_client(jwks: dict) -> AsyncMock:
    """Return an async context manager mock whose .get() returns jwks."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = jwks
    mock_resp.raise_for_status = MagicMock()
    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=mock_resp)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)
    return mock_http


class TestValidateIdToken:
    """Tests for _validate_id_token JWKS signature and claims verification."""

    async def test_valid_token_does_not_raise(self):
        """A properly signed token with correct iss/aud/exp passes validation."""
        token = _signed_id_token({
            "iss": _TEST_ISSUER,
            "aud": _TEST_CLIENT_ID,
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()),
            "sub": "user1",
        })
        with patch("_helpers.httpx.AsyncClient", return_value=_mock_jwks_client(_TEST_JWKS)):
            await _validate_id_token(token)  # must not raise

    async def test_tampered_signature_raises_401(self):
        """A token whose signature has been modified is rejected with 401."""
        token = _signed_id_token({
            "iss": _TEST_ISSUER,
            "aud": _TEST_CLIENT_ID,
            "exp": int(time.time()) + 3600,
        })
        header, payload, _ = token.split(".")
        tampered = f"{header}.{payload}.invalidsignature"
        with patch("_helpers.httpx.AsyncClient", return_value=_mock_jwks_client(_TEST_JWKS)):
            try:
                await _validate_id_token(tampered)
                assert False, "Expected HTTPException"
            except HTTPException as exc:
                assert exc.status_code == 401

    async def test_jwks_fetch_failure_raises_502(self):
        """A network error fetching JWKS raises 502."""
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("Connection refused"))
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        with patch("_helpers.httpx.AsyncClient", return_value=mock_http):
            try:
                await _validate_id_token("any.token.here")
                assert False, "Expected HTTPException"
            except HTTPException as exc:
                assert exc.status_code == 502


# ---------------------------------------------------------------------------
# _fetch_tokens failure
# ---------------------------------------------------------------------------

class TestFetchTokensFailure:
    """Tests for token-exchange error handling in the callback endpoint."""

    def test_token_exchange_failure_returns_502(self):
        """A failed token exchange propagates as HTTP 502 to the caller."""
        nonce = "failnonce123"
        return_to_b64 = base64.urlsafe_b64encode(b"/user1/lab").decode()
        c = TestClient(
            app, follow_redirects=False,
            cookies={"oauth_state": f"{nonce}:{return_to_b64}"},
        )
        with patch(
            "main._fetch_tokens",
            new=AsyncMock(side_effect=HTTPException(
                status_code=502, detail="Token exchange failed."
            )),
        ):
            resp = c.get(f"/login-relay/callback?code=authcode&state={nonce}")
        assert resp.status_code == 502

    def test_id_token_validation_failure_returns_401(self):
        """A failed id_token validation propagates as HTTP 401 to the caller."""
        nonce = "failnonce456"
        return_to_b64 = base64.urlsafe_b64encode(b"/user1/lab").decode()
        c = TestClient(
            app, follow_redirects=False,
            cookies={"oauth_state": f"{nonce}:{return_to_b64}"},
        )
        with patch("main._fetch_tokens", new=AsyncMock(return_value=("token", "id-token", 3600))):
            with patch(
                "main._validate_id_token",
                new=AsyncMock(side_effect=HTTPException(
                    status_code=401, detail="id_token validation failed."
                )),
            ):
                resp = c.get(f"/login-relay/callback?code=authcode&state={nonce}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# _proxy_introspect — unit tests for new branches
# ---------------------------------------------------------------------------

def _mock_introspect_http(status_code: int, body: object) -> AsyncMock:
    """Build an AsyncMock httpx.AsyncClient whose POST returns status_code + body."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = body
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)
    return mock_http


class TestProxyIntrospect:
    """Unit tests for the _proxy_introspect helper (new branches)."""

    async def test_http_400_returns_inactive(self):
        """HTTP 400 from the introspection endpoint returns {active: False}."""
        with patch("_helpers.httpx.AsyncClient", return_value=_mock_introspect_http(400, {})):
            result = await _proxy_introspect("some-token")
        assert result == {"active": False}

    async def test_http_500_returns_inactive(self):
        """HTTP 500 from the introspection endpoint returns {active: False}."""
        with patch("_helpers.httpx.AsyncClient", return_value=_mock_introspect_http(500, {})):
            result = await _proxy_introspect("some-token")
        assert result == {"active": False}

    async def test_missing_active_field_returns_inactive(self):
        """A 200 response without an 'active' key is treated as inactive."""
        body = {"username": "user1"}
        with patch("_helpers.httpx.AsyncClient", return_value=_mock_introspect_http(200, body)):
            result = await _proxy_introspect("some-token")
        assert result == {"active": False}

    async def test_non_bool_active_returns_inactive(self):
        """A 200 response where 'active' is a string (not bool) is treated as inactive."""
        body = {"active": "true"}
        with patch("_helpers.httpx.AsyncClient", return_value=_mock_introspect_http(200, body)):
            result = await _proxy_introspect("some-token")
        assert result == {"active": False}

    async def test_valid_active_true_returned(self):
        """A valid 200 response with boolean active=True is returned as-is."""
        body = {"active": True, "username": "user1"}
        with patch("_helpers.httpx.AsyncClient", return_value=_mock_introspect_http(200, body)):
            result = await _proxy_introspect("some-token")
        assert result == body


# ---------------------------------------------------------------------------
# _active_username — non-numeric exp guard
# ---------------------------------------------------------------------------

class TestActiveUsername:
    """Tests for the _active_username exp-guard fix."""

    def test_non_numeric_exp_returns_empty(self):
        """Token with a non-numeric exp claim is treated as expired."""
        token = _make_jwt({"sub": "user1", "preferred_username": "user1", "exp": "not-a-number"})
        assert _active_username(token) == ""

    def test_valid_future_exp_returns_username(self):
        """Token with a future numeric exp returns the preferred_username."""
        token = _make_jwt({
            "sub": "user1", "preferred_username": "user1",
            "exp": int(time.time()) + 3600,
        })
        assert _active_username(token) == "user1"

    def test_expired_token_returns_empty(self):
        """Token whose exp is in the past returns empty string."""
        token = _make_jwt({"sub": "user1", "preferred_username": "user1", "exp": 1})
        assert _active_username(token) == ""
