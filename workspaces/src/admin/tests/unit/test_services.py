"""
Unit tests for workspace service discovery.

Covers :mod:`admin.services`: loading the service catalogue and the
integrity of the JSON template it is loaded from.
"""

import json

from admin.services import SERVICES_TEMPLATE_PATH, load_services


def test_load_services_preserves_structure():
    """Test that load_services preserves the service structure."""
    services = load_services()

    # Check all required services exist
    required_services = ["desktop", "vscode", "notebook", "lab"]
    for service_id in required_services:
        assert service_id in services
        assert "name" in services[service_id]
        assert "description" in services[service_id]
        assert "endpoint" in services[service_id]


def test_load_services_with_missing_endpoint():
    """Test load_services handles services without endpoint field."""
    services = load_services()
    # All services should have endpoint field, even if empty
    for service_info in services.values():
        assert 'endpoint' in service_info


def test_services_template_file_exists():
    """Test that the services template file exists."""
    assert SERVICES_TEMPLATE_PATH.exists()
    assert SERVICES_TEMPLATE_PATH.is_file()


def test_services_template_valid_json():
    """Test that the services template is valid JSON."""
    with open(SERVICES_TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        services = json.load(f)

    # Should not raise an exception
    assert isinstance(services, dict)
    assert len(services) > 0
