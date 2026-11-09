# Workspace with Traefik Forward Auth (OIDC/Keycloak Security)

This guide explains how to use the workspace container with Traefik reverse proxy
and OIDC authentication via Keycloak and traefik-forward-auth for secure
multi-user deployments in the DTaaS installation.

## ❓ Prerequisites

✅ Docker Engine v27 or later
✅ Sufficient system resources (at least 2GB RAM - includes Keycloak)  
✅ Port 80 available on your host machine  

## 🗒️ Overview

The `compose.traefik.secure.yml` file sets up:

- **Traefik** reverse proxy on port 80
- **Keycloak** identity provider with OIDC support
- **Oathkeeper** — JWT proxy; validates tokens and forwards authenticated
  requests to workspaces
- **login-relay** — lightweight login relay service; initiates the Keycloak
  authorization code flow and sets the `dtaas_access_token` cookie after
  successful authentication
- **client** - DTaaS web interface
- **user1** workspace using the workspace image
- **user2** workspace using the mltooling/ml-workspace-minimal image
- Two Docker networks: `dtaas-frontend` and `dtaas-users`

## ⚙️ Initial Configuration

Please follow the steps in [`CONFIGURATION.md`](CONFIGURATION.md)
for the `compose.traefik.secure.yml` composition before building
the workspace and running the setup.

## :rocket: Start Services

To start all services (Traefik, Keycloak, auth, client, and workspaces):

```bash
docker compose -f workspaces/test/dtaas/compose.traefik.secure.yml --env-file workspaces/test/dtaas/config/.env up -d
```

This will:

1. Start the Traefik reverse proxy on port 80
2. Start Keycloak identity provider at `/auth`
3. Start Oathkeeper JWT proxy (validates tokens, routes workspace traffic)
4. Start the login-relay service at `/login-relay`
5. Start the DTaaS web client interface
6. Start workspace instances for both users

**Note**: First-time startup may take a few minutes for Keycloak to initialize.

## :gear: Configure Keycloak

After starting the services, Keycloak must be configured.
To do this, follow the steps in [KEYCLOAK_SETUP.md](./KEYCLOAK_SETUP.md).

## :technologist: Accessing Workspaces

Once all services are running and Keycloak is configured, access them through Traefik at `http://<DOMAIN_NAME>`.

### Initial Access

1. Navigate to `http://<DOMAIN_NAME>` in your web browser
2. You will be redirected to Keycloak for authentication
3. Log in with a user you created in Keycloak
4. You will be redirected back to the DTaaS web interface

### Keycloak Admin Console

- **URL**: `http://<DOMAIN_NAME>/auth`
- Access to manage users, roles, clients, and authentication settings
- Login with `KEYCLOAK_ADMIN` credentials from `.env`

### DTaaS Web Client

- **URL**: `http://<DOMAIN_NAME>/`

### User1 Workspace

All endpoints require authentication:

- **VNC Desktop**: `http://<DOMAIN_NAME>/user1/tools/vnc`
- **VS Code**: `http://<DOMAIN_NAME>/user1/tools/vscode`
- **Jupyter Notebook**: `http://<DOMAIN_NAME>/user1`
- **Jupyter Lab**: `http://<DOMAIN_NAME>/user1/lab`

#### Service Discovery

The workspace provides a `/services` endpoint that returns a JSON list of
available services. This is intended for future dynamic service discovery
for frontend applications.

**Example**: Get service list for user1

```bash
curl http://<DOMAIN_NAME>/user1/services
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

- **VNC Desktop**: `http://<DOMAIN_NAME>/user2/tools/vnc`
- **VS Code**: `http://<DOMAIN_NAME>/user2/tools/vscode`
- **Jupyter Notebook**: `http://<DOMAIN_NAME>/user2`
- **Jupyter Lab**: `http://<DOMAIN_NAME>/user2/lab`

### Logging Out
When logged in as one of the users:

- **URL**: `https://<DOMAIN_NAME>/logout`

## 🛑 Stopping Services

To stop all services:

```bash
docker compose -f workspaces/test/dtaas/compose.traefik.secure.yml \
  --env-file workspaces/test/dtaas/config/.env down
```

## 🔧 Customization

### Adding More Users

Follow the appropriate steps in [ADDING_MORE_USER.md](./ADDING_MORE_USERS.md).

## :shield: Security Considerations

### Current Setup (Development/Testing)

⚠️ **Important**: This configuration is designed for development and testing
and uses some insecure settings:

- `INSECURE_COOKIE=true` - Allows cookies over HTTP
- Traefik API is exposed (`--api.insecure=true`)
- No TLS/HTTPS encryption
- Debug logging enabled

For setting up a composition that includes TLS/HTTPS, see [TRAEFIK_TLS.md](TRAEFIK_TLS.md).

## 🔍 Troubleshooting

### Keycloak "HTTPS Required" Error

If Keycloak displays "We are sorry... HTTPS required" when accessed via HTTP:

1. This is caused by the per-realm `sslRequired` setting (defaults to `external`),
   which rejects HTTP from non-localhost clients
2. Fix it by disabling SSL requirement on the affected realm(s) — see
   [Disable Realm SSL Requirement](#disable-realm-ssl-requirement-http-only) above
3. If you previously ran the TLS composition (`compose.traefik.secure.tls.yml`),
   the `keycloak-data` volume may retain old SSL settings. Remove it and restart:

   ```bash
   docker compose -f workspaces/test/dtaas/compose.traefik.secure.yml \
     --env-file workspaces/test/dtaas/config/.env down
   docker volume rm dtaas_keycloak-data
   docker compose -f workspaces/test/dtaas/compose.traefik.secure.yml \
     --env-file workspaces/test/dtaas/config/.env up -d
   ```

   Then re-apply the SSL disable steps after Keycloak starts.

### Keycloak Not Accessible

1. Check Keycloak is running:

   ```bash
   docker compose -f workspaces/test/dtaas/compose.traefik.secure.yml \
     --env-file workspaces/test/dtaas/config/.env ps keycloak
   ```

2. Check Keycloak logs:

   ```bash
   docker compose -f workspaces/test/dtaas/compose.traefik.secure.yml \
     --env-file workspaces/test/dtaas/config/.env logs keycloak
   ```

3. Wait for Keycloak to fully start (first startup can take 1-2 minutes)

### Authentication Loop

If you're stuck in an authentication loop:

1. Clear browser cookies for localhost
2. Check that `OAUTH_SECRET` is set and consistent
3. Verify Keycloak client redirect URI matches `http://localhost/_oauth/*`
4. Check traefik-forward-auth logs for errors
5. Ensure `KEYCLOAK_ISSUER_URL` is correct

### Services Not Accessible

1. Check all services are running:

   ```bash
   docker compose -f workspaces/test/dtaas/compose.traefik.secure.yml ps
   ```

2. Check Traefik logs:

   ```bash
   docker compose -f workspaces/test/dtaas/compose.traefik.secure.yml logs traefik
   ```

3. Check traefik-forward-auth logs:

   ```bash
   docker compose -f workspaces/test/dtaas/compose.traefik.secure.yml logs traefik-forward-auth
   ```

### OIDC/OAuth Errors

If you see OIDC errors:

1. Verify all environment variables in `.env` are correct
2. Check Keycloak client settings (client ID, secret, redirect URIs)
3. Ensure Keycloak realm name matches `KEYCLOAK_REALM`
4. Verify client authentication is enabled in Keycloak
5. Check that the issuer URL is accessible from the traefik-forward-auth container

### "Invalid Client" Error

- Verify `KEYCLOAK_CLIENT_SECRET` matches the value in Keycloak
- Ensure client authentication is enabled in Keycloak client settings

## 📚 Additional Resources

- [KEYCLOAK_SETUP.md](KEYCLOAK_SETUP.md) - Detailed Keycloak setup guide
- [KEYCLOAK_MIGRATION.md](KEYCLOAK_MIGRATION.md) - Migration guide from GitLab OAuth
- [CONFIGURATION.md](CONFIGURATION.md) - General configuration guide
- [Traefik Documentation](https://doc.traefik.io/traefik/)
- [Keycloak Documentation](https://www.keycloak.org/documentation)
- [traefik-forward-auth GitHub](https://github.com/thomseddon/traefik-forward-auth)
- [OIDC Specification](https://openid.net/specs/openid-connect-core-1_0.html)
- [DTaaS Documentation](https://github.com/INTO-CPS-Association/DTaaS)
