"""
Git backup of the workspace directories.

This package is the home for the git logic of the workspace admin service:
reading the git configuration, cloning the configured repositories,
authenticating against the remotes and keeping the working trees in sync.

It is deliberately empty for now. The package exists so that the git logic
lands next to, and not inside, the HTTP layer in :mod:`admin.api`.
"""
