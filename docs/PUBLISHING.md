# Publishing Workspace Docker Images

This document explains how the workspace Docker image publishing workflow operates
and what configuration is required.

## Overview

The workspace Docker image is automatically published to two container
registries when all quality checks pass:

- **GitHub Container Registry (GHCR)**: `ghcr.io/into-cps-association/workspace`
- **Docker Hub**: `intocps/workspace`

## Workflow Trigger

The publish workflow (`.github/workflows/workspace-publish.yml`) is triggered
automatically when all of the following workflows complete successfully on the
`main` branch:

1. `Dockerfile Lint and Build` - Ensures Dockerfile quality and builds successfully
2. `Lint Scripts` - Validates shell, Python, and YAML code quality
3. `Test Traefik Configuration` - Tests multi-user deployment scenarios

The workflow can also be triggered manually via `workflow_dispatch` for testing
or emergency releases.

## Required Configuration

### Docker Hub Secrets

Before the workflow can publish to Docker Hub, repository administrators must
configure these secrets in GitHub repository settings
(Settings → Secrets and variables → Actions):

1. **DOCKERHUB_USERNAME**
   - The Docker Hub username or organization name
   - Example: `intocpsassociation`
   - How to get: Your Docker Hub account username

2. **DOCKERHUB_TOKEN**
   - A Docker Hub access token with write permissions
   - **DO NOT use your Docker Hub password**
   - How to create:
     1. Log in to [Docker Hub](https://hub.docker.com)
     2. Go to Account Settings → Security → Access Tokens
     3. Click "New Access Token"
     4. Give it a descriptive name (e.g., "GitHub Actions - workspace")
     5. Set permissions to "Read & Write"
     6. Copy the generated token (shown only once)

3. **DOCKERHUB_SCOPE**
   - The Dockerhub scope that the image should be saved in.
   - Example: if it should be saved in `hub.docker.com/r/intocps/workspace`, then `intocps` would be the scope.

### Docker Hub Repository

Create a repository on Docker Hub to receive the published images:

1. Log in to Docker Hub
2. Click "Create Repository"
3. Set repository name: `workspace`
4. Set visibility: Public (recommended) or Private
5. Add description: "Containerized virtual desktop environment with KasmVNC,
   Firefox, Jupyter, and VS Code Server for DTaaS"

### GitHub Container Registry

No additional configuration is required for GHCR. The workflow uses the built-in
`GITHUB_TOKEN` which automatically has permission to publish packages to the
repository's container registry.

## What Gets Published

The workflow publishes multi-platform Docker images supporting the following
architectures:

- **linux/amd64** - For Intel and AMD x86_64 processors
- **linux/arm64** - For ARM64 processors (Apple Silicon, AWS Graviton, etc.)

Each published image includes the following tags:

- `latest` - Most recent successful build from the main branch
- `main-<sha>` - Build from specific commit on main branch
- `main` - Latest build from the main branch

Docker will automatically select the appropriate architecture when pulling images.
For example, `docker pull intocps/workspace:latest` will fetch the arm64 version
on an Apple M1/M2/M3 Mac and the amd64 version on an Intel/AMD system.

## Image Testing

After publishing, the workflow automatically:

1. Pulls the published multi-platform image from both registries
2. Runs the complete Traefik integration test suite
3. Validates that services start correctly
4. Tests workspace routing through Traefik reverse proxy

This ensures that published images are functional and match local builds.

**Note**: The test suite currently runs on linux/amd64 runners. Both
architectures are built, tested and published. The arm64 variant uses QEMU emulation during testing.

## Manual Publishing

To manually trigger the publishing workflow:

1. Go to Actions → Publish Workspace Docker Image
2. Click "Run workflow"
3. Select the branch (usually `main`)
4. Optionally specify a version tag
5. Click "Run workflow"

## Image Labels

Published images include OCI-compliant metadata labels:

- `org.opencontainers.image.source` - GitHub repository URL
- `org.opencontainers.image.description` - Image description
- `org.opencontainers.image.licenses` - GPL-3.0
- `org.opencontainers.image.version` - Version number
- And more...

These labels help users understand what the image contains and where it came from.

## Using Published Images

Users can pull and use the published images without cloning this repository:

```bash
# From GitHub Container Registry
docker pull ghcr.io/into-cps-association/workspace:latest

# From Docker Hub
docker pull intocps/workspace:latest
```

See [README.md](README.md) and [TRAEFIK.md](TRAEFIK.md) for complete usage
instructions.

## Troubleshooting

### Workflow fails with "Error: Cannot perform an interactive login"

Error message: "Cannot perform an interactive login from a non TTY device"

This means Docker Hub secrets are not configured. Follow the "Required
Configuration" section above to set up the secrets.

### Image not appearing in GitHub Container Registry

1. Check that the workflow completed successfully
2. Go to the repository's Packages tab
3. If the package is private, you may need to make it public:
   - Go to the package settings
   - Change visibility to Public

### Traefik tests fail after publishing

This may indicate an issue with the published image. Check:

1. Workflow logs for the test-published-images job
2. Whether the image was built correctly
3. Service logs in the test output

## Security Considerations

- Never commit Docker Hub credentials directly to the repository
- Use Docker Hub access tokens, not passwords
- Rotate access tokens periodically
- Review workflow logs for any exposed secrets
- Published images are scanned by GitHub's Dependabot

## Version Management

The image version is currently set to `0.1.0` in the Dockerfile labels. To
update the version:

1. Edit the `org.opencontainers.image.version` label in `Dockerfile`
2. Commit and push to main (after PR approval)
3. Workflow will publish the new version

Consider using semantic versioning (MAJOR.MINOR.PATCH) for releases.
