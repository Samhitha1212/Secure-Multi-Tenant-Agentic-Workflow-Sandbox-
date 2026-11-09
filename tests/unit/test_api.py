
"""Tests for the workflow HTTP API."""

import pytest
from fastapi.testclient import TestClient

from secure_agentic_sandbox.api import (
    ApiPrincipal,
    create_app,
)
from secure_agentic_sandbox.tool_broker import create_mock_tool_broker

API_KEY = "test-development-api-key"


@pytest.fixture
def client():
    """Create an API client with a test tenant."""

    broker = create_mock_tool_broker(
        include_reservation=True,
    )

    app = create_app(
        api_keys={
            API_KEY: ApiPrincipal(
                tenant_id="tenant-a",
                allowed_tools=frozenset({"inventory.lookup"}),
            )
        },
        broker=broker,
    )

    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_workflow_requires_authentication(client):
    response = client.get("/tools")

    assert response.status_code == 401


def test_invalid_api_key_is_rejected(client):
    response = client.get(
        "/tools",
        headers={"Authorization": "Bearer incorrect-key"},
    )

    assert response.status_code == 401


def test_tools_endpoint_returns_only_allowed_tools(client):
    response = client.get(
        "/tools",
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "tools": ["inventory.lookup"],
    }


def test_authorized_workflow_execution(client):
    response = client.post(
        "/workflows/execute",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "specification": {
                "version": 1,
                "steps": [
                    {
                        "id": "lookup",
                        "type": "tool",
                        "tool": "inventory.lookup",
                        "arguments": {"sku": "SKU-100"},
                    }
                ],
            }
        },
    )

    assert response.status_code == 200

    result = response.json()

    assert result["status"] == "succeeded"
    assert result["execution_id"]
    assert result["tools_used"] == ["inventory.lookup"]
    assert result["step_results"]["lookup"]["found"] is True

    assert "tenant_id" not in result
    assert "grant_token" not in result


def test_workflow_cannot_use_tool_outside_tenant_policy(client):
    response = client.post(
        "/workflows/execute",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "specification": {
                "version": 1,
                "steps": [
                    {
                        "id": "reserve",
                        "type": "tool",
                        "tool": "inventory.reserve",
                        "arguments": {
                            "sku": "SKU-100",
                            "quantity": 1,
                            "idempotency_key": "api-test-001",
                        },
                    }
                ],
            }
        },
    )

    assert response.status_code == 403


def test_client_cannot_override_tenant_identity(client):
    response = client.post(
        "/workflows/execute",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "tenant_id": "tenant-attacker",
            "specification": {
                "version": 1,
                "steps": [
                    {
                        "id": "lookup",
                        "type": "tool",
                        "tool": "inventory.lookup",
                        "arguments": {"sku": "SKU-100"},
                    }
                ],
            },
        },
    )

    assert response.status_code == 422


def test_invalid_workflow_is_rejected(client):
    response = client.post(
        "/workflows/execute",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "specification": {
                "version": 99,
                "steps": [],
            }
        },
    )

    assert response.status_code == 422
