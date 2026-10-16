"""
Location of the config template for the git tests.

The template lives outside the package, in ``workspaces/src/templates``, so
the path only resolves in a source checkout.
"""

from pathlib import Path

#: The file users copy to ``<root>/.config/workspace-admin/config.toml``.
EXAMPLE_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "templates" / "config.toml.example"
)
