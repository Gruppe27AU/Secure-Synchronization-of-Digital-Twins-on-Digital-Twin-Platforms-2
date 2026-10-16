"""
Command-line entry point for the workspace admin service.

This module is the service's startup sequence and nothing else. It owns no
routing and no service discovery of its own; it only decides, from the
command line, which of the other layers to run:

- :func:`build_parser` declares the command-line surface.
- :func:`cli` runs the startup sequence: either print the service
  catalogue (:mod:`admin.services`) and exit, or bring up the git backup
  (:mod:`admin.git.bootstrap`) and serve the HTTP API (:mod:`admin.api`)
  with uvicorn.

Everything else in this module is a step of that sequence, split out so
each step has a name saying what it does.
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
from admin.git.bootstrap import start_git_backup, stop_git_backup
from admin.git.config import (
    DEFAULT_SYNC_INTERVAL_SECONDS,
    SYNC_INTERVAL_KEY,
    sync_interval_problem,
)
from admin.git.status import SyncStatus
from admin.services import load_services

#: Format of the service's own log records, matching uvicorn's
#: (``INFO:     message``, the level coloured on a terminal).
LOG_FORMAT = "%(levelprefix)s %(message)s"


def _positive_seconds(value: str) -> int:
    """
    Validate ``--sync-interval`` as argparse parses it.

    Args:
        value: The raw command-line argument.

    Returns:
        The interval in seconds.

    Raises:
        argparse.ArgumentTypeError: If the value is not a whole number of
            seconds in the range the config file accepts too. Zero would
            turn the backup into a loop that never waits, hammering the
            remote.
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
    """
    Declare the command-line surface of ``workspace-admin``.

    Returns:
        Parser configured with all supported command-line options.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Workspace Admin Service - "
            "Service discovery for DTaaS workspaces"
        )
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind the service to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("ADMIN_SERVER_PORT", "8091")),
        help=(
            "Port to bind the service to "
            "(default: $ADMIN_SERVER_PORT or 8091)"
        )
    )
    parser.add_argument(
        "--path-prefix",
        default=os.getenv("PATH_PREFIX", "dtaas-user"),
        help=(
            "Path prefix for API routes "
            "(e.g., 'dtaas-user' for routes at /dtaas-user/services)"
        )
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development"
    )
    parser.add_argument(
        "--sync-interval",
        type=_positive_seconds,
        default=None,
        help=(
            "Seconds between git backups of the workspace, overriding "
            f"{SYNC_INTERVAL_KEY} in config.toml "
            f"(default: the config file's value, or {DEFAULT_SYNC_INTERVAL_SECONDS})"
        )
    )
    parser.add_argument(
        "--list-services",
        action="store_true",
        help="List available services and exit"
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s " + APP_VERSION
    )

    return parser


def _configure_logging() -> None:
    """Send the service's log records to the console, formatted like uvicorn's."""
    handler = logging.StreamHandler()
    handler.setFormatter(DefaultFormatter(LOG_FORMAT))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def _print_service_catalogue() -> None:
    """Print the service catalogue as JSON, for ``--list-services``."""
    print(json.dumps(load_services(), indent=2))


def _start_git_backup(
    interval_seconds: int | None, status: SyncStatus
) -> Callable[[], bool]:
    """
    Start bringing the git-backed workspace assets up, without waiting.

    Clones whatever the git config describes and is not on disk yet, then
    leaves a thread committing and pushing those clones. All of it happens
    on a background thread and reports its own failures, so neither a
    broken git configuration nor a slow or unreachable remote delays the
    HTTP API.

    Args:
        interval_seconds: Seconds to wait between backups, from
            ``--sync-interval``. None uses ``SYNC_INTERVAL_SECONDS`` from
            the config file.
        status: Where the outcome of every backup is recorded.

    Returns:
        Stops the backup, waiting briefly for the git command it is in, so
        a shutdown does not cut it off halfway.
    """
    stop_event = threading.Event()
    thread = start_git_backup(status, interval_seconds, stop_event)
    return lambda: stop_git_backup(thread, stop_event)


def _print_startup_banner(host: str, port: int, prefix: str) -> None:
    """
    Print the endpoints the service is about to serve.

    Args:
        host: Host the service binds to.
        port: Port the service binds to.
        prefix: Normalized path prefix, empty when unprefixed.
    """
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
    Build the application and serve it until the process is stopped.

    Args:
        args: The parsed command-line arguments.
        status: Outcome of the git backups, served by ``/storage/status``.
        on_shutdown: Called when the server stops, before the process exits.
    """
    uvicorn.run(
        create_app(args.path_prefix, status, on_shutdown),
        host=args.host,
        port=args.port,
        reload=args.reload
    )


def cli() -> None:
    """
    Run the workspace admin service from the command line.

    Parses the arguments and then takes one of two routes: ``--list-services``
    prints the catalogue and exits without starting anything, and otherwise
    the git backup is brought up and the HTTP API is served.
    """
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
