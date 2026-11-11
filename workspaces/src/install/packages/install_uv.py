"""Installs the uv Python package manager and python development tools."""
import subprocess

from installation_util import get_dependencies_for_package

subprocess.run(
    ["apt-get", "update"],
    check=True
    )

subprocess.run(
    ["apt-get", "install", "-y", "--no-install-recommends", "python3-dev", "build-essential"],
    check=True
    )

get_dependencies_for_package(
    "uv",
    resulting_binaries=["uv", "uvx"]
    ).default_installation()
