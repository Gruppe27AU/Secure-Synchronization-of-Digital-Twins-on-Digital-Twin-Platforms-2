"""Configuration constants loaded from environment variables."""
import os

TLS = os.environ.get("TLS", "true").lower() == "true"
KEYCLOAK_PUBLIC_URL = os.environ.get("KEYCLOAK_PUBLIC_URL", "https://localhost/auth")
KEYCLOAK_INTERNAL_URL = os.environ.get("KEYCLOAK_INTERNAL_URL", "http://keycloak:8080/auth")
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "dtaas")
KEYCLOAK_CLIENT_ID = os.environ.get("KEYCLOAK_CLIENT_ID", "dtaas-workspace")
KEYCLOAK_CLIENT_SECRET = os.environ["KEYCLOAK_CLIENT_SECRET"]
SERVER_DNS = os.environ["SERVER_DNS"]

WORKSPACE_PREFIXES = tuple(
    f"/{u.strip('/')}"
    for u in os.environ.get("WORKSPACE_USERS", "user1,user2").split(",")
    if u.strip("/")
)
SPA_PREFIXES = (
    "/assets", "/library", "/digitaltwins", "/preview", "/create",
    "/static", "/env.js", "/favicon.ico", "/manifest.json", "/logo",
)

# Optional OIDC URL overrides for non-Keycloak providers (e.g. Dex in CI).
OIDC_AUTH_URL_PUBLIC = os.environ.get("OIDC_AUTH_URL_PUBLIC", "")
OIDC_TOKEN_URL_INTERNAL = os.environ.get("OIDC_TOKEN_URL_INTERNAL", "")
OIDC_JWKS_URL_INTERNAL = os.environ.get("OIDC_JWKS_URL_INTERNAL", "")
OIDC_INTROSPECTION_URL_INTERNAL = os.environ.get("OIDC_INTROSPECTION_URL_INTERNAL", "")
OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "")
