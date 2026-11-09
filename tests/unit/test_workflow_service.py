"""Tests for the platform workflow execution service."""

import pytest

from secure_agentic_sandbox import runner
from secure_agentic_sandbox.tool_broker import create_mock_tool_broker
from secure_agentic_sandbox.workflow_service import (
    WorkflowAuthorizationError,
    WorkflowExecutionService,
)


def create_specification():
    return {
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


@pytest.mark.asyncio
async def test_service_executes_authorized_workflow():
    broker = create_mock_tool_broker()
    service = WorkflowExecutionService(broker)

    result = await service.execute(
        tenant_id="tenant-a",
        execution_id="execution-a",
        specification=create_specification(),
        allowed_tools={"inventory.lookup"},
    )

    assert result.execution_id == "execution-a"
    assert result.status == "succeeded"
    assert result.tools_used == ("inventory.lookup",)
    assert result.step_results["lookup"]["found"] is True
    assert result.grant_id is not None

    assert any(
        record.event == "grant.revoke"
        and record.outcome == "succeeded"
        for record in broker.audit_log
    )


@pytest.mark.asyncio
async def test_service_rejects_tool_outside_platform_policy():
    broker = create_mock_tool_broker()
    service = WorkflowExecutionService(broker)

    with pytest.raises(WorkflowAuthorizationError):
        await service.execute(
            tenant_id="tenant-a",
            execution_id="execution-a",
            specification=create_specification(),
            allowed_tools=set(),
        )

    # Authorization rejection must happen before grant creation.
    assert not any(
        record.event == "grant.create"
        for record in broker.audit_log
    )


@pytest.mark.asyncio
async def test_service_supports_python_only_workflow(monkeypatch):
    broker = create_mock_tool_broker()
    service = WorkflowExecutionService(broker)

    async def fake_run_python_source(source, **options):
        return runner.ExecutionResult(
            sandbox_id="mock-sandbox",
            stdout="Python completed\n",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.1,
            output={"answer": 42},
        )

    monkeypatch.setattr(
        runner,
        "run_python_source",
        fake_run_python_source,
    )

    result = await service.execute(
        tenant_id="tenant-a",
        execution_id="execution-python",
        specification={
            "version": 1,
            "steps": [
                {
                    "id": "calculate",
                    "type": "python",
                    "source": "print('calculate')",
                }
            ],
        },
        allowed_tools=set(),
    )

    assert result.status == "succeeded"
    assert result.tools_used == ()
    assert result.grant_id is None
    assert result.step_results["calculate"]["output"] == {"answer": 42}


@pytest.mark.asyncio
async def test_service_rejects_invalid_execution_identity():
    broker = create_mock_tool_broker()
    service = WorkflowExecutionService(broker)

    with pytest.raises(ValueError):
        await service.execute(
            tenant_id="",
            execution_id="execution-a",
            specification=create_specification(),
            allowed_tools={"inventory.lookup"},
        )


@pytest.mark.asyncio
async def test_service_stops_after_python_timeout(monkeypatch):
    broker = create_mock_tool_broker()
    service = WorkflowExecutionService(broker)

    async def fake_run_python_source(source, **options):
        return runner.ExecutionResult(
            sandbox_id="mock-sandbox",
            stdout="",
            stderr="Execution timed out",
            exit_code=None,
            timed_out=True,
            duration_seconds=10,
        )

    monkeypatch.setattr(
        runner,
        "run_python_source",
        fake_run_python_source,
    )

    result = await service.execute(
        tenant_id="tenant-a",
        execution_id="execution-timeout",
        specification={
            "version": 1,
            "steps": [
                {
                    "id": "run",
                    "type": "python",
                    "source": "while True: pass",
                }
            ],
        },
        allowed_tools=set(),
    )

    assert result.status == "timed_out"
