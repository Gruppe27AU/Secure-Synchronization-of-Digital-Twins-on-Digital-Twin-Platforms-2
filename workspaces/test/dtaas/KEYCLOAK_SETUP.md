# Keycloak Setup
Keycloak is used for both `compose.traefik.secure.yml` and
`compose.traefik.secure.tls.yml`, and needs to be setup after the compositions
have been brought up.

To setup Keycloak follow the steps below, replacing `<DOMAIN_NAME>` with either
either your domain name if testing remotely, or `localhost` if testing locally,
and `<PROTOCOL>` with `http` if setting up `compose.traefik.secure.yml` or
`https` if setting up `compose.traefik.secure.tls.yml`.

## Access Keycloak Admin Console

1. Navigate to `<PROTOCOL>://<DOMAIN_NAME>/auth`
2. Login with credentials from your `.env` file (default: `admin` / `changeme`)

## Create a Realm

1. In the left sidebar, click **Manage realms**.
2. You are taken to the "Manage realms" page - click the **Create realm** button.
3. **Realm name**: `dtaas` (you can set a different name. If you do so, make so
  make sure that the value of `KEYCLOAK_REALM` in the
  [environment file `config/.env`](./config/.env) matches this name.)
4. Click **Create**

## Create the workspace Client

1. In the left sidebar, click **Clients**
2. Click **Create client**
3. Configure the client:
   - **Client type**: OpenID Connect
   - **Client ID**: `dtaas-workspace` (you can set a different id. If you do so, make so
  make sure that the value of `KEYCLOAK_CLIENT_ID` in the
  [environment file `config/.env`](./config/.env) matches this id.)
   - Click **Next**
4. Capability config:
   - **Client authentication**: **ON** (confidential client)
   - Authorization: OFF
   - Authentication flow: enable **Standard flow**
   - Click **Next**
5. Login settings:
   - **Root URL**: `<PROTOCOL>://<DOMAIN_NAME>`
   - **Valid redirect URIs**: `<PROTOCOL>://<DOMAIN_NAME>/login-relay/callback`
   - **Valid post logout redirect URIs**: `<PROTOCOL>://<DOMAIN_NAME>/*`
     *(required — login-relay redirects to `/` after logout; without this
     Keycloak will show "Invalid redirect uri")*
   - **Web origins**: `<PROTOCOL>://<DOMAIN_NAME>`
   - Click **Save**
6. Get the client secret:
   - Go to the **Credentials** tab
   - Copy the **Client secret** value
   - Update `KEYCLOAK_CLIENT_SECRET` in your [environment file `config/.env`](./config/.env) with this secret
7. Add an Audience mapper so the JWT's `aud` claim contains `dtaas-workspace`
   - Go to the **Client scopes** tab
   - Click `dtaas-workspace-dedicated`
   - Click **Configure a new mapper** → **Audience**
   - Set **Name**: `dtaas-workspace-audience`
   - Set **Included Client Audience**: `dtaas-workspace`
   - **Add to access token**: ON
   - Click **Save**

## Create the DTaaS web Client

1. In the left sidebar, click **Clients**
2. Click **Create client**
3. Configure the client:
   - **Client type**: OpenID Connect
   - **Client ID**: `dtaas-client`
   - Click **Next**
4. Capability config:
   - **Client authentication**: OFF (public client — no secret)
   - **Authorization**: OFF
   - **Authentication flow**: enable **Standard flow** only
   - **Require PKCE**: ON
   - **PKCE Method**: S256
   - Click **Next**
5. Login settings:
   - **Root URL**: `<PROTOCOL>://<DOMAIN_NAME>`
   - **Valid redirect URIs**: `<PROTOCOL>://<DOMAIN_NAME>/*`
   - **Valid post logout redirect URIs**: `<PROTOCOL>://<DOMAIN_NAME>/*`
   - **Web origins**: `<PROTOCOL>://<DOMAIN_NAME>`
   - Click **Save**
6. Add a `username` claim mapper so the SPA receives the username in the token:
   - Go to the **Client Scopes** tab
   - Click **`dtaas-client-dedicated`**
   - Click **Configure a new mapper** → **User Property**
   - Fill in:
     - **Name**: `username`
     - **Property**: `username`
     - **Token Claim Name**: `username`
     - **Claim JSON Type**: `String`
     - **Add to ID token**: ON
     - **Add to access token**: ON
     - **Add to userinfo**: ON
   - Click **Save**

## Create Users

1. In the left sidebar, click **Users**
2. Click **Create new user**
3. Fill in user details:
   - **Username**: `user1` (or desired username - make sure this matches the username set during [configuration](#️-initial-configuration))
   - **Email**: user's email (optional)
   - **First name** / **Last name**: optional
   - **Email verified**: OFF
4. Click **Create**
5. Set password:
   - Go to the **Credentials** tab
   - Click **Set password**
   - Enter a password
   - **Temporary**: OFF (so users don't have to change it on first login)  
   - Click **Save**
6. Repeat for additional users (e.g., `user2`)

## Restart Services

After configuring Keycloak, restart the auth services so they pick up the
new realm and client configuration, replacing <THE_COMPOSE_FILE> with either
`compose.traefik.secure.yml` or `compose.traefik.secure.tls.yml` as appropriate.

```bash
docker compose -f workspaces/test/dtaas/<THE_COMPOSE_FILE> \
  --env-file workspaces/test/dtaas/config/.env \
  up -d --force-recreate oathkeeper login-relay
```