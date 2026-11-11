"""Installs JupyterLab and Jupyter Notebook."""
from installation_util import get_dependencies_for_package

get_dependencies_for_package(
    "jupyter"
    ).default_installation()
