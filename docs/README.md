# Documentation

Repository-level documentation. Documentation for a specific component
lives beside the code it describes, not here.

## In this directory

| Document | Contents |
| -------- | -------- |
| [CHANGELOG.md](CHANGELOG.md) | Version history, newest first |
| [PUBLISHING.md](PUBLISHING.md) | Docker image publishing and registries |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Contributor code of conduct |
| [PENDING_ISSUES.md](PENDING_ISSUES.md) | Known issues awaiting work |
| [POTENTIAL_IMPROVEMENTS.md](POTENTIAL_IMPROVEMENTS.md) | Improvement backlog |

## Elsewhere in the repository

| Document | Why it lives there |
| -------- | ------------------ |
| [README.md](../README.md) | GitHub renders it as the landing page |
| [LICENSE.md](../LICENSE.md) | GitHub detects the licence at the root |
| [AGENTS.md](../AGENTS.md), [CLAUDE.md](../CLAUDE.md) | Coding agents load them from the root |
| [.github/copilot-instructions.md](../.github/copilot-instructions.md) | Copilot reads this exact path |

Component documentation:

| Document | Covers |
| -------- | ------ |
| [workspaces/DEVELOPER.md](../workspaces/DEVELOPER.md) | Building and developing the image |
| [workspaces/src/admin/README.md](../workspaces/src/admin/README.md) | Admin service overview (also the package readme in `pyproject.toml`) |
| [workspaces/src/admin/DOCUMENTATION.md](../workspaces/src/admin/DOCUMENTATION.md) | Admin service endpoints and architecture |
| [workspaces/test/dtaas/CONFIGURATION.md](../workspaces/test/dtaas/CONFIGURATION.md) | Configuring the DTaaS compositions |
| [workspaces/test/dtaas/SINGLE_USER.md](../workspaces/test/dtaas/SINGLE_USER.md) | Single-user deployment |
| [workspaces/test/dtaas/TRAEFIK.md](../workspaces/test/dtaas/TRAEFIK.md) | Multi-user deployment over HTTP |
| [workspaces/test/dtaas/TRAEFIK_SECURE.md](../workspaces/test/dtaas/TRAEFIK_SECURE.md) | Multi-user with OAuth2 |
| [workspaces/test/dtaas/TRAEFIK_TLS.md](../workspaces/test/dtaas/TRAEFIK_TLS.md) | Production deployment with HTTPS |
| [workspaces/test/dtaas/KEYCLOAK_SETUP.md](../workspaces/test/dtaas/KEYCLOAK_SETUP.md) | Keycloak setup, migration and quick reference |
