# Workspace with Traefik, Keycloak, and TLS

This guide explains how to deploy the workspace container with Traefik reverse
proxy, OIDC/OAuth2 authentication using Keycloak, and TLS/HTTPS support for
secure multi-user deployments.

## ❓ Prerequisites

✅ Docker Engine v27 or later  
✅ Docker Compose v2.x  
✅ Ports 80 and 443 available on your host machine  
✅ At least 2GB RAM (includes Keycloak, Oathkeeper, and workspaces)  
✅ Valid TLS certificates (production) or self-signed certs (testing)  
✅ A domain name pointing to your server  

## 🗒️ Overview

The `compose.traefik.secure.tls.yml` file provides a production-ready setup with:

- **Traefik** — reverse proxy with TLS termination (port 80 redirects to HTTPS,
  port 443 serves HTTPS)
- **Keycloak** — embedded identity provider (OIDC)
- **Oathkeeper** — JWT proxy; validates tokens and forwards authenticated
  requests to workspaces
- **login-relay** — lightweight login relay service; initiates the Keycloak
  authorization code flow and sets the `dtaas_access_token` cookie after
  successful authentication
- **client** — DTaaS web interface
- **user1** workspace using the workspace image
- **user2** workspace using the workspace image
- **Two Docker networks**: `dtaas-frontend` and `dtaas-users`

## ⚙️ Initial Configuration

Please follow the steps in [`CONFIGURATION.md`](CONFIGURATION.md) for the
`compose.traefik.secure.tls.yml` composition before building the workspace and
running the setup.

## :rocket: Start Services

To start all services with TLS:

```bash
docker compose -f workspaces/test/dtaas/compose.traefik.secure.tls.yml  --env-file workspaces/test/dtaas/config/.env up -d
```

This will:

1. Start the Traefik reverse proxy with TLS (port 80 → 443 redirect,
   port 443 HTTPS)
2. Start Keycloak identity provider at `/auth`
3. Start Oathkeeper JWT proxy (validates tokens, routes workspace traffic)
4. Start the login-relay service at `/login-relay`
5. Start the DTaaS web client interface
6. Start workspace instances for both users

**Note**: First-time startup may take a few minutes for Keycloak to initialize.

## :gear: Configure Keycloak

After starting the services, Keycloak must be configured.
To do this, follow the steps in [KEYCLOAK_SETUP.md](./KEYCLOAK_SETUP.md).

## 🔒 Authentication Flow

1. User navigates to a workspace or SPA URL over HTTPS
2. Traefik calls Oathkeeper's decision API (port 4456) via forwardAuth middleware
3. Oathkeeper checks for a valid `dtaas_access_token` cookie (Keycloak JWT)
4. If no valid token: Oathkeeper redirects to
   `/login-relay?return_to=<original-url>`
5. login-relay generates a state nonce and redirects the browser to
   Keycloak login
6. User authenticates with Keycloak
7. Keycloak redirects to `/login-relay/callback` with an auth code
8. login-relay exchanges the code for a Keycloak JWT using the client secret
   (server-to-server)
9. login-relay sets the `dtaas_access_token` HttpOnly Secure cookie and
   redirects to the original URL
10. Oathkeeper introspects the token via login-relay, injects identity headers,
    and approves the request; Traefik then proxies it to the workspace

The `dtaas_access_token` cookie expires after 5 minutes (matching Keycloak's
default access token lifetime). When the cookie is absent (deleted or never set)
or has expired, Oathkeeper rejects the request and redirects to login-relay.
login-relay uses `prompt=login`, which instructs Keycloak to always show the
login form — even if a Keycloak SSO session is still active. This ensures that
deleting the cookie always forces a full re-login. If re-auth is triggered
inside an iframe (e.g. on the Digital Twins page), the Keycloak login form will
appear inside the iframe.

## :technologist: Accessing Workspaces

Once all services are running and Keycloak is configured, access them at
`https://<DOMAIN_NAME>`.

### Initial Access

1. Navigate to `https://<DOMAIN_NAME>` in your browser
2. You are redirected to Keycloak login
3. Log in with a user you created in Keycloak
4. You are redirected back to the DTaaS web interface

### Keycloak Admin Console

- **URL**: `https://<DOMAIN_NAME>/auth`
- Login with `KEYCLOAK_ADMIN` credentials from `.env`

### DTaaS Web Client

- **URL**: `https://<DOMAIN_NAME>/`

### User1 Workspace

All endpoints require authentication:

- **VNC Desktop**: `https://<DOMAIN_NAME>/user1/tools/vnc`
- **VS Code**: `https://<DOMAIN_NAME>/user1/tools/vscode`
- **Jupyter Notebook**: `https://<DOMAIN_NAME>/user1`
- **Jupyter Lab**: `https://<DOMAIN_NAME>/user1/lab`

#### Service Discovery

The workspace provides a `/services` endpoint that returns a JSON list of
available services. This is intended for future dynamic service discovery
for frontend applications.

**Example**: Get service list for user1

```bash
curl https://<DOMAIN_NAME>/user1/services
```

**Response**:

```json
{
  "desktop": {
    "name": "Desktop",
    "description": "Virtual Desktop Environment",
    "endpoint": "tools/vnc"
  },
  "vscode": {
    "name": "VS Code",
    "description": "VS Code IDE",
    "endpoint": "tools/vscode"
  },
  "notebook": {
    "name": "Jupyter Notebook",
    "description": "Jupyter Notebook",
    "endpoint": ""
  },
  "lab": {
    "name": "Jupyter Lab",
    "description": "Jupyter Lab IDE",
    "endpoint": "lab"
  }
}
```

The endpoint values are dynamically populated with the user's username from the
`MAIN_USER` environment variable. This variable corresponds to `USERNAME1` of
`.env` file.

### User2 Workspace

- **VNC Desktop**: `https://<DOMAIN_NAME>/user2/tools/vnc`
- **VS Code**: `https://<DOMAIN_NAME>/user2/tools/vscode`
- **Jupyter Notebook**: `https://<DOMAIN_NAME>/user2`
- **Jupyter Lab**: `https://<DOMAIN_NAME>/user2/lab`

### Logging Out
When logged in as one of the users:

- **URL**: `https://<DOMAIN_NAME>/logout`

## 🛑 Stopping Services

To stop all services:

```bash
docker compose -f workspaces/test/dtaas/compose.traefik.secure.tls.yml  --env-file workspaces/test/dtaas/config/.env down
```

To stop and remove volumes:

```bash
docker compose -f workspaces/test/dtaas/compose.traefik.secure.tls.yml  --env-file workspaces/test/dtaas/config/.env down -v
```

## 🔧 Customization

### Adding More Users

Follow the appropriate steps in [ADDING_MORE_USER.md](./ADDING_MORE_USERS.md).

## 🐛 Troubleshooting

### Certificate Issues

**Problem**: "NET::ERR_CERT_INVALID" in browser

**Solutions**:

- Verify certificate files exist in `./certs/` directory
- Check certificate file permissions
- Ensure `dynamic/tls.yml` correctly references certificate paths
- For self-signed certs, add security exception in browser

### Authentication Loop

**Problem**: Redirected to login repeatedly after authenticating

**Solutions**:

1. Clear browser cookies for `<DOMAIN_NAME>`
2. Verify the Keycloak client **Valid redirect URIs** includes `https://<DOMAIN_NAME>/login-relay/callback`
3. Ensure `KEYCLOAK_CLIENT_ID` in `.env` matches the client ID in Keycloak
4. Confirm client authentication is **ON** in Keycloak (confidential client)
5. Verify `KEYCLOAK_CLIENT_SECRET` in `.env` matches the Keycloak credentials tab
6. Verify that the `dtaas-workspace-audience` client scope is properly configured
6. Check login-relay logs:

   ```bash
   docker compose -f workspaces/test/dtaas/compose.traefik.secure.tls.yml \
     --env-file workspaces/test/dtaas/config/.env logs login-relay
   ```

### ERR_TOO_MANY_REDIRECTS on Tool URLs

**Problem**: Browser shows `ERR_TOO_MANY_REDIRECTS` when accessing
`/user1/tools/vnc/` or similar tool paths.

**Cause**: Oathkeeper v26 strips trailing slashes from URLs before forwarding
to the upstream container. If the workspace nginx.conf contains a
trailing-slash redirect (e.g. `return 302 $uri/`), the cycle becomes:
browser → `/tools/vnc/` → Oathkeeper strips → `/tools/vnc` → nginx
redirects → `/tools/vnc/` → loop.

**Solution**: The workspace `nginx.conf` must not contain a generic
trailing-slash redirect for tool paths. The VNC and VS Code location blocks
handle both `/tools/vnc` and `/tools/vnc/` directly via the `/?$` pattern.
If you have customised `workspaces/src/startup/nginx.conf`, verify no such
redirect is present.

### Oathkeeper 500 — "Multiple Rules Matched"

**Problem**: HTTP 500 from Oathkeeper

**Solution**: Access rule URL patterns in `oathkeeper/access-rules.yml` must
not overlap. Each URL must match exactly one rule. Review all patterns for
ambiguity.

### Services Not Accessible

1. Check all services are running:

   ```bash
   docker compose -f workspaces/test/dtaas/compose.traefik.secure.tls.yml \
     --env-file workspaces/test/dtaas/config/.env ps
   ```

2. Check Oathkeeper logs:

   ```bash
   docker compose -f workspaces/test/dtaas/compose.traefik.secure.tls.yml \
     --env-file workspaces/test/dtaas/config/.env logs oathkeeper
   ```

3. Check login-relay logs:

   ```bash
   docker compose -f workspaces/test/dtaas/compose.traefik.secure.tls.yml \
     --env-file workspaces/test/dtaas/config/.env logs login-relay
   ```

4. Check Traefik logs:

   ```bash
   docker compose -f workspaces/test/dtaas/compose.traefik.secure.tls.yml \
     --env-file workspaces/test/dtaas/config/.env logs traefik
   ```

### Keycloak Not Accessible

1. Check Keycloak is running:

   ```bash
   docker compose -f workspaces/test/dtaas/compose.traefik.secure.tls.yml \
     --env-file workspaces/test/dtaas/config/.env ps keycloak
   ```

2. Check Keycloak logs:

   ```bash
   docker compose -f workspaces/test/dtaas/compose.traefik.secure.tls.yml \
     --env-file workspaces/test/dtaas/config/.env logs keycloak
   ```

3. First startup can take 1–2 minutes.

### Port Conflicts

**Problem**: Ports 80 or 443 already in use

**Solutions**:

- Check for other services: `sudo netstat -tlnp | grep -E ':(80|443)'`
- Stop conflicting services
- Or modify port mappings in compose file (not recommended for production)

## 📚 Additional Resources

- [CONFIGURATION.md](CONFIGURATION.md) — General configuration guide
- [certs/README.md](certs/README.md) — TLS certificate setup
- [Traefik Documentation](https://doc.traefik.io/traefik/)
- [Keycloak Documentation](https://www.keycloak.org/documentation)
- [Oathkeeper Documentation](https://www.ory.sh/docs/oathkeeper)

## 🔄 Alternative Configurations

### Using a Different Identity Provider (Google, GitLab, etc.)

login-relay is hardcoded to Keycloak's OIDC endpoints. To use an external
provider, configure it as a **Keycloak Identity Provider** — Keycloak acts
as a broker and users log in via the external provider through Keycloak.
See the [Keycloak Identity Providers documentation](https://www.keycloak.org/docs/latest/server_admin/#_identity_broker).

### HTTP-Only with Keycloak (Development)

For development environments where TLS is not required, see [`TRAEFIK_SECURE.md`](TRAEFIK_SECURE.md).

This provides Keycloak authentication without TLS encryption.

### Basic Traefik (No Auth, No TLS)

For local development without authentication or encryption, see [`TRAEFIK.md`](TRAEFIK.md).

### Standalone Workspace (Single User)

For single-user local development, use:

```bash
docker compose -f workspaces/test/dtaas/compose.yml up -d
```
