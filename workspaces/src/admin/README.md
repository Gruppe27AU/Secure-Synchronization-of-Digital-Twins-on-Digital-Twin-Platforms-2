# Admin Service

FastAPI service for workspace service discovery and management.

## Features

- `/services` endpoint - Returns JSON list of available workspace services
- Path prefix support for multi-user deployments
- Command-line interface for standalone operation

## Layout

```text
src/admin/
├── api.py                        # FastAPI app factory and HTTP routes
├── services.py                   # Workspace service discovery
├── main.py                       # Command-line entry point
├── config/
│   └── services_template.json    # Service catalogue
└── git/                          # Git backup of workspace directories
```

See [DOCUMENTATION.md](DOCUMENTATION.md) for the full architecture and
endpoint reference.

## Running

### As a Service (in workspace container)

The service is automatically started when the workspace container starts.
It runs on port 8091 and is accessible via the nginx reverse proxy at `/services`.

### As a CLI Utility

```bash
poetry install
poetry run workspace-admin --path-prefix dtaas-user
```

`poetry run workspace-admin --help` lists every flag. See
[DOCUMENTATION.md](DOCUMENTATION.md#running-locally) for worked examples.

## Development

```bash
poetry install
poetry run pytest --cov=admin --cov-report=term-missing
poetry run pylint src/admin tests
```

See [DOCUMENTATION.md](DOCUMENTATION.md#development) for the full workflow,
including coverage reports and the checks run in CI.
