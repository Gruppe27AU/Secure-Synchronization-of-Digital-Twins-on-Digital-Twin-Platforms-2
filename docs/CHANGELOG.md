# Purpose

The main changes made so far are listed here.

## Week of 07-Sep-2026

### Changed

* Split the workspace-admin `main.py` into layered modules ahead of the git
  backup feature: `api.py` (FastAPI app factory and HTTP routes),
  `services.py` (workspace service discovery) and `main.py` (CLI entry point)
* Moved `services_template.json` into `src/admin/config/` and added an empty
  `src/admin/git/` package as the home for the upcoming git backup logic
* Split `tests/test_main.py` into `test_api.py`, `test_services.py` and
  `test_main.py` to mirror the new module layout
* CLI now passes the application straight to uvicorn instead of rebinding a
  module-level global, removing the last inline pylint suppression
* Version is read from the package metadata rather than a literal in the
  code, leaving `pyproject.toml` as the only place it is declared
* Trimmed the CLI command list duplicated between the admin service
  `README.md` and `DOCUMENTATION.md`, and added `coverage.xml` to
  `.gitignore`
* Behaviour is unchanged: no endpoints, flags or responses were added,
  removed or modified

### Documentation

* Added a `docs/` directory for repository-level documentation and moved
  `CHANGELOG.md`, `PUBLISHING.md`, `CODE_OF_CONDUCT.md`,
  `PENDING_ISSUES.md` and `POTENTIAL_IMPROVEMENTS.md` into it
* The repository root now holds only `README.md`, `LICENSE.md` and the
  agent instruction files, which have to stay there to be found by GitHub
  and by the coding agents that read them
* Component documentation stays beside the code it describes, so the
  deployment guides remain in `workspaces/test/dtaas/` and the admin
  service documentation in `workspaces/src/admin/`

## Week of 06-April-2026

### Changed

* Changed the Docker build workflows to use only the GitHub Actions cache
  (`type=gha`) for build layer reuse across repeated runs (previously also cached to the GHCR)
* Consolidated test of Docker compose files and Docker image into one GitHub Action
* Updated Actions to work with Keycloak Auth compose files
* Added GitHub Actions test of the basic Docker compose file, for testing actual Docker image functionality
* Removed outdated references to deleted Traefik-specific workflow files and
  documented the cache mechanism actually used in this PR

## Week of 17-Mar-2026

### Added

* Lightweight workspace image `Dockerfile.ubuntu.noble.xfce` based on
  `kasmweb/core-ubuntu-noble:1.18.0` (Ubuntu 24.04 + XFCE desktop)
* The new image uses a lighter XFCE desktop manager, making it suitable
  for low-resource environments
* GitHub Actions coverage was updated to validate the workspace image and
  related Docker compose setup used by this repository

## Week of 03-Mar-2026

### Changed

* GitHub Actions build cache optimised to reduce execution time on repeated runs
  * Added GHCR registry cache (`type=registry`) alongside GitHub Actions cache
    (`type=gha`) in all Docker build workflows for persistent cross-run
    and cross-workflow layer caching
  * Extracted a dedicated `build-workspace-image` job in
    `traefik-secure-test.yml` so the workspace image is built once per
    architecture and reused by both `test-traefik-secure` and
    `test-traefik-secure-tls`, reducing full builds from four to two per run
  * Test jobs now use read-only cache (`cache-from` only) after the build
    job has populated the cache, further reducing write contention
  * Fixed inconsistent `docker/setup-buildx-action` version in
    `test-traefik-secure-tls` (updated from v3.11.1 to v3.12.0)

## Week of 10-Feb-2026

### Added
* Admin service FastAPI application with `/services` endpoint for service discovery
* CLI interface for admin service (`workspace-admin` command) with options for listing services, host, port, and reload
* Comprehensive test suite for admin service with 84% coverage (14 tests)
* Detailed documentation (DOCUMENTATION.md and README.md) for admin service

### Changed
* Admin service installation changed from poetry run to wheel package + pipx installation
* Poetry installation now uses install.python-poetry.org installer with virtualenvs configured in-project

### Fixed
* ShellCheck issues in install_admin.sh (test syntax, variable bracing, error handling)
* Docker build path from `${INST_DIR}/../../admin` to `${INST_DIR}/../admin`
* Linting issues: removed whitespace, unused imports, fixed f-strings
* Type hints for FastAPI response endpoints
* Mistakes in workspace admin installation

## Week of 03-Feb-2026

### Changed
* Workspace Docker image now published to `intocps/workspace` registry

## 21-Jan-2026

* **Keycloak Integration**: Added Keycloak as the default identity provider for authentication
  * Replaced GitLab OAuth with OIDC-based authentication
  * New `keycloak` service in `compose.traefik.secure.yml`
  * Updated `traefik-forward-auth` to use OIDC provider
  * Added persistent volume for Keycloak data
* **Environment Configuration**: Updated `.env.example` with Keycloak-specific variables
  * Maintained backward compatibility with GitLab OAuth
  * Added comprehensive comments for both authentication methods
* **Documentation**: Created comprehensive setup and migration guides
  * [KEYCLOAK_SETUP.md](KEYCLOAK_SETUP.md) - Detailed Keycloak configuration
  * [KEYCLOAK_MIGRATION.md](KEYCLOAK_MIGRATION.md) - Migration guide from GitLab
  * Updated [CONFIGURATION.md](CONFIGURATION.md) to reference Keycloak
  * Updated [TRAEFIK_SECURE.md](TRAEFIK_SECURE.md) with Keycloak instructions
* **Flexibility**: Designed for minimal changes when moving Keycloak external
  * Environment variable based configuration
  * Easy to switch between internal and external Keycloak
  * Can still use GitLab OAuth with minor compose file modifications
* Docker labels moved from build stage to deploy stage in Dockerfile

## Week of 13-Jan-2026

### Added
* TLS/HTTPS support with OAuth2 authentication for production deployments
* New Docker compose files for TLS configuration
* Self-signed certificate generation support
* `.gitattributes` file specifying LF line endings for all non-binary files

### Changed
* Workspace name changed from `workspace-nouveau` to `workspace`
* GitHub Actions updated to reflect new image location
* Traefik-forward-auth version updated to fix endless redirect loop bug
* Consolidated environment file setup between OAuth2 and TLS features

### Fixed
* Docker image publish problems
* Regular user set for login user

## Week of 06-Jan-2026

### Added
* Automated Docker image publishing to GHCR and Docker Hub
* OCI labels to Dockerfile for better metadata
* PUBLISHING.md documentation for Docker image publishing workflow
* CLAUDE.md file for Claude code use

### Changed
* Main image name from "workspace-nouveau" to "workspace"

## Week of 16-Dec-2025

### Added
* Traefik reverse proxy integration for multi-user deployments
* OAuth2-secured multi-user deployment with traefik-forward-auth and DTaaS web client integration
* Strict linting enforcement in GitHub Actions workflows
* New project structure with dedicated DTaaS testing directory
* Configuration and certificates organization in dedicated DTaaS directory

### Fixed
* Resolved Copilot review comments from PR #10

### Changed
* Improved documentation for multi-user deployments

## 15-Dec-2025

* Adds both ml-workspace and workspace in one docker compose
  and puts them behind traefik proxy
* Based on the KASM core ubuntu image.
* Added VSCode service with [code-server](https://github.com/coder/code-server),
  is started by the [custom_startup.sh](/startup/custom_startup.sh) script.
* Jupyter is available.
* No longer need to authenticate when opening VNC Desktop.
* User is now a sudoer, can install debian packages, and user password
  can be set at container instantiation (via the environment variable USER_PW).
* All access to services is over http (VNC https is hidden behind reverse proxy).
* Reverse proxy exists, and VNC's websocket is forced to adchere to path structure with 'path' argument as path of http request.
* Still need to get image under 500 MB.
