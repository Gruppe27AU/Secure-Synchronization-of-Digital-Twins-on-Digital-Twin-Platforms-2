"""Installs nginx from the official nginx.org repository."""
import os
import subprocess

from installation_util import get_dependencies_for_package

get_dependencies_for_package("nginx").default_installation()

# Create nginx working directories for the runtime user
home = os.environ.get("HOME")
nginx_dir = os.path.join(home, ".nginx")
for subdir in ["logs", "temp"]:
    os.makedirs(os.path.join(nginx_dir, subdir), exist_ok=True)
subprocess.run(["chown", "-R", "1000:0", nginx_dir], check=True)
