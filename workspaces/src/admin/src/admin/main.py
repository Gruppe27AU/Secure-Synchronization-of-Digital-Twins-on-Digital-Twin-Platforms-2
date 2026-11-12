"""
Command-line entry point of the workspace admin service.

:func:`cli` either prints the service catalogue (``--list-services``), or
starts the git backup (:mod:`admin.git.service.bootstrap`) and serves the HTTP API
(:mod:`admin.api`) with uvicorn.
"""

import argparse
import json
import logging
import os
import sys
import threading
from collections.abc import Callable

import uvicorn
from uvicorn.logging import DefaultFormatter

from admin.api import APP_VERSION, create_app, normalize_path_prefix
from admin.git.service.bootstrap import start_git_backup, stop_git_backup
from admin.git.setup.config import DEFAULT_SYNC_INTERVAL_SECONDS, SYNC_INTERVAL_KEY
from admin.git.service.status import SyncStatus
from admin.git.setup.validate import sync_interval_problem
from admin.services import load_services

#: Format of the service's log records, as uvicorn's (``INFO:     message``).
LOG_FORMAT = "%(levelprefix)s %(message)s"


def _positive_seconds(value: str) -> int:
    """
    Parse ``--sync-interval``, accepting what the config file accepts.

    Raises:
        argparse.ArgumentTypeError: If it is not such a number of seconds.
    """
    try:
        seconds = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"expected a whole number of seconds, got '{value}'"
        ) from error
    problem = sync_interval_problem(seconds)
    if problem:
        raise argparse.ArgumentTypeError(problem)
    return seconds


def build_parser() -> argparse.ArgumentParser:
    """Declare the command-line options of ``workspace-admin``."""
    parser = argparse.ArgumentParser(
        description="Workspace Admin Service - Service discovery for DTaaS workspaces"
    )
    parser.add_argument("--host", default="0.0.0.0",
                        help="Host to bind the service to (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=int(os.getenv("ADMIN_SERVER_PORT", "8091")),
                        help="Port to bind the service to (default: $ADMIN_SERVER_PORT or 8091)")
    parser.add_argument("--path-prefix", default=os.getenv("PATH_PREFIX", "dtaas-user"),
                        help="Path prefix for API routes "
                             "(e.g., 'dtaas-user' for routes at /dtaas-user/services)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument("--sync-interval", type=_positive_seconds, default=None,
                        help=f"Seconds between git backups of the workspace, overriding "
                             f"{SYNC_INTERVAL_KEY} in config.toml (default: the config "
                             f"file's value, or {DEFAULT_SYNC_INTERVAL_SECONDS})")
    parser.add_argument("--list-services", action="store_true",
                        help="List available services and exit")
    parser.add_argument("--version", action="version", version="%(prog)s " + APP_VERSION)
    return parser


def _configure_logging() -> None:
    """Log to the console, formatted like uvicorn."""
    handler = logging.StreamHandler()
    handler.setFormatter(DefaultFormatter(LOG_FORMAT))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def _print_service_catalogue() -> None:
    """Print the service catalogue as JSON."""
    print(json.dumps(load_services(), indent=2))


def _start_git_backup(interval_seconds: int | None, status: SyncStatus) -> Callable[[], bool]:
    """
    Start the git backup on a background thread, without waiting for it.

    Args:
        interval_seconds: From ``--sync-interval``; None uses the config file.

    Returns:
        A function that stops the backup, waiting briefly for git.
    """
    stop_event = threading.Event()
    thread = start_git_backup(status, interval_seconds, stop_event)
    return lambda: stop_git_backup(thread, stop_event)


def _print_startup_banner(host: str, port: int, prefix: str) -> None:
    """Print the endpoints about to be served; ``prefix`` is normalized."""
    print(f"Starting Workspace Admin Service on {host}:{port}")
    print("Service endpoints:")
    print(f"  - http://{host}:{port}{prefix}/services")
    print(f"  - http://{host}:{port}{prefix}/health")
    print(f"  - http://{host}:{port}{prefix}/storage/status")
    print(f"  - http://{host}:{port}{prefix}/")


def _serve(
    args: argparse.Namespace,
    status: SyncStatus,
    on_shutdown: Callable[[], object],
) -> None:
    """
    Serve the HTTP API until the process stops.

    Args:
        status: Served by ``/storage/status``.
        on_shutdown: Called when the server stops.
    """
    uvicorn.run(
        create_app(args.path_prefix, status, on_shutdown),
        host=args.host,
        port=args.port,
        reload=args.reload
    )


def cli() -> None:
    """Print the catalogue for ``--list-services``; otherwise start the backup and serve."""
    args = build_parser().parse_args()
    _configure_logging()

    if args.list_services:
        _print_service_catalogue()
        sys.exit(0)

    # Printed before the backup starts, so its log lines cannot land inside.
    _print_startup_banner(
        args.host, args.port, normalize_path_prefix(args.path_prefix)
    )
    status = SyncStatus()
    stop_backup = _start_git_backup(args.sync_interval, status)
    _serve(args, status, stop_backup)


if __name__ == "__main__":
    cli()
