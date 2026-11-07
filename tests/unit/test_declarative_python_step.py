"""Tests for isolated Python steps in declarative workflows."""

import pytest

from secure_agentic_sandbox import runner
from secure_agentic_sandbox.declarative_workflow import (
    WorkflowSpecError,
    build_declarative_tool_workflow,
)
from secure_agentic_sandbox.tool_broker import (
    ToolCallContext,
    create_mock_tool_broker,
)


def create_broker_and_grant():
    broker = create_mock_tool_broker()

    context = ToolCallContext(
        tenant_id="tenant-a",
        execution_id="execution-a",
    )

    grant = broker.create_grant(
        tenant_id=context.tenant_id,
        execution_id=context.execution_id,
        allowed_tools={"inventory.lookup"},
        max_calls=20,
    )

    return broker, context, grant


def python_step(source="print('Hello')", **options):
    return {
        "id": "script",
        "type": "python",
        "source": source,
        **options,
    }


def test_python_step_executes_and_records_result(monkeypatch):
    broker, context, grant = create_broker_and_grant()
    captured = {}

    expected_result = runner.ExecutionResult(
        sandbox_id="mock-sandbox",
        stdout="Hello\n",
        stderr="",
        exit_code=0,
        timed_out=False,
        duration_seconds=0.1,
    )

    async def fake_run_python_source(source, **options):
        captured["source"] = source
        captured["options"] = options
        return expected_result

    monkeypatch.setattr(
        runner,
        "run_python_source",
        fake_run_python_source,
    )

    graph = build_declarative_tool_workflow(
        broker=broker,
        context=context,
        grant=grant,
        specification={
            "version": 1,
            "steps": [python_step()],
        },
    )

    result = await_graph(graph)

    assert captured["source"] == "print('Hello')"
    assert captured["options"]["timeout_seconds"] == 10
    assert result["step_results"]["script"]["stdout"] == "Hello\n"
    assert result["step_results"]["script"]["exit_code"] == 0


def await_graph(graph):
    """Invoke a graph containing asynchronous Python nodes."""

    import asyncio

    return asyncio.run(graph.ainvoke({"step_results": {}}))


@pytest.mark.asyncio
async def test_python_step_can_be_followed_by_tool_step(monkeypatch):
    broker, context, grant = create_broker_and_grant()

    async def fake_run_python_source(source, **options):
        return runner.ExecutionResult(
            sandbox_id="mock-sandbox",
            stdout="Python completed\n",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.1,
        )

    monkeypatch.setattr(
        runner,
        "run_python_source",
        fake_run_python_source,
    )

    graph = build_declarative_tool_workflow(
        broker=broker,
        context=context,
        grant=grant,
        specification={
            "version": 1,
            "steps": [
                python_step(),
                {
                    "id": "lookup",
                    "type": "tool",
                    "tool": "inventory.lookup",
                    "arguments": {"sku": "SKU-100"},
                },
            ],
        },
    )

    result = await graph.ainvoke({"step_results": {}})

    assert result["step_results"]["script"]["exit_code"] == 0
    assert result["step_results"]["lookup"]["found"] is True


@pytest.mark.asyncio
async def test_timeout_stops_following_tool_steps(monkeypatch):
    broker, context, grant = create_broker_and_grant()

    async def fake_run_python_source(source, **options):
        return runner.ExecutionResult(
            sandbox_id="mock-sandbox",
            stdout="",
            stderr="Execution timed out.",
            exit_code=None,
            timed_out=True,
            duration_seconds=10.0,
        )

    monkeypatch.setattr(
        runner,
        "run_python_source",
        fake_run_python_source,
    )

    graph = build_declarative_tool_workflow(
        broker=broker,
        context=context,
        grant=grant,
        specification={
            "version": 1,
            "steps": [
                python_step(),
                {
                    "id": "lookup",
                    "type": "tool",
                    "tool": "inventory.lookup",
                    "arguments": {"sku": "SKU-100"},
                },
            ],
        },
    )

    result = await graph.ainvoke({"step_results": {}})

    assert result["step_results"]["script"]["timed_out"] is True
    assert "lookup" not in result["step_results"]


@pytest.mark.asyncio
async def test_oom_kill_stops_following_tool_steps(monkeypatch):
    broker, context, grant = create_broker_and_grant()

    async def fake_run_python_source(source, **options):
        return runner.ExecutionResult(
            sandbox_id="mock-sandbox",
            stdout="",
            stderr="Out of memory.",
            exit_code=137,
            timed_out=False,
            duration_seconds=2.0,
            oom_killed=True,
        )

    monkeypatch.setattr(
        runner,
        "run_python_source",
        fake_run_python_source,
    )

    graph = build_declarative_tool_workflow(
        broker=broker,
        context=context,
        grant=grant,
        specification={
            "version": 1,
            "steps": [
                python_step(),
                {
                    "id": "lookup",
                    "type": "tool",
                    "tool": "inventory.lookup",
                    "arguments": {"sku": "SKU-100"},
                },
            ],
        },
    )

    result = await graph.ainvoke({"step_results": {}})

    assert result["step_results"]["script"]["oom_killed"] is True
    assert "lookup" not in result["step_results"]


def test_python_step_rejects_invalid_source():
    broker, _, _ = create_broker_and_grant()

    with pytest.raises(WorkflowSpecError, match="Invalid Python step"):
        build_declarative_tool_workflow(
            broker=broker,
            context=ToolCallContext(
                tenant_id="tenant-a",
                execution_id="execution-a",
            ),
            grant=broker.create_grant(
                tenant_id="tenant-a",
                execution_id="execution-a",
                allowed_tools={"inventory.lookup"},
            ),
            specification={
                "version": 1,
                "steps": [python_step(source=123)],
            },
        )


def test_python_step_rejects_invalid_resource_limits():
    broker, context, grant = create_broker_and_grant()

    with pytest.raises(WorkflowSpecError, match="Invalid Python step"):
        build_declarative_tool_workflow(
            broker=broker,
            context=context,
            grant=grant,
            specification={
                "version": 1,
                "steps": [
                    python_step(cpu="5000m"),
                ],
            },
        )
