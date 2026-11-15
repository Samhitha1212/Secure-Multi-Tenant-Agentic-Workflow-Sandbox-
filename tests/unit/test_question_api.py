"""Tests for question-based submission endpoints."""

import pytest
from fastapi.testclient import TestClient

from secure_agentic_sandbox import api, runner
from secure_agentic_sandbox.api import ApiPrincipal, create_app
from secure_agentic_sandbox.tool_broker import create_mock_tool_broker

API_KEY = "question-test-api-key"


@pytest.fixture
def broker():
    return create_mock_tool_broker(include_reservation=True)


@pytest.fixture
def client(broker, tmp_path):
    app = create_app(
        api_keys={
            API_KEY: ApiPrincipal(
                tenant_id="tenant-a",
                allowed_tools=frozenset(
                    {
                        "inventory.lookup",
                        "inventory.reserve",
                    }
                ),
            )
        },
        broker=broker,
        checkpoint_db_path=tmp_path / "question-api-checkpoints.sqlite3",
    )
    with TestClient(app) as test_client:
        yield test_client


def auth_headers():
    return {"Authorization": f"Bearer {API_KEY}"}


def test_list_questions(client):
    response = client.get("/questions", headers=auth_headers())

    assert response.status_code == 200

    questions = response.json()["questions"]

    assert len(questions) == 3
    assert {question["type"] for question in questions} == {
        "python",
        "workflow",
    }


def test_get_python_question_details(client):
    response = client.get(
        "/questions/python-list-sum",
        headers=auth_headers(),
    )

    assert response.status_code == 200

    question = response.json()

    assert question["type"] == "python"
    assert question["statement"]
    assert question["starter_code"]
    assert question["resource_limits"]["cpu"] == "500m"


def test_get_workflow_question_details(client):
    response = client.get(
        "/questions/inventory-reservation",
        headers=auth_headers(),
    )

    assert response.status_code == 200

    question = response.json()

    assert question["type"] == "workflow"
    assert question["starter_workflow"] is not None
    assert question["permissions_satisfied"] is True


def test_unknown_question_returns_404(client):
    response = client.get(
        "/questions/not-found",
        headers=auth_headers(),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_python_question_submission(monkeypatch, client):
    captured = {}

    async def fake_run_python_source(source, **options):
        captured["source"] = source
        captured["options"] = options

        return runner.ExecutionResult(
            sandbox_id="mock-sandbox",
            stdout="100\n",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.1,
        )

    monkeypatch.setattr(api.runner, "run_python_source", fake_run_python_source)

    response = client.post(
        "/questions/python-list-sum/submit",
        headers=auth_headers(),
        json={"source": "print(sum([10, 20, 30, 40]))"},
    )

    assert response.status_code == 200

    result = response.json()

    assert result["status"] == "succeeded"
    assert result["submission_type"] == "python"
    assert result["execution_result"]["stdout"] == "100\n"
    assert captured["source"] == "print(sum([10, 20, 30, 40]))"
    assert captured["options"]["cpu"] == "500m"
    assert captured["options"]["memory"] == "512Mi"


def test_python_question_rejects_workflow_specification(client):
    response = client.post(
        "/questions/python-list-sum/submit",
        headers=auth_headers(),
        json={
            "specification": {
                "version": 1,
                "steps": [],
            }
        },
    )

    assert response.status_code == 422



def test_workflow_question_submission(client):
    response = client.post(
        "/questions/inventory-reservation/submit",
        headers=auth_headers(),
        json={
            "execution_id": "question-workflow-test-001",
            "specification": {
                "version": 1,
                "steps": [
                    {
                        "id": "lookup",
                        "type": "tool",
                        "tool": "inventory.lookup",
                        "arguments": {"sku": "SKU-100"},
                    },
                    {
                        "id": "reserve",
                        "type": "tool",
                        "tool": "inventory.reserve",
                        "arguments": {
                            "sku": {"$ref": "lookup.sku"},
                            "quantity": 2,
                            "idempotency_key": "question-api-test-001",
                        },
                    },
                ],
            },
        },
    )

    assert response.status_code == 200

    result = response.json()

    assert result["execution_id"] == "question-workflow-test-001"
    assert result["status"] == "succeeded"
    assert result["submission_type"] == "workflow"
    assert result["tools_used"] == [
        "inventory.lookup",
        "inventory.reserve",
    ]
    assert result["step_results"]["reserve"]["remaining_quantity"] == 10


def test_question_request_cannot_override_resource_limits(monkeypatch, tmp_path):
    broker = create_mock_tool_broker(include_reservation=True)

    restricted_client = TestClient(
        create_app(
            api_keys={
                API_KEY: ApiPrincipal(
                    tenant_id="tenant-a",
                    allowed_tools=frozenset(
                        {
                            "inventory.lookup",
                            "inventory.reserve",
                        }
                    ),
                )
            },
            broker=broker,
            checkpoint_db_path=tmp_path / "restricted-api-checkpoints.sqlite3",
        )
    )

    captured = {}

    async def fake_run_python_source(source, **options):
        captured.update(options)

        return runner.ExecutionResult(
            sandbox_id="mock-sandbox",
            stdout="done",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.1,
        )

    monkeypatch.setattr(api.runner, "run_python_source", fake_run_python_source)

    response = restricted_client.post(
        "/questions/python-list-sum/submit",
        headers=auth_headers(),
        json={"source": "print('hello')"},
    )

    assert response.status_code == 200
    assert captured["cpu"] == "500m"
    assert captured["memory"] == "512Mi"
    assert captured["timeout_seconds"] == 10


def test_question_request_cannot_override_tenant(client):
    response = client.post(
        "/questions/python-list-sum/submit",
        headers=auth_headers(),
        json={
            "source": "print('hello')",
            "tenant_id": "tenant-attacker",
        },
    )

    assert response.status_code == 422


def test_workflow_submission_persists_checkpoint(client):
    response = client.post(
        "/questions/inventory-reservation/submit",
        headers=auth_headers(),
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
    payload = response.json()

    checkpoint = client.app.state.checkpoint_store.get_checkpoint(
        tenant_id="tenant-a",
        execution_id=payload["execution_id"],
    )

    assert checkpoint is not None
    assert checkpoint.status == "completed"
    assert checkpoint.next_step_index == 1
    assert checkpoint.step_results["lookup"]["found"] is True


def test_pause_endpoint_requests_pause_for_owned_execution(client):
    store = client.app.state.checkpoint_store

    store.create_checkpoint(
        tenant_id="tenant-a",
        execution_id="execution-api-pause",
        workflow_version=1,
        specification={
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
    )

    response = client.post(
        "/executions/execution-api-pause/pause",
        headers=auth_headers(),
    )

    assert response.status_code == 202
    assert response.json() == {
        "execution_id": "execution-api-pause",
        "status": "pause_requested",
        "checkpoint_status": "running",
    }

    checkpoint = store.get_checkpoint(
        tenant_id="tenant-a",
        execution_id="execution-api-pause",
    )
    assert checkpoint is not None
    assert checkpoint.pause_requested is True
    assert checkpoint.status == "running"


def test_pause_endpoint_does_not_allow_cross_tenant_access(client):
    store = client.app.state.checkpoint_store

    store.create_checkpoint(
        tenant_id="tenant-b",
        execution_id="execution-other-tenant",
        workflow_version=1,
        specification={
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
    )

    response = client.post(
        "/executions/execution-other-tenant/pause",
        headers=auth_headers(),
    )

    assert response.status_code == 404

    checkpoint = store.get_checkpoint(
        tenant_id="tenant-b",
        execution_id="execution-other-tenant",
    )
    assert checkpoint is not None
    assert checkpoint.pause_requested is False


def test_pause_endpoint_requires_authentication(client):
    response = client.post(
        "/executions/execution-missing/pause",
    )

    assert response.status_code == 401


def test_pause_endpoint_rejects_non_running_execution(client):
    store = client.app.state.checkpoint_store

    store.create_checkpoint(
        tenant_id="tenant-a",
        execution_id="execution-already-completed",
        workflow_version=1,
        specification={
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
        status="completed",
    )

    response = client.post(
        "/executions/execution-already-completed/pause",
        headers=auth_headers(),
    )

    assert response.status_code == 409


def test_resume_endpoint_continues_owned_checkpoint(client):
    store = client.app.state.checkpoint_store

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
                "id": "lookup_again",
                "type": "tool",
                "tool": "inventory.lookup",
                "arguments": {"sku": "SKU-100"},
            },
        ],
    }

    saved_result = {
        "sku": "SAVED-BEFORE-RESUME",
        "found": True,
        "quantity": 123,
    }

    store.create_checkpoint(
        tenant_id="tenant-a",
        execution_id="execution-api-resume",
        workflow_version=1,
        specification=specification,
        allowed_tools={"inventory.lookup"},
        next_step_index=1,
        step_results={"lookup": saved_result},
        status="paused",
    )

    response = client.post(
        "/executions/execution-api-resume/resume",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["execution_id"] == "execution-api-resume"
    assert payload["status"] == "succeeded"
    assert payload["step_results"]["lookup"] == saved_result
    assert payload["step_results"]["lookup_again"]["found"] is True

    checkpoint = store.get_checkpoint(
        tenant_id="tenant-a",
        execution_id="execution-api-resume",
    )

    assert checkpoint is not None
    assert checkpoint.status == "completed"
    assert checkpoint.next_step_index == 2
    assert checkpoint.step_results["lookup"] == saved_result
    assert (
        checkpoint.step_results["lookup_again"]
        == payload["step_results"]["lookup_again"]
    )
    assert checkpoint.step_results["lookup_again"]["found"] is True

def test_resume_endpoint_rejects_another_tenant_checkpoint(client):
    store = client.app.state.checkpoint_store

    store.create_checkpoint(
        tenant_id="tenant-b",
        execution_id="execution-other-tenant-resume",
        workflow_version=1,
        specification={
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
        status="paused",
    )

    response = client.post(
        "/executions/execution-other-tenant-resume/resume",
        headers=auth_headers(),
    )

    assert response.status_code == 404

    checkpoint = store.get_checkpoint(
        tenant_id="tenant-b",
        execution_id="execution-other-tenant-resume",
    )

    assert checkpoint is not None
    assert checkpoint.status == "paused"


def test_resume_endpoint_requires_authentication(client):
    response = client.post(
        "/executions/execution-api-resume/resume",
    )

    assert response.status_code == 401




def test_workflow_question_submission_generates_execution_id(client):
    response = client.post(
        "/questions/inventory-reservation/submit",
        headers=auth_headers(),
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

    assert result["execution_id"]
    assert result["submission_type"] == "workflow"
    assert result["status"] == "succeeded"


def test_workflow_question_submission_rejects_invalid_execution_id(client):
    response = client.post(
        "/questions/inventory-reservation/submit",
        headers=auth_headers(),
        json={
            "execution_id": "invalid execution id!",
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
    assert response.json()["detail"] == "Invalid execution ID."
