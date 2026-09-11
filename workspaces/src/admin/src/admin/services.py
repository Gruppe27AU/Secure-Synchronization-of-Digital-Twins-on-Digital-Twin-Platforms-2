"""
Workspace service discovery.

Loads the catalogue of services (desktop, VS Code, Jupyter, ...) that the
workspace exposes. The catalogue is data, not code: it is read from
``config/services_template.json`` at request time, so adding a service only
requires editing that file.
"""

import json
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).parent / "config"
SERVICES_TEMPLATE_PATH = CONFIG_DIR / "services_template.json"


def load_services() -> dict[str, Any]:
    """
    Load the workspace service catalogue from the JSON template.

    Returns:
        Mapping of service identifier to its ``name``, ``description`` and
        ``endpoint``.

    Raises:
        OSError: If the template file cannot be read.
        json.JSONDecodeError: If the template file is not valid JSON.
    """
    with open(SERVICES_TEMPLATE_PATH, "r", encoding="utf-8") as template_file:
        return json.load(template_file)
