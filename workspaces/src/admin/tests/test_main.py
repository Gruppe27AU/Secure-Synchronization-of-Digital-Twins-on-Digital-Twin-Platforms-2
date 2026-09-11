"""
Unit tests for the command-line entry point.

Covers :mod:`admin.main`: argument parsing and the CLI flags that exit
without starting the HTTP server.
"""

import json
import sys

import pytest

from admin.main import build_parser, cli


def test_cli_list_services(monkeypatch, capsys):
    """Test CLI --list-services flag."""
    monkeypatch.setattr(sys, 'argv', ['workspace-admin', '--list-services'])

    with pytest.raises(SystemExit) as exc_info:
        cli()

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert "desktop" in output
    assert "vscode" in output


def test_cli_version(monkeypatch):
    """Test CLI --version flag."""
    monkeypatch.setattr(sys, 'argv', ['workspace-admin', '--version'])

    with pytest.raises(SystemExit) as exc_info:
        cli()

    # argparse exits with 0 for --version
    assert exc_info.value.code == 0


def test_build_parser_defaults(monkeypatch):
    """Test that the parser falls back to the documented defaults."""
    monkeypatch.delenv("ADMIN_SERVER_PORT", raising=False)
    monkeypatch.delenv("PATH_PREFIX", raising=False)

    args = build_parser().parse_args([])

    assert args.host == "0.0.0.0"
    assert args.port == 8091
    assert args.path_prefix == "dtaas-user"
    assert args.reload is False
    assert args.list_services is False


def test_build_parser_reads_environment(monkeypatch):
    """Test that the parser defaults can be overridden by the environment."""
    monkeypatch.setenv("ADMIN_SERVER_PORT", "9000")
    monkeypatch.setenv("PATH_PREFIX", "user1")

    args = build_parser().parse_args([])

    assert args.port == 9000
    assert args.path_prefix == "user1"


def test_build_parser_arguments_override_environment(monkeypatch):
    """Test that command-line arguments win over environment variables."""
    monkeypatch.setenv("ADMIN_SERVER_PORT", "9000")

    args = build_parser().parse_args(
        ["--host", "127.0.0.1", "--port", "9100", "--path-prefix", "user2"]
    )

    assert args.host == "127.0.0.1"
    assert args.port == 9100
    assert args.path_prefix == "user2"
