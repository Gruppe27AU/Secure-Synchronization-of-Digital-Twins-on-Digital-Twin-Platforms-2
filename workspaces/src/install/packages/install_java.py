"""Installs the Eclipse Temurin JDK."""
from installation_util import get_dependencies_for_package

get_dependencies_for_package(
    "java",
    installed_package_name="java"
).default_installation()
