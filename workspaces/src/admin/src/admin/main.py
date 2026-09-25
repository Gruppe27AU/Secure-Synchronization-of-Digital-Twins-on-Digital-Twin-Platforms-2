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

import uvicorn

from admin.api import APP_VERSION, create_app, normalize_path_prefix
from admin.git.bootstrap import clone_configured_repos, start_git_sync
from admin.git.scheduler import DEFAULT_SYNC_INTERVAL_SECONDS
from admin.services import load_services

#: Format of the log records written to the console by the git backup.
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def _positive_seconds(value: str) -> int:
    """
    Validate ``--sync-interval`` as argparse parses it.

    Args:
        value: The raw command-line argument.

    Returns:
        The interval in seconds.

    Raises:
        argparse.ArgumentTypeError: If the value is not a whole number of
            seconds above zero. Zero would turn the backup into a loop
            that never waits, hammering the remote.
    """
    try:
        seconds = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"expected a whole number of seconds, got '{value}'"
        ) from error

    if seconds < 1:
        raise argparse.ArgumentTypeError(
            f"expected at least 1 second, got {seconds}"
        )

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
        default=DEFAULT_SYNC_INTERVAL_SECONDS,
        help=(
            "Seconds between git backups of the workspace "
            f"(default: {DEFAULT_SYNC_INTERVAL_SECONDS})"
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
    """
    Send the service's log records to the console.
    
    """
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)


def _print_service_catalogue() -> None:
    """Print the service catalogue as JSON, for ``--list-services``."""
    print(json.dumps(load_services(), indent=2))


def _start_git_backup(interval_seconds: int) -> None:
    """
    Bring the git-backed workspace assets up before the server starts.

    Clones whatever the git config describes and is not on disk yet, then
    leaves a background thread committing and pushing those clones. Both
    steps report their own failures and never raise, so a broken git
    configuration delays nothing and the HTTP API still comes up.

    Args:
        interval_seconds: Seconds to wait between backups.
    """
    clone_configured_repos()
    start_git_sync(interval_seconds)


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
    print(f"  - http://{host}:{port}{prefix}/")


def _serve(args: argparse.Namespace) -> None:
    """
    Build the application and serve it until the process is stopped.

    Args:
        args: The parsed command-line arguments.
    """
    _print_startup_banner(
        args.host, args.port, normalize_path_prefix(args.path_prefix)
    )

    uvicorn.run(
        create_app(args.path_prefix),
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

    _start_git_backup(args.sync_interval)
    _serve(args)


if __name__ == "__main__":
    cli()
