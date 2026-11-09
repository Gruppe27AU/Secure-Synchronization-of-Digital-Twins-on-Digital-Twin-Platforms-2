# Adding more users
The [`compose.traefik.yml`](./compose.traefik.yml),
[`compose.traefik.secure.yml`](./compose.traefik.secure.yml) and
[`compose.traefik.secure.tls.yml`](./compose.traefik.secure.tls.yml) Docker
compositions all work with multiple users. By default, two users are included
in the basic setup, but this can be extended. To do this, follow the steps that
fit your composition. In this guide, replace `user3` with the name of the new
user to be added.

- `compose.traefik.tml`:
  - [Setup Persistent Directories](#setup-persistent-directories)
  - [Add Additional Workspace Instance to Composition](#additional-workspace-in-composetraefikyml)
  - [Update Environment Config](#update-environment-config)
- `compose.traefik.secure.yml`:
  - [Setup Persistent Directories](#setup-persistent-directories)
  - [Add Additional Workspace Instance to Composition](#additional-workspace-in-composetraefiksecureyml)
  - [Update Environment Config](#update-environment-config)
  - [Add Variable to Oathkeeper Service](#add-variable-to-oathkeeper-service)
  - [Oathkeeper Access Rules](#oathkeeper-access-rules)
  - [Create New User in Keycloak](#create-new-user-in-keycloak)
  - [Redeploy](#redeploy)
- `compose.traefik.secure.tls.yml`:
  - [Setup Persistent Directories](#setup-persistent-directories)
  - [Add Additional Workspace Instance to Composition](#additional-workspace-in-composetraefiksecuretlsyml)
  - [Update Environment Config](#update-environment-config)
  - [Add Variable to Oathkeeper Service](#add-variable-to-oathkeeper-service)
  - [Oathkeeper Access Rules](#oathkeeper-access-rules)
  - [Create New User in Keycloak](#create-new-user-in-keycloak)
  - [Redeploy](#redeploy)

## Setup Persistent Directories

Copy the directory structure for the standard user to the new user.

```bash
cp -r ./workspaces/test/dtaas/files/user1 ./workspaces/test/dtaas/files/user3
sudo chown -R 1000:100 workspaces/test/dtaas/files
```

## Add Additional Workspace Instance to Composition

An additional workspace instance needs to be added to the Docker compose file.
The structure of this instance is file dependent - see the relevant subsection
below.

### Additional Workspace in `compose.traefik.yml`

To add additional workspace instances, add a new service in `compose.traefik.yml`:

```yaml
user3:
  image: workspace:latest
  restart: unless-stopped
    build:
      context: ../..
      dockerfile: Dockerfile.ubuntu.noble.xfce
  environment:
    - MAIN_USER=${USERNAME3:-user3}
  volumes:
    - ./files/user3:/workspace
    - ./files/common:/workspace/common
  shm_size: 512m
  labels:
    - "traefik.enable=true"
    - "traefik.http.routers.u3.entryPoints=web"
    - "traefik.http.routers.u3.rule=PathPrefix(`/${USERNAME3:-user3}`)"
  networks:
    - users
```

### Additional Workspace in `compose.traefik.secure.yml`

Add a new workspace service to `compose.traefik.secure.yml`:

```yaml
  user3:
    image: workspace:latest
    restart: unless-stopped
    build:
      context: .
      dockerfile: ../Dockerfile.ubuntu.noble.xfce
    environment:
      - MAIN_USER=${USERNAME3:-user3}
    volumes:
      - "./files/common:/workspace/common"
      - "./files/${USERNAME3:-user3}:/workspace"
    shm_size: 512m
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.u3.entryPoints=web"
      - "traefik.http.routers.u3.rule=Host(`${SERVER_DNS}`) && PathPrefix(`/${USERNAME3:-user3}`)"
      - "traefik.http.routers.u3.middlewares=oathkeeper-auth"
    networks:
      - users
```

### Additional Workspace in `compose.traefik.secure.tls.yml`

Add a new workspace service to `compose.traefik.secure.tls.yml`:

```yaml
  user3:
    image: workspace:latest
    restart: unless-stopped
    build:
      context: ../..
      dockerfile: Dockerfile.ubuntu.noble.xfce
    environment:
      - MAIN_USER=${USERNAME3:-user3}
    volumes:
      - "./files/common:/workspace/common"
      - "./files/${USERNAME3:-user3}:/workspace"
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.u3.entryPoints=web-secure"
      - "traefik.http.routers.u3.rule=Host(`${SERVER_DNS}`) && PathPrefix(`/${USERNAME3:-user3}`)"
      - "traefik.http.routers.u3.tls=true"
      - "traefik.http.services.u3.loadbalancer.server.port=8080"
      - "traefik.http.routers.u3.middlewares=oathkeeper-auth"
    networks:
      - users
```

## Update Environment Config

Add the new username to [`config/.env`](./config/.env). `user1` and `user2`
are the names of the two already present users (and might have been changed
by you previously)

```bash
USERNAME3=user3
# Keep WORKSPACE_USERS in sync — add the new name to the comma-separated list.
WORKSPACE_USERS=user1,user2,user3
```

## Add Variable to Oathkeeper Service

In your Docker compose file, add `USERNAME3=${USERNAME3:-user3}` to the `environment` section of the
`oathkeeper` service.

## Oathkeeper Access Rules

Add an access rule in `oathkeeper/access-rules.yml`:

```yaml
- id: dtaas-user3-workspace
  version: "v0.36.0-beta.1"
  description: >
    Requires a valid Keycloak JWT whose username matches the path prefix
    (${USERNAME3}). The remote_json authorizer delegates the per-user check
    to login-relay, which returns 200 only when the token belongs to that user.
  match:
    url: "<^https?://[^/]*/${USERNAME3}(/.*)?$>"
    methods:
      - GET
      - HEAD
      - POST
      - PUT
      - PATCH
      - DELETE
      - OPTIONS
  authenticators:
    - handler: oauth2_introspection
      config:
        token_from:
          header: Authorization
    - handler: oauth2_introspection
      config:
        token_from:
          cookie: dtaas_access_token
  authorizer:
    handler: remote_json
    config:
      remote: http://login-relay:8080/authz/workspace/${USERNAME3}
      payload: '{"subject":{"id":{{ .Subject | toJson }},"extra":{{ .Extra | toJson }}}}'
  mutators:
    - handler: header
```

## Create New User in Keycloak

Add a new user in Keycloak with the new name, following the steps in the
[Create Users section of KEYCLOAK_SETUP.md](./KEYCLOAK_SETUP.md#create-users).

## Redeploy

Redeploy the composition, replacing <THE_COMPOSE_FILE> with either
`compose.traefik.secure.yml` or `compose.traefik.secure.tls.yml` as appropriate.

```bash
docker compose -f workspaces/test/dtaas/<THE_COMPOSE_FILE> \
  --env-file workspaces/test/dtaas/config/.env \
  up -d --force-recreate oathkeeper login-relay
```
