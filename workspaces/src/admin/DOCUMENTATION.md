# Temporary documentation file with latest changes updated by Claude

# Admin Service Documentation

The admin service is a FastAPI-based REST API that provides service discovery
and management capabilities for the DTaaS workspace.

## Overview

The service runs on port 8091 (configurable via `ADMIN_SERVER_PORT` environment
variable) and is proxied through nginx. It supports path prefixes for multi-user
deployments, allowing routes to be accessible at `/{path-prefix}/services`.

## Endpoints

### GET /services

Returns a JSON object containing information about all available workspace
services.

**Request**:

```bash
# Without path prefix
curl http://localhost:8080/services

# With path prefix
curl http://localhost:8080/{path-prefix}/services
```

**Response**: Status 200 OK

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

**Notes**:
- Service endpoints are relative paths that should be appended to the base
  workspace URL
- When using path prefixes, the prefix is automatically prepended to all routes
- Empty string endpoints indicate the service is available at the root path

### GET /health

Health check endpoint for monitoring service availability.

**Request**:

```bash
# Without path prefix
curl http://localhost:8091/health

# With path prefix  
curl http://localhost:8091/{path-prefix}/health
```

**Response**: Status 200 OK

```json
{
  "status": "healthy"
}
```

### GET /

Root endpoint providing service metadata and available endpoints.

**Request**:

```bash
# Without path prefix
curl http://localhost:8091/

# With path prefix
curl http://localhost:8091/{path-prefix}
```

**Response**: Status 200 OK

```json
{
  "service": "Workspace Admin Service",
  "version": "0.1.1",
  "endpoints": {
    "/services": "Get list of available workspace services",
    "/health": "Health check endpoint"
  }
}
```

## Architecture

### Service Discovery Flow

1. User accesses `http://{domain}/{path-prefix}/services` (or `/services` without prefix)
2. nginx receives the request and routes it to the admin service on port 8091
3. Admin service reads the `config/services_template.json` file
4. JSON response is returned to the client
5. Path prefix is configured via CLI argument when starting the service

### Package Layout

The package is split by layer, so that transport concerns stay separate from
the logic they expose:

```text
src/admin/
├── __init__.py                   # Package marker
├── api.py                        # FastAPI app factory and HTTP routes
├── services.py                   # Workspace service discovery
├── main.py                       # Command-line entry point
├── config/
│   ├── services_template.json    # Service catalogue (data, not code)
│   └── config.env.example        # Example git asset configuration
└── git/
    ├── __init__.py                # Git backup of workspace directories
    ├── config.py                  # Reads config.env into RepoConfig objects
    ├── clone.py                   # Clones a single RepoConfig
    └── bootstrap.py               # Clones the shared "common" asset on startup
```

Dependencies point in one direction only: `main` → `api` → `services`. No
module imports the layer above it, so each can be tested on its own.

### Components

- **HTTP API** (`src/admin/api.py`): FastAPI application factory
  (`create_app`), the router holding the `/`, `/services` and `/health`
  routes, path prefix normalization, and `APP_VERSION`. Contains transport
  logic only
- **Service Discovery** (`src/admin/services.py`): `load_services()`, which
  reads the service catalogue from the JSON template
- **CLI Entry Point** (`src/admin/main.py`): Argument parsing
  (`build_parser`) and the `workspace-admin` command (`cli`), which either
  prints the catalogue or serves the API with uvicorn
- **Git Backup** (`src/admin/git/`): Configuration, cloning, authentication
  and working tree synchronization for shared and private git assets
  - `config.py`: `load_config()` reads the TOML-formatted `config.env` file
    at `$WORKSPACE_APP_DIR/config.env` and returns a `RepoConfig` per
    `[assets.<name>]` table (currently `private` and `common`)
  - `clone.py`: `clone_asset()` clones one `RepoConfig` with a separated
    git directory and working tree (`git clone --separate-git-dir`),
    skipping repositories that are already cloned and raising
    `CloneError` on failure. Credentials, when set, are passed as a
    one-off `http.extraHeader` so they are never written into
    `.git/config`
  - `bootstrap.py`: `clone_common_repo()` wires the two together for the
    shared `common` asset and is called once from `admin.main.cli()` on
    startup. Errors are logged, never raised, so a missing or broken git
    configuration does not prevent the admin service from starting.
    Cloning the `private` asset is out of scope here - it belongs to the
    authentication feature built on top of this same `config.py` contract
- **Services Template** (`src/admin/config/services_template.json`): JSON
  template defining available services
- **nginx Configuration** (`startup/nginx.conf`): Reverse proxy routing
- **Startup Script** (`startup/custom_startup.sh`): Service bootstrap and
  monitoring

## Environment Variables

- `ADMIN_SERVER_PORT`: Port for the admin service (default: `8091`)
- `PATH_PREFIX`: Optional path prefix for API routes (can also be set via CLI `--path-prefix` argument)
- `WORKSPACE_APP_DIR`: Directory that holds `config.env`, the git asset
  configuration file read by `admin.git.bootstrap.clone_common_repo()` on
  startup (default: current directory)

## Development

### Running Tests

```bash
cd workspaces/src/admin
poetry install
poetry run pytest --cov=admin --cov-report=html --cov-report=term-missing
```

The test files mirror the package layout, so a change to a module has one
obvious test file:

| Test file             | Module under test  | Covers                                     |
| --------------------- | ------------------ | ------------------------------------------ |
| `tests/test_api.py`   | `admin/api.py`     | Routes, responses, path prefix handling     |
| `tests/test_services.py` | `admin/services.py` | Catalogue loading and template integrity |
| `tests/test_main.py`  | `admin/main.py`    | Argument parsing and CLI flags              |
| `tests/test_git_clone.py` | `admin/git/clone.py` | Successful clone, already-cloned skip, failed clone, credential handling |
| `tests/test_git_bootstrap.py` | `admin/git/bootstrap.py` | Startup wiring: cloning `common`, and graceful handling of config/clone failures |

### Code Quality and Coverage

**Run pylint for code quality analysis:**

```bash
cd workspaces/src/admin
poetry run pylint src/admin tests
```

**Run tests with coverage analysis:**

```bash
cd workspaces/src/admin
poetry run pytest --cov=admin --cov-report=html --cov-report=term
```

This will generate a coverage report in `htmlcov/index.html` and display a
summary in the terminal.

### Running Locally

**As a service:**

```bash
cd workspaces/src/admin
export ADMIN_SERVER_PORT=8091
poetry run workspace-admin --path-prefix dtaas-user
```

**As a CLI utility:**

```bash
cd workspaces/src/admin
poetry install

# Run the service
poetry run workspace-admin

# Run with custom host and port
poetry run workspace-admin --host 127.0.0.1 --port 9000

# Run with path prefix for multi-user deployments
poetry run workspace-admin --path-prefix dtaas-user

# List services without starting the server
poetry run workspace-admin --list-services

# Run with auto-reload for development
poetry run workspace-admin --reload

# Show help
poetry run workspace-admin --help
```

The CLI interface makes the admin service work like a system utility similar to
glances, allowing easy command-line operation and service listing.

### Adding New Services

To add a new service to the workspace:

1. Update `config/services_template.json` with the new service definition:

```json
{
  "new_service": {
    "name": "New Service Name",
    "description": "Description of the service",
    "endpoint": "path/to/service"
  }
}
```

2. The service automatically reads and processes the template - no code changes required

## Integration with DTaaS

The `/services` endpoint enables the DTaaS frontend to:

1. Dynamically discover available workspace services
2. Display service shortcuts to users
3. Support different workspace configurations without hardcoded service lists
4. Handle multi-user deployments where each user has different available
   services

This replaces the previous approach of hardcoding service endpoints in the
frontend configuration, enabling more flexible workspace deployments.

## Future Enhancements

Potential future enhancements include:

- Service registry for dynamic service registration
- Authentication and authorization integration
- Service health monitoring and status reporting
- Custom service definitions per workspace type
- Web application firewall integration for zero-trust security
