"""Helper functions for the login-relay service."""
import base64
import binascii
import hmac
import json
import logging
import time
import urllib.parse

import httpx
from authlib.common.security import generate_token
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.jose import JsonWebKey, jwt as jose_jwt
from authlib.jose.errors import JoseError
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from _config import (
    KEYCLOAK_CLIENT_ID, KEYCLOAK_CLIENT_SECRET, KEYCLOAK_INTERNAL_URL,
    KEYCLOAK_PUBLIC_URL, KEYCLOAK_REALM, OIDC_AUTH_URL_PUBLIC, OIDC_INTROSPECTION_URL_INTERNAL,
    OIDC_ISSUER, OIDC_JWKS_URL_INTERNAL, OIDC_TOKEN_URL_INTERNAL, SERVER_DNS,
    SPA_PREFIXES, TLS, WORKSPACE_PREFIXES,
)


def _public_protocol() -> str:
    return "https" if TLS else "http"


def _public_realm_url() -> str:
    return f"{KEYCLOAK_PUBLIC_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect"


def _internal_realm_url() -> str:
    return f"{KEYCLOAK_INTERNAL_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect"


def _callback_uri() -> str:
    return f"{_public_protocol()}://{SERVER_DNS}/login-relay/callback"


def _auth_url_public() -> str:
    return OIDC_AUTH_URL_PUBLIC or f"{_public_realm_url()}/auth"


def _token_url_internal() -> str:
    return OIDC_TOKEN_URL_INTERNAL or f"{_internal_realm_url()}/token"


def _jwks_url_internal() -> str:
    return OIDC_JWKS_URL_INTERNAL or f"{_internal_realm_url()}/certs"


def _introspection_url_internal() -> str:
    return OIDC_INTROSPECTION_URL_INTERNAL or f"{_internal_realm_url()}/token/introspect"


def _expected_issuer() -> str:
    return OIDC_ISSUER or f"{KEYCLOAK_PUBLIC_URL}/realms/{KEYCLOAK_REALM}"


def _decode_jwt_claims(token: str) -> dict:
    """Extract JWT payload claims without signature verification.

    Used for redirect resolution, cross-user access checks, and expiry checks.
    Never use the returned claims for access control — use JWKS-verified tokens only.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, KeyError, UnicodeDecodeError, binascii.Error):
        return {}


def _is_routable(path: str) -> bool:
    """Return True if path is covered by an Oathkeeper access rule."""
    if path in ("/", ""):
        return True
    for prefix in WORKSPACE_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    for prefix in SPA_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def _safe_return_to(return_to: str) -> str:
    """Return a safe relative path, guarding against open redirects and redirect loops."""
    parsed = urllib.parse.urlparse(return_to)
    if parsed.netloc and parsed.netloc != SERVER_DNS:
        return "/"
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    if not _is_routable(path):
        return "/"
    if parsed.query:
        path += "?" + parsed.query
    return path


def _active_username(token: str) -> str:
    """Return preferred_username from a valid non-expired token, or '' otherwise."""
    claims = _decode_jwt_claims(token)
    try:
        exp = int(claims.get("exp", 0) or 0)
    except (TypeError, ValueError):
        logging.warning("Token has non-numeric exp claim; treating as expired.")
        return ""
    if exp <= time.time():
        return ""
    return claims.get("preferred_username", "")


def _check_cross_user_redirect(token: str, destination: str) -> None:
    """Raise 403 if a valid token owner tries to access another user's workspace."""
    if not token:
        return
    username = _active_username(token)
    if not username:
        return
    for prefix in WORKSPACE_PREFIXES:
        if destination == prefix or destination.startswith(prefix + "/"):
            if prefix != f"/{username}":
                raise HTTPException(status_code=403, detail="Forbidden")


def _generate_state(destination: str) -> tuple[str, str]:
    """Return (nonce, oauth_state) for the CSRF-protected auth redirect."""
    nonce = generate_token(32)
    return_to_b64 = base64.urlsafe_b64encode(destination.encode()).decode()
    return nonce, f"{nonce}:{return_to_b64}"


def _build_auth_params(nonce: str) -> str:
    """Build URL-encoded auth request parameters."""
    return urllib.parse.urlencode({
        "client_id": KEYCLOAK_CLIENT_ID,
        "redirect_uri": _callback_uri(),
        "response_type": "code",
        "scope": "openid profile",
        "state": nonce,
        "prompt": "login",
    })


def _verify_state(oauth_state: str, state: str) -> str:
    """Verify CSRF nonce and decode return_to; return the safe destination path."""
    try:
        nonce, return_to_b64 = oauth_state.split(":", 1)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Malformed OAuth state cookie.") from exc
    if not hmac.compare_digest(state, nonce):
        raise HTTPException(status_code=400, detail="State mismatch — possible CSRF attempt.")
    try:
        padding = "=" * (-len(return_to_b64) % 4)
        return_to = base64.urlsafe_b64decode(return_to_b64 + padding).decode()
        return _safe_return_to(return_to)
    except (ValueError, binascii.Error):
        return "/"


async def _proxy_introspect(token: str) -> dict:
    """Forward a token introspection request to Keycloak and return the response.

    Login-relay acts as an OIDC gateway: Oathkeeper calls this endpoint and
    login-relay forwards to Keycloak using its own client credentials.
    """
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                _introspection_url_internal(),
                data={"token": token},
                auth=(KEYCLOAK_CLIENT_ID, KEYCLOAK_CLIENT_SECRET),
                timeout=10,
            )
        except Exception as exc:
            logging.error("Introspection proxy failed: %s", type(exc).__name__)
            raise HTTPException(
                status_code=502, detail="Introspection upstream unreachable."
            ) from exc
        if resp.status_code >= 400:
            logging.warning(
                "Introspection returned HTTP %d; treating token as inactive.",
                resp.status_code,
            )
            return {"active": False}
        body = resp.json()
        if not isinstance(body, dict) or not isinstance(body.get("active"), bool):
            logging.warning(
                "Introspection response missing boolean 'active'; treating as inactive."
            )
            return {"active": False}
        return body


async def _validate_id_token(id_token: str) -> None:
    """Validate OIDC id_token signature, issuer, audience, and expiry via JWKS."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(_jwks_url_internal(), timeout=10)
            resp.raise_for_status()
            jwks_data = resp.json()
    except Exception as exc:
        logging.error("Failed to fetch JWKS: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Failed to fetch JWKS.") from exc
    try:
        key_set = JsonWebKey.import_key_set(jwks_data)
        claims = jose_jwt.decode(
            id_token,
            key_set,
            claims_options={
                "iss": {"essential": True, "value": _expected_issuer()},
                "aud": {"essential": True, "value": KEYCLOAK_CLIENT_ID},
            },
        )
        claims.validate()
    except JoseError as exc:
        logging.warning("id_token validation failed: %s", type(exc).__name__)
        raise HTTPException(status_code=401, detail="id_token validation failed.") from exc


async def _fetch_tokens(code: str) -> tuple[str, str, int]:
    """Exchange an authorisation code; return (access_token, id_token, expires_in)."""
    async with AsyncOAuth2Client(
        client_id=KEYCLOAK_CLIENT_ID,
        client_secret=KEYCLOAK_CLIENT_SECRET,
        redirect_uri=_callback_uri(),
    ) as client:
        try:
            token = await client.fetch_token(
                url=_token_url_internal(),
                grant_type="authorization_code",
                code=code,
            )
        except Exception as exc:
            logging.error("Token exchange failed: %s", type(exc).__name__)
            raise HTTPException(status_code=502, detail="Token exchange failed.") from exc
    access_token = token.get("access_token", "")
    if not access_token:
        raise HTTPException(status_code=502, detail="No access_token in response.")
    return access_token, token.get("id_token", ""), int(token.get("expires_in", 300))


def _set_short_cookie(response: RedirectResponse, key: str, value: str) -> None:
    """Set a short-lived HttpOnly Secure cookie scoped to the login-relay paths."""
    response.set_cookie(
        key=key, value=value, max_age=300,
        httponly=True, secure=True, samesite="lax", path="/login-relay",
    )


def _set_access_token_cookie(
    response: RedirectResponse, token: str, expires_in: int
) -> None:
    """Set the dtaas_access_token cookie with the token's actual lifetime."""
    response.set_cookie(
        key="dtaas_access_token", value=token, max_age=expires_in,
        httponly=True, secure=True, samesite="lax", path="/",
    )
