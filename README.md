# Workspace

A new workspace image for [DTaaS](https://github.com/INTO-CPS-Association/DTaaS).

We are still very much in the explorative phase. Everything that is
working is subject to change.

## 📦 Pre-built Images

Pre-built Docker images are available from:

### Standard Workspace (Ubuntu 24.04 Noble + XFCE)

- **GitHub Container Registry**:
  `ghcr.io/into-cps-association/workspace:latest`
- **Docker Hub**: `intocps/workspace:latest`

```bash
# Standard image from GitHub Container Registry
docker pull ghcr.io/into-cps-association/workspace:latest

# From Docker Hub
docker pull intocps/workspace:latest
```

## 🦾 Build Workspace Image

If you want to build the image locally instead of using pre-built images, then:

### Single Platform Build

*Either*  
Using plain `docker` command:

```ps1
docker build -t workspace:latest -f workspaces/Dockerfile.ubuntu.noble.xfce ./workspaces
```

**Or**
using `docker compose`:

```bash
docker compose -f workspaces/test/dtaas/compose.yml build
```

### Multi-Platform Build

To build images for multiple architectures (amd64 and arm64):

```bash
# Create and use a multi-platform builder (one-time setup)
docker buildx create --name multiarch --use

# Build standard image for multiple platforms
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t workspace:latest \
  -f workspaces/Dockerfile.ubuntu.noble.xfce \
  ./workspaces

# To build and push to a registry (e.g., Docker Hub or GHCR)
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t your-registry/workspace:latest \
  -f workspaces/Dockerfile.ubuntu.noble.xfce \
  --push \
  ./workspaces
```

**Note**: Multi-platform builds require Docker Buildx and QEMU for cross-platform
emulation. The pre-built images from GitHub Container Registry and Docker Hub
are already multi-platform and will automatically match your system architecture.

## :running: Run it

*Either*  
Using plain `docker` command:

```bash
docker run -d --shm-size=512m \
  -p 8080:8080 \
  -e MAIN_USER=user1 --name workspace  workspace:latest
```

:point_right: You can change the **MAIN_USER** variable to any other username
of your choice.

## :technologist: Use Services

An active container provides the following services.
:warning: please remember to change `user1` to the username (`USERNAME1`) set in
the `.env` file.

- ***Open workspace*** -
  <http://localhost:8080/user1/tools/vnc>
- ***Open VSCode*** - <http://localhost:8080/user1/tools/vscode>
- ***Open Jupyter Notebook*** - <http://localhost:8080/user1>
- ***Open Jupyter Lab*** - <http://localhost:8080/user1/lab>

### Service Discovery

The workspace provides a `/services` endpoint that returns a JSON list of
available services. This is intended for future dynamic service discovery
for frontend applications.

**Example**: Get service list for user1

```bash
curl http://localhost:8080/user1/services
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
`MAIN_USER` environment variable.

## :broom: Clean Up

*Either*  
Using plain `docker` command:

```bash
docker stop workspace
docker rm workspace
```

## :arrows_counterclockwise: Deployment Options

This workspace supports multiple deployment configurations depending
on your needs. All deployment-relevant files can be found in
`workspaces/test/dtaas/`.

### 1. Standalone Development (Single User)

**File**: `compose.yml`  
**Use case**: Local development, single user  
**Features**: Basic workspace without reverse proxy  
**Documentation**: [SINGLE_USER.md](workspaces/test/dtaas/SINGLE_USER.md)

### 2. Multi-User Development (HTTP)

**File**: `compose.traefik.yml`  
**Use case**: Multi-user development/testing without authentication  
**Features**: Traefik reverse proxy, multiple workspaces  
**Documentation**: [TRAEFIK.md](workspaces/test/dtaas/TRAEFIK.md)

### 3. Multi-User with OAuth2 (HTTP)

**File**: `compose.traefik.secure.yml`  
**Use case**: Development/testing with OAuth2 authentication  
**Features**: Traefik reverse proxy, OAuth2 authentication, HTTP only  
**Documentation**: [TRAEFIK_SECURE.md](workspaces/test/dtaas/TRAEFIK_SECURE.md)

### 4. Production Deployment (HTTPS + OAuth2)

**File**: `compose.traefik.secure.tls.yml`  
**Use case**: Production deployment with full security  
**Features**: Traefik reverse proxy, OAuth2 authentication, TLS/HTTPS  
**Documentation**: [TRAEFIK_TLS.md](workspaces/test/dtaas/TRAEFIK_TLS.md)

Choose the configuration that best matches your deployment requirements.

## 📊 Deployment Comparison

| Feature | compose.yml | compose.traefik.yml | compose.traefik.secure.yml | compose.traefik.secure.tls.yml |
| ------- | ----------- | ------------------- | -------------------------- | ------------------------------ |
| Reverse Proxy | ❌ | ✅ | ✅ | ✅ |
| Multi-user | ❌ | ✅ | ✅ | ✅ |
| OAuth2 Auth | ❌ | ❌ | ✅ | ✅ |
| TLS/HTTPS | ❌ | ❌ | ❌ | ✅ |
| Production Ready | ❌ | ❌ | ❌ | ✅ |
| Use Case | Local dev | Multi-user dev | Secure dev/test | Production |

## :package: Publishing

For information about publishing Docker images to registries,
see [PUBLISHING.md](docs/PUBLISHING.md).

## Development

### Alternative Development Image

If the full featureset of the workspace image is not necessary during development, a slimmer, quicker to build image can be generated instead.

This is done by adding the setting the build argument `INSTALLATION` to `minimal` when building the image. For example:

```
docker build -t workspace:latest \
  -f workspaces/Dockerfile.ubuntu.noble.xfce \
  --build-arg INSTALLATION=minimal \
  ./workspaces
```

### Code Quality

This project enforces strict code quality checks via GitHub Actions:

* **Dockerfile**: Linted with [hadolint](https://github.com/hadolint/hadolint)
  \- all errors must be fixed
* **Shell scripts**: Checked with [shellcheck](https://www.shellcheck.net/)
  \- all warnings must be addressed
* **Python scripts**: Linted with [flake8](https://flake8.pycqa.org/) and
  [pylint](https://pylint.org/) - all errors must be resolved
* **YAML files**: Validated with [yamllint](https://yamllint.readthedocs.io/)
  \- all issues must be corrected
* **Markdown files**: Checked with
  [markdownlint](https://github.com/DavidAnson/markdownlint) - all style
  violations must be fixed

All quality checks must pass before code can be merged. The workflows will
fail if any linting errors are detected.

### Configuration Files

Linting behavior is configured through:

* `.shellcheckrc` - shellcheck configuration
* `.pylintrc` - pylint configuration
* `.yamllint.yml` - yamllint configuration
* `.markdownlint.yaml` - markdownlint configuration
