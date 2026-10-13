"""
HTTP API for the workspace admin service.

This module owns the transport layer only: it builds the FastAPI
application, declares the routes and translates results into responses.
Any logic behind a route lives in a sibling module (for example
:mod:`admin.services` for service discovery), so that the routing layer
stays thin and both layers can be tested independently.

Every route is read-only. The API is served without authentication on all
interfaces, so nothing here may change state or reveal a secret; in
particular there is no route that triggers a git backup.
"""

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

from admin.git.status import SyncStatus
from admin.services import load_services

try:
    # Read the version from the installed distribution metadata, so that
    # pyproject.toml stays the single place where it is declared.
    APP_VERSION = version("workspace-admin")
except PackageNotFoundError:  # pragma: no cover - source tree, not installed
    APP_VERSION = "0.0.0+unknown"

APP_TITLE = "Workspace Admin Service"
APP_DESCRIPTION = "Service discovery and management for DTaaS workspace"


def normalize_path_prefix(path_prefix: str) -> str:
    """
    Normalize a user supplied path prefix into a router prefix.

    Args:
        path_prefix: Raw prefix, with or without surrounding slashes
            (e.g. ``"dtaas-user"``, ``"/dtaas-user/"`` or ``""``).

    Returns:
        The prefix as a single leading slash followed by the trimmed value,
        or an empty string when no prefix is configured.
    """
    trimmed = path_prefix.strip("/")
    return f"/{trimmed}" if trimmed else ""


def create_router(sync_status: SyncStatus) -> APIRouter:
    """
    Create the router holding the admin service endpoints.

    Args:
        sync_status: Outcome of the git backups, served by ``/git/status``.

    Returns:
        Router exposing the ``/``, ``/services``, ``/health`` and
        ``/git/status`` routes.
    """
    router = APIRouter()

    @router.get("/")
    async def root() -> dict[str, Any]:
        """Root endpoint providing service information."""
        return {
            "service": APP_TITLE,
            "version": APP_VERSION,
            "endpoints": {
                "/services": "Get list of available workspace services",
                "/health": "Health check endpoint",
                "/git/status": "Outcome of the most recent git backups"
            }
        }

    @router.get("/services")
    async def get_services() -> JSONResponse:
        """
        Get list of available workspace services.

        Returns:
            JSONResponse containing service information.
        """
        return JSONResponse(content=load_services())

    @router.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "healthy"}

    @router.get("/git/status")
    async def git_status() -> dict[str, Any]:
        """
        Report how the git backup of each repository went last.

        Kept apart from ``/health`` on purpose: a failing backup must not
        make an orchestrator restart a workspace that is otherwise fine.
        Only timestamps and error kinds are returned, never a message.
        """
        return sync_status.snapshot()

    return router


def create_app(
    path_prefix: str = "", sync_status: SyncStatus | None = None
) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        path_prefix: Optional path prefix for all routes (e.g., "dtaas-user")
        sync_status: Outcome of the git backups. Without one, ``/git/status``
            reports the backup as disabled.

    Returns:
        Configured FastAPI application instance.
    """
    fastapi_app = FastAPI(
        title=APP_TITLE,
        description=APP_DESCRIPTION,
        version=APP_VERSION
    )
    fastapi_app.include_router(
        create_router(sync_status or SyncStatus()),
        prefix=normalize_path_prefix(path_prefix)
    )

    return fastapi_app
