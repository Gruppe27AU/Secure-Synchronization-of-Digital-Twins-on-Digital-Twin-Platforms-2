# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when
working with code in this repository.

## Repository Overview

This repository builds a containerized virtual desktop environment
(Workspace) for the DTaaS (Digital Twin as a Service) platform. The
container provides multiple services through a web-based interface
including KasmVNC for desktop access, Jupyter notebooks, VS Code
Server, and Firefox browser.

## Core Architecture

### Multi-stage Docker Build

The Dockerfile uses a multi-stage build pattern:

1. **Configure Stage**: Installs all software components and
  configurations
2. **Deploy Stage**: Creates a clean scratch image with all files
  from the configure stage

Key installed components in the workspace image:

- KasmVNC (web-based desktop/VNC)
- Jupyter Server (port 8090)
- VS Code Server (port 8054)
- Admin Service (port 8091) - FastAPI service for workspace service
  discovery
- Firefox browser
- nginx (reverse proxy routing)

### Service Routing

All services are accessed through nginx, which routes traffic based
on URL paths:

- Jupyter: `/`
- Jupyter Lab: `/lab`
- VS Code: `/tools/vscode`
- VNC Desktop: `/tools/vnc`
- Admin/Services API: `/{path-prefix}/services` (service discovery
  endpoint)

The routing configuration is templated in `startup/nginx.conf` and
processed by `startup/configure_nginx.py:12-59` which substitutes
environment variables at container startup.

### Admin Service

The admin service (`workspaces/src/admin/`) is a FastAPI application
that provides:

- `/services` endpoint - Returns JSON of available workspace services
- `/{path-prefix}/services` - Path-prefixed route for multi-user
  deployments
- `/health` - Health check endpoint
- Command-line interface via `workspace-admin` command

The service supports path prefixes for multi-user deployments,
configured via the `--path-prefix` CLI argument when starting the
service.

### Multi-User Deployment

The workspace supports multi-user deployments via Traefik reverse
proxy. Each user gets their own workspace container with path-based
routing:

- `domain.com/user1/...` → User 1's workspace services
- `domain.com/user2/...` → User 2's workspace services

See `TRAEFIK.md:69-85` for detailed configuration and access patterns.

### Data Persistence Model

User data is persisted through Docker volume mounts to the
`PERSISTENT_DIR` location (default: `/workspace`):

- `./persistent_dir/<username>` → `/workspace` in container
- This directory persists across container rebuilds and restarts
- Multiple users can share data through `./persistent_dir/common`

The persistence configuration is defined in `compose.yml:14-15`.

## Development Commands

### Build and Run Workspace

**Local single-user workspace** (see `compose.yml:1-20` for
configuration):

```bash
# Build the image
docker build -t workspace:latest -f Dockerfile .

# Run with Docker Compose
docker compose up -d

# Run with raw Docker
docker run -d --shm-size=512m -p 8080:8080 \
  -e MAIN_USER=dtaas-user \
  --name workspace \
  workspace:latest
```

### Manual Testing After Changes

After your changes build successfully, test them in a clean
environment:

```bash
# Test single-user deployment
docker compose down && docker compose up -d
# Wait 30 seconds then verify at http://localhost:8080
# Check logs: docker compose logs workspace

# Test multi-user with Traefik
docker compose -f compose.traefik.yml down && \
  docker compose -f compose.traefik.yml up -d
# Verify users are accessible at http://localhost/user1 and \
# http://localhost/user2
# Check Traefik dashboard at http://localhost:8088 (when enabled)
```

Multi-user with Traefik:

```bash
# Build first user container (if using local image)
docker compose -f compose.traefik.yml build user1

# Start all services
docker compose -f compose.traefik.yml up -d
```

### Before PR Checklist

Before submitting a PR, ensure all quality checks pass:

#### Docker & Compose

(see `config/kasm_vnc/kasmvnc.yaml:1-50` and
`config/jupyter/jupyter_notebook_config.py:1-20`)

```bash
hadolint Dockerfile                                    # Dockerfile linting
for file in $(find . -name "compose*.yaml" -o -name "compose*.yml"); do \
  docker compose -f "$file" config --quiet; done  # Compose validation
```

#### Shell Scripts

(see `.shellcheckrc:1-5` for configuration)

```bash
shellcheck install/**/*.sh startup/*.sh               # All shell scripts
```

#### Python Scripts

(see `.pylintrc:1-30` for configuration)

```bash
pylint startup/configure_nginx.py                     # Python linting
flake8 startup/configure_nginx.py                     # Python style
```

#### YAML & Markdown

(see `.yamllint.yml:1-10` and `.markdownlint.yaml:1-15`)

```bash
yamllint .                                            # YAML validation
markdownlint .                                        # Markdown style
```

See `.github/workflows/` for all automated checks (see
`workspace-publish.yml:1-20` for publish workflow).

### Troubleshooting

**Container fails to start**

```bash
docker compose logs workspace    # View container logs
docker ps -a                     # Check container status
```

**Service unreachable**

- Verify `MAIN_USER` environment variable matches the username in
  your URL
- Check nginx configuration with `docker exec workspace cat \
  /etc/nginx/nginx.conf`
- Confirm all ports are exposed and not blocked by firewall

**Linting failures**

```bash
# Run individual linters to identify specific issues
shellcheck -x <script.sh>        # Verbose shellcheck
hadolint --no-color Dockerfile   # Verbose hadolint
```

**Permission errors**

```bash
chmod +x install/**/*.sh startup/*.sh    # Ensure scripts are executable
git update-index --chmod=+x <file>       # Fix git executable bit
```

### Publishing Docker Images

Images are automatically published via GitHub Actions when PRs merge
to main. The workflow:

1. Runs all quality checks
2. Builds the Docker image
3. Publishes to both GHCR and Docker Hub
4. Tests the published images

To manually trigger publishing:

1. Go to Actions → "Publish Workspace Docker Image"
2. Click "Run workflow"
3. Select the branch

**Required Secrets** (for Docker Hub):

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

## Adding or Modifying Components

### Installation Scripts

Location: `install/<component>/install_<component>.sh`

When adding new software (see example patterns in
`install/firefox/install_firefox.sh:1-30`):

1. Create installation script following shell format (see existing
  examples)
2. Pin versions explicitly
3. Clean up temporary files
4. Update `Dockerfile:32-36` to call the installation script
5. Run installation scripts as part of the Docker build process, not
  at container runtime

### Configuration Files

Location: `config/<component>/`

- Place service-specific config files here
- Document any environment variables that affect configuration
- Ensure configs work in containerized environment

### Startup Scripts

Location: `startup/`

- `dtaas_shim.sh`: Runs before base image startup scripts (see
  `Dockerfile:61`)
- `configure_nginx.py:12-59`: Configures nginx routing with
  environment variables
- `custom_startup.sh`: Pluggable service configuration

**Python scripts**: Use type hints and error handling. See
`startup/configure_nginx.py:1-10` for proper module docstring and
imports.

### Admin Service Development

Location: `workspaces/src/admin/`

The admin service is a Poetry-managed Python project with FastAPI:

```bash
cd workspaces/src/admin

# Install dependencies
poetry install

# Run tests with coverage
poetry run pytest --cov=admin --cov-report=html --cov-report=term-missing

# Run linting
poetry run pylint src/admin tests

# Run the service locally
poetry run workspace-admin --path-prefix dtaas-user

# List services without starting server
poetry run workspace-admin --list-services
```

The package is layered, and the test files mirror it:

- `src/admin/api.py` - FastAPI app factory and HTTP routes
  (tested by `tests/unit/test_api.py`)
- `src/admin/services.py` - Workspace service discovery
  (tested by `tests/unit/test_services.py`)
- `src/admin/main.py` - CLI entry point
  (tested by `tests/unit/test_main.py`)
- `src/admin/git/` - Git backup of workspace directories
- `src/admin/config/` - Service catalogue and other configuration data

Dependencies point one way only: `main` → `api` → `services`.

To add new services to the workspace:

1. Edit `src/admin/src/admin/config/services_template.json`
2. Add service definition with name, description, and endpoint
3. No code changes needed - template is read at runtime

## Important Environment Variables

- `MAIN_USER`: Username for the workspace (default: `dtaas-user`)
- `JUPYTER_SERVER_PORT`: Port for Jupyter (default: 8090)
- `CODE_SERVER_PORT`: Port for VS Code Server (default: 8054)
- `ADMIN_SERVER_PORT`: Port for Admin service (default: 8091)
- `NO_VNC_PORT`: Port for KasmVNC (default varies, read by
  `configure_nginx.py`)
- `PERSISTENT_DIR`: Directory for user data persistence (default:
  `/workspace`)
- `PATH_PREFIX`: Optional path prefix for admin service routes (can
  be set via CLI)

## Code Quality Standards

This project enforces strict code quality - all linting must pass
before merging. See `.github/copilot-instructions.md` for detailed
guidelines.

### Shell Scripts

- Follow Google Shell Style Guide (see `.shellcheckrc:1-5`)
- Use `set -e` for error handling
- Quote variables appropriately
- Include proper shebang lines
- **Example**: `install/firefox/install_firefox.sh` demonstrates
  proper structure

### Python Scripts

- Follow PEP 8 style guide (see `.pylintrc`)
- Include docstrings for functions and modules
- Use type hints where appropriate
- Handle errors gracefully
- **Example**: `startup/configure_nginx.py:1-10` shows proper
  docstring and imports

### Dockerfile

- Pin specific versions for base images and packages (see
  `Dockerfile:1` uses `1.18.0`)
- Minimize layers by combining RUN commands
- Clean up package manager caches in the same RUN command
- Use OCI-compliant labels (see `Dockerfile:4-12`)

### Docker Compose

- Use version 3.x syntax
- Define explicit service dependencies
- Use environment variables for configuration
- Include volume mounts for persistent data

### Before Committing Changes

1. Lint all modified scripts using the commands in "Before PR
  Checklist"
2. Build the Docker image locally: `docker build -t workspace-test:\
  latest .`
3. Test services start correctly and are accessible
4. Verify persistent data survives container rebuilds
5. Run both single-user and multi-user test scenarios if applicable

## Additional Documentation

- **README.md:25-88** - Basic build and run instructions
- **workspaces/test/dtaas/TRAEFIK.md** - Multi-user deployment with
  Traefik reverse proxy
- **docs/PUBLISHING.md** - Docker image publishing workflow and
  registry configuration
- **docs/CHANGELOG.md** - Version history and release notes
- **docs/PENDING_ISSUES.md** and **docs/POTENTIAL_IMPROVEMENTS.md** -
  Working notes and backlog

## Getting Help

If you encounter issues:

1. Check the troubleshooting section above
2. Review logs: `docker compose logs workspace`
3. Inspect container: `docker exec -it workspace bash`
4. Check service status: `docker exec workspace ps aux`
