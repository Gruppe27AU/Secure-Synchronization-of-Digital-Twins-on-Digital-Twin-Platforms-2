# Workspace with Traefik Reverse Proxy

This guide explains how to use the workspace container with Traefik reverse proxy
for multi-user deployments in the DTaaS installation.

## ❓ Prerequisites

✅ Docker Engine v27 or later  
✅ Sufficient system resources (at least 1GB RAM per workspace instance)  
✅ Port 80 available on your host machine

## 🗒️ Overview

The `compose.traefik.yml` file sets up:

- **Traefik** reverse proxy on port 80
- **user1** workspace using the workspace image
- **user2** workspace using the mltooling/ml-workspace-minimal image
- Two Docker networks: `dtaas-frontend` and `dtaas-users`

Traefik routes requests to different workspace instances based on URL path prefixes.

## ⚙️ Initial Configuration

Please follow the steps in [`CONFIGURATION.md`](CONFIGURATION.md) for
the `compose.traefik.yml` composition before running the setup.

## Create Workspace Files

All the deployment options require user directories for
storing workspace files. These need to
be created for `USERNAME1` and `USERNAME2` set in
`workspaces/test/dtaas/config/.env` file.

```bash
# create required files
cp -R workspaces/test/dtaas/files/user1 workspaces/test/dtaas/files/<USERNAME1>
cp -R workspaces/test/dtaas/files/user1 workspaces/test/dtaas/files/<USERNAME2>
# set file permissions for use inside the container
sudo chown -R 1000:100 workspaces/test/dtaas/files
```

## :rocket: Start Services

To start all services (Traefik and both workspace instances):

```bash
docker compose -f workspaces/test/dtaas/compose.traefik.yml --env-file workspaces/test/dtaas/config/.env up -d
```

This will:

1. Start the Traefik reverse proxy on port 80
2. Start workspace of both users

## :technologist: Accessing Workspaces

Once all services are running, access the workspaces through Traefik:

### User1 Workspace (workspace)

- **VNC Desktop**: `http://localhost/user1/tools/vnc`
- **VS Code**: `http://localhost/user1/tools/vscode`
- **Jupyter Notebook**: `http://localhost/user1`
- **Jupyter Lab**: `http://localhost/user1/lab`

#### Service Discovery

The workspace provides a `/services` endpoint that returns a JSON list of
available services. This is intended for future dynamic service discovery
for frontend applications.

**Example**: Get service list for user1

```bash
curl http://localhost/user1/services
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

### User2 Workspace (ml-workspace-minimal)

- **VNC Desktop**: `http://localhost/user2/tools/vnc/?password=vncpassword`
- **VS Code**: `http://localhost/user2/tools/vscode/`
- **Jupyter Notebook**: `http://localhost/user2`
- **Jupyter Lab**: `http://localhost/user2/lab`

### Custom URL

Remember to change the following variables in URLs to the variable values
specified in `.env`:

- Change `user1` to `USERNAME1` value
- Change `user2` to `USERNAME2` value
- Change `localhost` in URL to the `SERVER_DNS` value

## 🛑 Stopping Services

To stop all services:

```bash
docker compose -f workspaces/test/dtaas/compose.traefik.yml --env-file workspaces/test/dtaas/config/.env down
```

## 🔧 Customization

### Adding More Users

Follow the appropriate steps in [ADDING_MORE_USER.md](./ADDING_MORE_USERS.md).

## :shield: Security Considerations

⚠️ **Important**: This configuration is designed for development and testing, and should not be reconfigured to be exposed to the internet.

For setting up a composition that can be exposed to the internet, see [TRAEFIK_TLS.md](./TRAEFIK_TLS.md).
