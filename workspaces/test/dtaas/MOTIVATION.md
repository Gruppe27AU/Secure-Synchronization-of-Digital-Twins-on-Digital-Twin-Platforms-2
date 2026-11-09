# Oathkeeper + Login-Relay Architecture — Motivation and System Structure

## Why This Exists

The TLS deployment (`compose.traefik.secure.tls.yml`) previously used
[thomseddon/traefik-forward-auth](https://github.com/thomseddon/traefik-forward-auth) as its
authentication middleware. That image is unmaintained and lacks the rule-based, per-user
authorization model needed for DTaaS.

This PR replaces it with two components:

- **Oathkeeper** — acts as a validating reverse proxy (Policy Enforcement Point). Every request
  to a workspace path is authenticated against Keycloak's token introspection endpoint before
  being forwarded.
- **login-relay** — a lightweight FastAPI service that initiates the Keycloak
  authorization-code login flow and sets the session cookie that Oathkeeper reads.

The HTTP composition (`compose.traefik.secure.yml`) is unchanged; it continues to use
traefik-forward-auth for simpler non-TLS testing.

---

## Why Oathkeeper

Issue [#59](https://github.com/INTO-CPS-Association/workspace/issues/59) evaluated two candidates —
Authelia and ORY Oathkeeper — against the DTaaS requirements:

| Requirement | Authelia | Oathkeeper |
|---|---|---|
| Works with an external IdP (Keycloak) without duplicating IAM | Limited | Yes |
| Validates JWTs issued by Keycloak | Yes | Yes |
| Supports per-route, per-claim rules | Limited | Yes |
| Clear PEP / IdP separation | No | Yes |

Oathkeeper was selected because it acts purely as an enforcement layer. Keycloak remains the
identity provider (IdP); Oathkeeper is the Policy Enforcement Point (PEP). The two have
clearly separated responsibilities and Oathkeeper introduces no session management of its own.

---

## Architecture

### Before (traefik-forward-auth)

```
Browser → Traefik → traefik-forward-auth (middleware) → workspace container
```

All authentication was handled as a Traefik middleware, with no per-user rule capability.

### After (Oathkeeper + login-relay)

```
Browser → Traefik → [forwardAuth: Oathkeeper :4456] → workspace container
                          │ no token / expired
                          ▼
                    login-relay (/login-relay)
                          │ 302 → Keycloak login page
                          ▼
                    Keycloak (authorization code exchange)
                          │ 302 → /login-relay/callback
                          ▼
                    login-relay sets dtaas_access_token cookie
                          │ 302 → original workspace path
                          ▼
                    Oathkeeper decision API → login-relay (/token/introspect) → Keycloak
                          │ token active
                          ▼
                    workspace container
```

Traefik uses Oathkeeper's decision API (port 4456) as a forwardAuth middleware.
Traefik handles all routing; Oathkeeper only decides allow/deny.

Login-relay acts as an OIDC gateway: Oathkeeper treats it as its OIDC provider
and has no direct dependency on Keycloak. Login-relay forwards introspection
requests to Keycloak and returns the response.

---

## Authentication Flow (Step by Step)

1. **User navigates** to `https://shared.example.com/user1/lab`
2. **Oathkeeper** finds the `dtaas-user1-workspace` rule, reads `cookie: dtaas_access_token` — cookie is absent or expired → 401
3. **Oathkeeper error handler** redirects to `https://shared.example.com/login-relay?return_to=/user1/lab`
4. **login-relay** (`GET /login-relay`):
   - Generates a random CSRF state nonce
   - Stores `nonce:base64(return_to)` in a short-lived `oauth_state` HttpOnly cookie
   - Redirects browser to Keycloak's `/auth` endpoint (authorization code flow, `prompt=login`)
5. **Keycloak** presents the login form; user authenticates
6. **Keycloak** redirects to `https://shared.example.com/login-relay/callback?code=…&state=<nonce>`
7. **login-relay** (`GET /login-relay/callback`):
   - Reads `oauth_state` cookie; verifies `state == nonce` (constant-time, CSRF protection)
   - Exchanges auth code for access token **server-to-server** (confidential client, uses `KEYCLOAK_CLIENT_SECRET`)
   - Sets `dtaas_access_token` cookie (`HttpOnly`, `Secure`, `SameSite=Lax`, `max_age` = `expires_in` from token response)
   - Deletes `oauth_state` cookie
   - Redirects browser to the original path (`/user1/lab`)
8. **Oathkeeper** reads `dtaas_access_token` cookie, sends it to login-relay `POST /token/introspect` → login-relay forwards to Keycloak → active → Oathkeeper forwards to `http://user1:8080`

> **Note:** This is a confidential authorization-code flow using a client secret, not PKCE.
> The DTaaS frontend SPA uses its own separate PKCE flow for the UI session (a different client,
> `dtaas-client`). These are two independent authentication paths.

---

## Per-User RBAC

Oathkeeper authenticates (validates the token) but cannot natively check claim values against URL
path segments. Per-user authorization is delegated to login-relay via Oathkeeper's `remote_json`
authorizer.

For every workspace request, Oathkeeper calls:

```
POST http://login-relay:8080/authz/workspace/<USERNAME>
Body: {"subject": {"id": "<sub>", "extra": {<introspection claims>}}}
```

login-relay's `POST /authz/workspace/{path_prefix}` reads `extra.username` (from introspection)
or `extra.preferred_username`, and returns:

- **200** if `username == path_prefix` (correct user)
- **403** if they differ (wrong user accessing another workspace)

This means user1's valid token cannot access `/user2/...`, even though both are valid Keycloak
tokens for the same realm.

---

## Redirect Loop Protection

Oathkeeper v26 redirects **all** errors — including 403 Forbidden — to the login-relay. Without
special handling, a user with a valid token attempting to access another user's workspace would
loop: Oathkeeper 403 → redirect to login → re-login → Oathkeeper 403 → ...

login-relay detects this at the `GET /login-relay` endpoint: if the incoming
`dtaas_access_token` cookie contains a non-expired JWT for a user who does not own the
`return_to` path, it raises **403 immediately** instead of initiating a new login, breaking the
loop.

---

## Companion Code Structure

```
workspaces/test/dtaas/
├── MOTIVATION.md                          ← this file
├── oathkeeper/
│   ├── oathkeeper.yml                     global Oathkeeper config (serve, authenticators, authorizers, mutators, errors)
│   └── access-rules.yml                  per-route rules (template; env vars substituted at container startup)
├── login-relay/
│   ├── Dockerfile                         pinned python:3.12.9-slim; non-root appuser
│   ├── requirements.txt                   FastAPI, uvicorn, authlib, httpx, pydantic
│   ├── requirements-dev.txt               pytest (test dependencies, not installed in image)
│   ├── _config.py                         env-var configuration constants
│   ├── _helpers.py                        OIDC helpers, state/cookie utilities, RBAC check
│   ├── main.py                           login relay service
│   │   ├── GET  /health                          liveness probe (used by compose healthcheck)
│   │   ├── GET  /login-relay                     initiate Keycloak login; set oauth_state cookie
│   │   ├── GET  /login-relay/callback            exchange code; set dtaas_access_token cookie
│   │   ├── GET  /logout                          clear cookie; redirect to Keycloak end session
│   │   ├── GET  /login-relay/logout              alias for /logout
│   │   ├── POST /authz/workspace/{user}          remote_json RBAC endpoint (called by Oathkeeper)
│   │   └── POST /token/introspect               OIDC gateway — proxies introspection to Keycloak (Oathkeeper calls this)
│   └── tests/
│       ├── conftest.py                   sets required env vars before import
│       └── test_main.py                  unit + integration tests for all endpoints and helpers
└── compose.traefik.secure.tls.yml        production TLS deployment
```

### Running Tests

```bash
cd workspaces/test/dtaas/login-relay
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -v
```

---

## Oathkeeper Access Rules

There are five rules in `access-rules.yml`. Oathkeeper v26 requires exactly one rule to match per
request — overlapping patterns cause a 500 "multiple rules matched" error, so each rule's URL
pattern is non-overlapping by design.

| Rule ID | Path pattern | Auth | Authz |
|---|---|---|---|
| `dtaas-spa-gateway` | `/`, `/library/…`, `/digitaltwins/…`, `/preview/…`, `/create/…`, `/static/…`, `/env.js`, `/favicon.ico`, `/manifest.json`, `/logo*` | `oauth2_introspection` (cookie) | `allow` |
| `dtaas-user1-workspace` | `/${USERNAME1}(/…)?` | `oauth2_introspection` (header or cookie) | `remote_json` → `/authz/workspace/${USERNAME1}` |
| `dtaas-user2-workspace` | `/${USERNAME2}(/…)?` | `oauth2_introspection` (header or cookie) | `remote_json` → `/authz/workspace/${USERNAME2}` |
| *(user3, etc.)* | `/${USERNAMEn}(/…)?` | `oauth2_introspection` (header or cookie) | `remote_json` → `/authz/workspace/${USERNAMEn}` |
| `dtaas-login-relay-public` | `/login-relay*`, `/logout` | `noop` | `allow` |
| `dtaas-public-health` | `/health` | `noop` | `allow` |

Workspace rules accept the token from either:
- `Authorization: Bearer <token>` header (service-to-service / API clients)
- `cookie: dtaas_access_token` (browser sessions set by login-relay)

---

## Keycloak Client Requirements

The `dtaas-workspace` client must be configured as a **confidential client** (client authentication
ON). The login-relay exchanges auth codes server-to-server using the client secret; it is never
sent to the browser.

| Setting | Value |
|---|---|
| Client authentication | ON |
| Standard flow | ON |
| Valid redirect URIs | `https://<SERVER_DNS>/login-relay/callback` |
| Valid post-logout redirect URIs | `https://<SERVER_DNS>/*` |
| Web origins | `https://<SERVER_DNS>` |

The `dtaas-client` (used by the DTaaS SPA for its own PKCE flow) remains a **public client** and
is configured separately.

See [`KEYCLOAK_SETUP.md`](KEYCLOAK_SETUP.md) for step-by-step Keycloak configuration.

---

## Required Environment Variables

| Variable | Used by | Purpose |
|---|---|---|
| `SERVER_DNS` | login-relay, Traefik | Public domain name (e.g. `shared.example.com`) |
| `KEYCLOAK_CLIENT_ID` | login-relay | Confidential client ID (`dtaas-workspace`) |
| `KEYCLOAK_CLIENT_SECRET` | login-relay | Client secret for server-side token exchange |
| `KEYCLOAK_PUBLIC_URL` | login-relay | Browser-facing Keycloak URL (for login redirect) |
| `KEYCLOAK_INTERNAL_URL` | login-relay | Container-internal Keycloak URL (for token exchange) |
| `KEYCLOAK_REALM` | login-relay, Oathkeeper | Keycloak realm name (`dtaas`) |
| `USERNAME1`, `USERNAME2` | Oathkeeper | Workspace usernames (substituted into access rules at startup) |
| `WORKSPACE_USERS` | login-relay | Comma-separated list of workspace usernames for redirect-loop detection (e.g. `user1,user2`) |
| `LOGIN_RELAY_URL` | Oathkeeper | URL of the login-relay redirect target |

---

## Known Limitations

### 1. Session lifetime — WebSocket connections drop on re-authentication

`dtaas_access_token` `max_age` is set to the `expires_in` value returned by Keycloak's token
endpoint, so the cookie lifetime automatically matches the configured access token lifespan.
When the `dtaas_access_token` cookie is absent (deleted or never set) or has expired, the next
browser request is rejected by Oathkeeper and redirected to login-relay. login-relay uses
`prompt=login`, which instructs Keycloak to always show the login form — even if a Keycloak
SSO session is still active. This ensures that deleting the cookie always forces a full
re-login. If re-auth is triggered inside an iframe (e.g. on the Digital Twins page), the
Keycloak login form will appear inside the iframe.

Active WebSocket connections (VS Code, Jupyter terminals) will drop when the redirect happens,
because the tab navigates away.

**To extend the session lifetime**, increase **Access Token Lifespan** in Keycloak admin →
Realm settings → Tokens (e.g. `1h`). The cookie lifetime will follow automatically.

---

### 2. Scalability — adding users requires changes in four places

The current design has one Oathkeeper access rule and one Docker Compose service per user.
Adding a third user (`user3`) requires editing:

| File | Change needed |
|---|---|
| `compose.traefik.secure.tls.yml` | New `user3:` service block + Traefik labels |
| `oathkeeper/access-rules.yml` | New `dtaas-user3-workspace` rule |
| Oathkeeper `environment:` in compose | Add `USERNAME3=...` + `sed` substitution line in entrypoint |
| Login-relay `environment:` in compose | Add `user3` to `WORKSPACE_USERS` |

**Practical limit:** ~5–10 users before the config becomes unmanageable.

**Path forward for larger deployments:** replace the per-user Oathkeeper rules with a single
wildcard rule that matches `/<any-username>(/.*)?` and delegates all RBAC to the
`/authz/workspace/{path_prefix}` endpoint. Oathkeeper would pass the path prefix as the
`path_prefix` parameter; login-relay already has the logic to accept or reject it. This requires
upgrading Oathkeeper's rule matching to use a capture group and passing the captured segment to
the `remote_json` URL — something Oathkeeper's Go template syntax supports but is non-trivial
to test. Dynamic workspace registration (a user registry) would also be needed so the
login-relay knows which prefixes are valid without `WORKSPACE_USERS` being a static list.

---

### 3. Oathkeeper strips trailing slashes — workspace nginx must compensate

Oathkeeper forwards requests to upstream containers without preserving trailing slashes on tool
paths. Without compensation, `/tools/vscode` (no trailing slash) reaches the workspace nginx,
which redirects to the base URL, which Oathkeeper intercepts again — producing an infinite 302
loop.

`workspaces/src/startup/nginx.conf` handles this by matching `/tools/vscode` and `/tools/vnc`
**before** the generic trailing-slash redirect, and using an `if`-block to re-add the slash to
the upstream path when it is absent:

```nginx
location ~* "^{WORKSPACE_BASE_URL_DECODED}/tools/vscode(?<remaining_part>.*)" {
    if ($remaining_part !~ ^/(.*)$) {
        set $remaining_part /$remaining_part;
    }
    proxy_pass http://vscode$remaining_part$is_args$args;
}
```

The same pattern applies to the `/tools/vnc` location. This must be kept in sync whenever the
nginx routing is changed.

---

### 4. `/authz/workspace` endpoint is unauthenticated

`POST /authz/workspace/{path_prefix}` is called server-to-server by Oathkeeper and has no
authentication of its own. Any service on the same Docker network can call it and receive a 200
or 403. This is acceptable for the current threat model (the Docker network is private and the
endpoint is not exposed via Traefik), but should be noted for deployments where the Docker network
is shared across tenants.

---

## How To Run

```bash
# 1. Copy and fill in environment variables
cp workspaces/test/dtaas/config/.env.example workspaces/test/dtaas/config/.env

# 2. Create user workspace directories
cp -R workspaces/test/dtaas/files/user1 workspaces/test/dtaas/files/<USERNAME1>
cp -R workspaces/test/dtaas/files/user1 workspaces/test/dtaas/files/<USERNAME2>
sudo chown -R 1000:100 workspaces/test/dtaas/files

# 3. Place TLS certificates in workspaces/test/dtaas/certs/

# 4. Start all services
docker compose -f workspaces/test/dtaas/compose.traefik.secure.tls.yml \
  --env-file workspaces/test/dtaas/config/.env up -d

# 5. Configure Keycloak (see KEYCLOAK_SETUP.md), then restart auth services
docker compose -f workspaces/test/dtaas/compose.traefik.secure.tls.yml \
  --env-file workspaces/test/dtaas/config/.env \
  up -d --force-recreate oathkeeper login-relay
```

---

## Verification Checklist

- [ ] Navigating to `https://<SERVER_DNS>/<USERNAME1>` redirects to Keycloak login
- [ ] After login, `dtaas_access_token` cookie is set and workspace is accessible
- [ ] user1's token cannot access `/user2/…` (expected: 403)
- [ ] Navigating to `/logout` clears the cookie and redirects to Keycloak end session
- [ ] After token expiry (5 min), the next request redirects back to login
- [ ] SPA paths (`/library`, `/digitaltwins`, etc.) are accessible after login
