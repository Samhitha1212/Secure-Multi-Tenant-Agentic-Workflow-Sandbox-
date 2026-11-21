
"""Tests for the workflow HTTP API."""

import pytest
from fastapi.testclient import TestClient

from secure_agentic_sandbox.api import (
    ApiPrincipal,
    create_app,
)
from secure_agentic_sandbox.tool_broker import (
    ToolCallContext,
    create_mock_tool_broker,
)

API_KEY = "test-development-api-key"


@pytest.fixture
def client(tmp_path):
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
        checkpoint_db_path=tmp_path / "legacy-api-checkpoints.sqlite3",
    )

    with TestClient(app) as test_client:
        yield test_client


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


def test_api_startup_recovers_interrupted_checkpoints(tmp_path):
    database = tmp_path / "restart-checkpoints.sqlite3"
    api_keys = {
        API_KEY: ApiPrincipal(
            tenant_id="tenant-a",
            allowed_tools=frozenset({"inventory.lookup"}),
        )
    }

    # Simulate the previous process creating and progressing a workflow.
    previous_app = create_app(
        api_keys=api_keys,
        broker=create_mock_tool_broker(include_reservation=True),
        checkpoint_db_path=database,
    )
    previous_store = previous_app.state.checkpoint_store

    specification = {
        "version": 1,
        "steps": [
            {
                "id": "lookup",
                "type": "tool",
                "tool": "inventory.lookup",
                "arguments": {"sku": "SKU-100"},
            },
            {
                "id": "process",
                "type": "python",
                "source": "print('processed')",
            },
        ],
    }

    previous_store.create_checkpoint(
        tenant_id="tenant-a",
        execution_id="execution-restart",
        workflow_version=1,
        specification=specification,
    )
    saved_results = {
        "lookup": {
            "found": True,
            "quantity": 12,
        }
    }
    previous_store.update_progress(
        tenant_id="tenant-a",
        execution_id="execution-restart",
        next_step_index=1,
        step_results=saved_results,
        status="running",
        expected_next_step_index=0,
    )

    # Simulate the old process exiting before the workflow finishes.
    previous_store.close()

    restarted_app = create_app(
        api_keys=api_keys,
        broker=create_mock_tool_broker(include_reservation=True),
        checkpoint_db_path=database,
    )

    # Entering the lifespan performs startup recovery.
    with TestClient(restarted_app) as restarted_client:
        checkpoint = restarted_client.app.state.checkpoint_store.get_checkpoint(
            tenant_id="tenant-a",
            execution_id="execution-restart",
        )

        assert checkpoint is not None
        assert checkpoint.status == "paused"
        assert checkpoint.pause_requested is False
        assert checkpoint.next_step_index == 1
        assert checkpoint.step_results == saved_results


def test_resume_does_not_duplicate_tool_side_effect_after_restart(tmp_path):
    database = tmp_path / "durable-resume.sqlite3"
    execution_id = "crash-window-execution"
    context = ToolCallContext(
        tenant_id="tenant-a",
        execution_id=execution_id,
    )
    api_keys = {
        API_KEY: ApiPrincipal(
            tenant_id="tenant-a",
            allowed_tools=frozenset(
                {"inventory.lookup", "inventory.reserve"}
            ),
        )
    }
    specification = {
        "version": 1,
        "steps": [
            {
                "id": "reserve",
                "type": "tool",
                "tool": "inventory.reserve",
                "arguments": {
                    "sku": "SKU-100",
                    "quantity": 2,
                    "idempotency_key": "crash-window-key",
                },
            }
        ],
    }

    # First process: create a running checkpoint.
    first_app = create_app(
        api_keys=api_keys,
        checkpoint_db_path=database,
    )

    with TestClient(first_app) as first_client:
        first_client.app.state.checkpoint_store.create_checkpoint(
            tenant_id="tenant-a",
            execution_id=execution_id,
            workflow_version=1,
            specification=specification,
            allowed_tools={"inventory.reserve"},
        )

        # The tool side effect succeeds, but progress is deliberately
        # not written to the checkpoint before simulating a crash.
        first_broker = first_client.app.state.broker
        first_grant = first_broker.create_grant(
            tenant_id=context.tenant_id,
            execution_id=context.execution_id,
            allowed_tools={"inventory.reserve"},
            max_calls=10,
        )
        first_result = first_broker.invoke(
            context=context,
            grant_token=first_grant.token,
            tool_name="inventory.reserve",
            arguments={
                "sku": "SKU-100",
                "quantity": 2,
                "idempotency_key": "crash-window-key",
            },
        )

        assert first_result["status"] == "reserved"
        assert first_result["remaining_quantity"] == 10

    # Second process: startup recovers the checkpoint and resume replays
    # the tool step using the same durable idempotency key.
    restarted_app = create_app(
        api_keys=api_keys,
        checkpoint_db_path=database,
    )

    with TestClient(restarted_app) as restarted_client:
        store = restarted_client.app.state.checkpoint_store
        recovered = store.get_checkpoint(
            tenant_id="tenant-a",
            execution_id=execution_id,
        )

        assert recovered is not None
        assert recovered.status == "paused"
        assert recovered.next_step_index == 0

        response = restarted_client.post(
            f"/executions/{execution_id}/resume",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert (
            payload["step_results"]["reserve"]["reservation_id"]
            == first_result["reservation_id"]
        )

        # The inventory remains at 10: the replay did not decrement it again.
        restarted_broker = restarted_client.app.state.broker
        lookup_grant = restarted_broker.create_grant(
            tenant_id=context.tenant_id,
            execution_id=context.execution_id,
            allowed_tools={"inventory.lookup"},
            max_calls=10,
        )
        inventory = restarted_broker.invoke(
            context=context,
            grant_token=lookup_grant.token,
            tool_name="inventory.lookup",
            arguments={"sku": "SKU-100"},
        )

        assert inventory["quantity"] == 10

        completed = store.get_checkpoint(
            tenant_id="tenant-a",
            execution_id=execution_id,
        )
        assert completed is not None
        assert completed.status == "completed"
        assert completed.next_step_index == 1
