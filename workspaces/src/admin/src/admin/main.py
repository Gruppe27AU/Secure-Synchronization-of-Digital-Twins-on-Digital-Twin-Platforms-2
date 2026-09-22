"""
Command-line entry point for the workspace admin service.

This module wires the command-line interface to the application layers: it
parses arguments, and then either prints the service catalogue
(:mod:`admin.services`) or serves the HTTP API (:mod:`admin.api`) with
uvicorn. It contains no routing and no service discovery logic itself.
"""

import argparse
import json
import os
import sys

import uvicorn

from admin.api import APP_VERSION, create_app
from admin.git.bootstrap import clone_common_repo
from admin.services import load_services


def build_parser() -> argparse.ArgumentParser:
    """
    Build the argument parser for the ``workspace-admin`` command.

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


def print_startup_banner(host: str, port: int, prefix_display: str) -> None:
    """
    Print the endpoints the service is about to serve.

    Args:
        host: Host the service binds to.
        port: Port the service binds to.
        prefix_display: Normalized path prefix, empty when unprefixed.
    """
    print(f"Starting Workspace Admin Service on {host}:{port}")
    print("Service endpoints:")
    print(f"  - http://{host}:{port}{prefix_display}/services")
    print(f"  - http://{host}:{port}{prefix_display}/health")
    print(f"  - http://{host}:{port}{prefix_display}/")


def cli() -> None:
    """
    Command-line interface for the workspace admin service.

    This allows the service to be run as a standalone utility
    similar to glances.
    """
    args = build_parser().parse_args()

    # Set up path prefix
    path_prefix = args.path_prefix.strip("/")
    prefix_display = f"/{path_prefix}" if path_prefix else ""

    if args.list_services:
        # Just list services and exit
        print(json.dumps(load_services(), indent=2))
        sys.exit(0)

    clone_common_repo()

    print_startup_banner(args.host, args.port, prefix_display)

    uvicorn.run(
        create_app(path_prefix),
        host=args.host,
        port=args.port,
        reload=args.reload
    )


if __name__ == "__main__":
    cli()
