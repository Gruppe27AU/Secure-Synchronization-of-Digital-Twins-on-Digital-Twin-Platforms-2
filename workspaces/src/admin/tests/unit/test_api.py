"""
Unit tests for the HTTP API layer.

Covers the routes exposed by :mod:`admin.api` (``/``, ``/services``,
``/health`` and ``/git/status``) and the path prefix handling used for
multi-user deployments.
"""

from importlib.metadata import version

import pytest
from fastapi.testclient import TestClient

from admin.api import APP_VERSION, create_app, normalize_path_prefix
from admin.git.status import SyncStatus
from admin.git.sync import SyncErrorKind


@pytest.fixture(name='test_client')
def fixture_test_client():
    """Create a test client for the FastAPI app."""
    app = create_app()
    return TestClient(app)


@pytest.fixture(name='test_client_with_prefix')
def fixture_test_client_with_prefix():
    """Create a test client for the FastAPI app with path prefix."""
    app = create_app(path_prefix="user1")
    return TestClient(app)


def test_root_endpoint(test_client):
    """Test the root endpoint returns service information."""
    response = test_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "Workspace Admin Service"
    assert "endpoints" in data
    assert "/services" in data["endpoints"]


def test_health_check(test_client):
    """Test the health check endpoint."""
    response = test_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_services_endpoint(test_client):
    """Test the /services endpoint returns service list."""
    response = test_client.get("/services")
    assert response.status_code == 200

    services = response.json()

    # Check that we have the expected services
    assert "desktop" in services
    assert "vscode" in services
    assert "notebook" in services
    assert "lab" in services

    # Check desktop service structure
    desktop = services["desktop"]
    assert "name" in desktop
    assert "description" in desktop
    assert "endpoint" in desktop
    assert desktop["name"] == "Desktop"


def test_services_endpoint_returns_all_services(test_client):
    """Test /services endpoint returns all defined services."""
    response = test_client.get("/services")
    assert response.status_code == 200

    services = response.json()

    # Verify all expected services are present
    required_services = ["desktop", "vscode", "notebook", "lab"]
    for service_id in required_services:
        assert service_id in services


def test_services_endpoint_json_structure(test_client):
    """Test that services endpoint returns proper JSON structure."""
    response = test_client.get("/services")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"

    services = response.json()
    # Verify it's a dictionary with string keys
    assert isinstance(services, dict)
    for key, value in services.items():
        assert isinstance(key, str)
        assert isinstance(value, dict)
        assert "name" in value
        assert "description" in value
        assert "endpoint" in value


def test_root_endpoint_version(test_client):
    """Test that root endpoint includes version."""
    response = test_client.get("/")
    data = response.json()
    assert "version" in data
    assert data["version"] == APP_VERSION


def test_app_version_comes_from_package_metadata():
    """Test that the reported version is read from the installed package."""
    # Guards against the distribution being renamed without updating the
    # lookup in admin.api, which would silently fall back to a placeholder.
    assert APP_VERSION != "0.0.0+unknown"
    assert APP_VERSION == version("workspace-admin")


def test_health_endpoint_returns_json(test_client):
    """Test that health endpoint returns JSON."""
    response = test_client.get("/health")
    assert response.headers["content-type"] == "application/json"


def test_root_endpoint_with_prefix(test_client_with_prefix):
    """Test root endpoint with path prefix."""
    response = test_client_with_prefix.get("/user1/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "Workspace Admin Service"


def test_services_endpoint_with_prefix(test_client_with_prefix):
    """Test services endpoint with path prefix."""
    response = test_client_with_prefix.get("/user1/services")
    assert response.status_code == 200
    services = response.json()
    assert "desktop" in services


def test_health_endpoint_with_prefix(test_client_with_prefix):
    """Test health endpoint with path prefix."""
    response = test_client_with_prefix.get("/user1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_path_prefix_not_accessible_without_prefix(test_client_with_prefix):
    """Test that endpoints are not accessible without prefix when prefix is set."""
    # When app has prefix, root-level routes should not work
    response = test_client_with_prefix.get("/services")
    assert response.status_code == 404


def test_create_app_with_various_prefixes():
    """Test creating app with different prefix formats."""
    # Test with leading/trailing slashes - routes should work
    app1 = create_app("/user1/")
    client1 = TestClient(app1)
    assert client1.get("/user1/health").status_code == 200

    app2 = create_app("user1")
    client2 = TestClient(app2)
    assert client2.get("/user1/health").status_code == 200

    app3 = create_app("")
    client3 = TestClient(app3)
    assert client3.get("/health").status_code == 200

    app4 = create_app("/")
    client4 = TestClient(app4)
    assert client4.get("/health").status_code == 200


@pytest.mark.parametrize(
    "raw_prefix,expected",
    [
        ("user1", "/user1"),
        ("/user1", "/user1"),
        ("/user1/", "/user1"),
        ("", ""),
        ("/", ""),
    ]
)
def test_normalize_path_prefix(raw_prefix, expected):
    """Test that path prefixes are normalized to a single leading slash."""
    assert normalize_path_prefix(raw_prefix) == expected


def test_git_status_reports_disabled_without_a_status(test_client):
    """Test /git/status works, and says disabled, when no backup runs."""
    response = test_client.get("/git/status")
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "repositories": {}}


def test_git_status_serves_the_recorded_outcome():
    """Test /git/status serves what the scheduler recorded, kind only."""
    status = SyncStatus()
    status.enable(["private", "common"])
    status.record_failure("private", SyncErrorKind.AUTH_FAILED)
    status.record_success("common")
    client = TestClient(create_app(sync_status=status))

    data = client.get("/git/status").json()

    assert data["enabled"] is True
    assert data["repositories"]["private"]["last_error"]["kind"] == "auth_failed"
    assert data["repositories"]["common"]["last_error"] is None
    assert data["repositories"]["common"]["last_success"] is not None


def test_git_status_reflects_later_records():
    """Test the route reads the live status, not a copy taken at startup."""
    status = SyncStatus()
    client = TestClient(create_app(sync_status=status))
    status.enable(["private"])

    assert client.get("/git/status").json()["enabled"] is True


def test_git_status_with_prefix():
    """Test /git/status is served under the path prefix only."""
    client = TestClient(create_app(path_prefix="user1"))

    assert client.get("/user1/git/status").status_code == 200
    assert client.get("/git/status").status_code == 404


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_git_status_is_read_only(test_client, method):
    """Test the status cannot be changed or a backup triggered over HTTP."""
    response = getattr(test_client, method)("/git/status")
    assert response.status_code == 405
