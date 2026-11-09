#!/usr/bin/env bash
set -xe

# Installs Jupyter and Python3

DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv

python3 -m venv /opt/jupyter-env
/opt/jupyter-env/bin/pip install --no-cache-dir \
    jupyterlab==4.3.4 \
    notebook==7.3.2

ln -s /opt/jupyter-env/bin/jupyter /usr/local/bin/jupyter
