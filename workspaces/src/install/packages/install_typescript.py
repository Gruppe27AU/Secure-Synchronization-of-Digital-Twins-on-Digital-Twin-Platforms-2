"""Installs Node.js and TypeScript toolchain."""
import subprocess

from installation_util import get_dependencies_for_package

get_dependencies_for_package("node").default_installation()

subprocess.run(
    ["npm", "config", "set", "prefix", "/usr/local/npm-packages"],
    check=True
    )

get_dependencies_for_package("typescript").default_installation()
