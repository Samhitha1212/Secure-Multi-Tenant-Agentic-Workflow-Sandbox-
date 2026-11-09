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
def client(broker):
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
    )
    return TestClient(app)


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
            }
        },
    )

    assert response.status_code == 200

    result = response.json()

    assert result["status"] == "succeeded"
    assert result["submission_type"] == "workflow"
    assert result["tools_used"] == [
        "inventory.lookup",
        "inventory.reserve",
    ]
    assert result["step_results"]["reserve"]["remaining_quantity"] == 10


def test_question_request_cannot_override_resource_limits(monkeypatch):
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
