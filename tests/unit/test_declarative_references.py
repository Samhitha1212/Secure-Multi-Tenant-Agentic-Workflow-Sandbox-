"""Tests for data references between declarative workflow steps."""

import pytest

from secure_agentic_sandbox import runner
from secure_agentic_sandbox.declarative_workflow import (
    WorkflowReferenceError,
    WorkflowSpecError,
    build_declarative_tool_workflow,
    parse_tool_workflow_spec,
)
from secure_agentic_sandbox.tool_broker import (
    ToolCallContext,
    create_mock_tool_broker,
)


def create_broker_and_grant(*, include_reservation=True):
    broker = create_mock_tool_broker(
        include_reservation=include_reservation,
    )

    context = ToolCallContext(
        tenant_id="tenant-a",
        execution_id="execution-a",
    )

    grant = broker.create_grant(
        tenant_id=context.tenant_id,
        execution_id=context.execution_id,
        allowed_tools=broker.registered_tools,
        max_calls=30,
    )

    return broker, context, grant


def test_tool_arguments_can_reference_previous_tool_result():
    broker, context, grant = create_broker_and_grant()

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
                "id": "reserve",
                "type": "tool",
                "tool": "inventory.reserve",
                "arguments": {
                    "sku": {"$ref": "lookup.sku"},
                    "quantity": {"$ref": "lookup.quantity"},
                    "idempotency_key": "reference-test-001",
                },
            },
        ],
    }

    graph = build_declarative_tool_workflow(
        broker=broker,
        context=context,
        grant=grant,
        specification=specification,
    )

    result = graph.invoke({"step_results": {}})

    assert result["step_results"]["lookup"]["quantity"] == 12
    assert result["step_results"]["reserve"]["status"] == "reserved"
    assert result["step_results"]["reserve"]["quantity"] == 12
    assert result["step_results"]["reserve"]["remaining_quantity"] == 0


@pytest.mark.asyncio
async def test_python_step_receives_selected_previous_result(monkeypatch):
    broker, context, grant = create_broker_and_grant(
        include_reservation=False,
    )
    captured = {}

    async def fake_run_python_source(source, **options):
        captured["source"] = source
        captured["input_data"] = options["input_data"]

        return runner.ExecutionResult(
            sandbox_id="mock-sandbox",
            stdout="Processed inventory\n",
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
                {
                    "id": "lookup",
                    "type": "tool",
                    "tool": "inventory.lookup",
                    "arguments": {"sku": "SKU-100"},
                },
                {
                    "id": "process",
                    "type": "python",
                    "source": "print('Process inventory')",
                    "inputs": {
                        "inventory": {"$ref": "lookup"},
                        "sku": {"$ref": "lookup.sku"},
                        "quantity": {"$ref": "lookup.quantity"},
                    },
                },
            ],
        },
    )

    result = await graph.ainvoke({"step_results": {}})

    assert captured["input_data"]["sku"] == "SKU-100"
    assert captured["input_data"]["quantity"] == 12
    assert captured["input_data"]["inventory"]["found"] is True
    assert result["step_results"]["process"]["exit_code"] == 0


def test_forward_reference_is_rejected():
    broker, _, _ = create_broker_and_grant()

    specification = {
        "version": 1,
        "steps": [
            {
                "id": "reserve",
                "type": "tool",
                "tool": "inventory.reserve",
                "arguments": {
                    "sku": {"$ref": "lookup.sku"},
                    "quantity": 1,
                    "idempotency_key": "forward-reference",
                },
            },
            {
                "id": "lookup",
                "type": "tool",
                "tool": "inventory.lookup",
                "arguments": {"sku": "SKU-100"},
            },
        ],
    }

    with pytest.raises(WorkflowSpecError, match="earlier workflow step"):
        parse_tool_workflow_spec(
            specification,
            available_tools=broker.registered_tools,
        )


def test_unknown_reference_is_rejected():
    broker, _, _ = create_broker_and_grant()

    specification = {
        "version": 1,
        "steps": [
            {
                "id": "reserve",
                "type": "tool",
                "tool": "inventory.reserve",
                "arguments": {
                    "sku": {"$ref": "unknown.sku"},
                    "quantity": 1,
                    "idempotency_key": "unknown-reference",
                },
            }
        ],
    }

    with pytest.raises(WorkflowSpecError, match="earlier workflow step"):
        parse_tool_workflow_spec(
            specification,
            available_tools=broker.registered_tools,
        )


def test_reference_object_rejects_extra_fields():
    broker, _, _ = create_broker_and_grant()

    specification = {
        "version": 1,
        "steps": [
            {
                "id": "lookup",
                "type": "tool",
                "tool": "inventory.lookup",
                "arguments": {
                    "sku": "SKU-100",
                },
            },
            {
                "id": "reserve",
                "type": "tool",
                "tool": "inventory.reserve",
                "arguments": {
                    "sku": {
                        "$ref": "lookup.sku",
                        "fallback": "SKU-200",
                    },
                    "quantity": 1,
                    "idempotency_key": "invalid-reference",
                },
            },
        ],
    }

    with pytest.raises(WorkflowSpecError, match="reference object"):
        parse_tool_workflow_spec(
            specification,
            available_tools=broker.registered_tools,
        )


def test_missing_result_field_fails_at_runtime():
    broker, context, grant = create_broker_and_grant()

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
                "id": "reserve",
                "type": "tool",
                "tool": "inventory.reserve",
                "arguments": {
                    "sku": {"$ref": "lookup.missing_field"},
                    "quantity": 1,
                    "idempotency_key": "missing-field-test",
                },
            },
        ],
    }

    graph = build_declarative_tool_workflow(
        broker=broker,
        context=context,
        grant=grant,
        specification=specification,
    )

    with pytest.raises(WorkflowReferenceError, match="does not exist"):
        graph.invoke({"step_results": {}})


@pytest.mark.asyncio
async def test_lookup_python_reservation_sequence(monkeypatch):
    broker, context, grant = create_broker_and_grant()
    captured = {}

    async def fake_run_python_source(source, **options):
        captured["source"] = source
        captured["input_data"] = options["input_data"]

        return runner.ExecutionResult(
            sandbox_id="mock-sandbox",
            stdout="Inventory processed\n",
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
                "source": (
                    "import json\n"
                    "with open('/tmp/workflow_input.json') as f:\n"
                    "    data = json.load(f)\n"
                    "print(data['inventory']['sku'])\n"
                ),
                "inputs": {
                    "inventory": {"$ref": "lookup"},
                },
            },
            {
                "id": "reserve",
                "type": "tool",
                "tool": "inventory.reserve",
                "arguments": {
                    "sku": {"$ref": "lookup.sku"},
                    "quantity": 2,
                    "idempotency_key": "full-workflow-test-001",
                },
            },
        ],
    }

    graph = build_declarative_tool_workflow(
        broker=broker,
        context=context,
        grant=grant,
        specification=specification,
    )

    result = await graph.ainvoke({"step_results": {}})

    assert captured["input_data"]["inventory"]["sku"] == "SKU-100"
    assert captured["input_data"]["inventory"]["quantity"] == 12

    assert result["step_results"]["process"]["exit_code"] == 0
    assert result["step_results"]["reserve"]["status"] == "reserved"
    assert result["step_results"]["reserve"]["remaining_quantity"] == 10

@pytest.mark.asyncio
async def test_python_output_can_feed_subsequent_tool_step(monkeypatch):
    broker, context, grant = create_broker_and_grant()
    captured = {}

    async def fake_run_python_source(source, **options):
        captured["input_data"] = options["input_data"]

        return runner.ExecutionResult(
            sandbox_id="mock-sandbox",
            stdout="Reservation plan generated\n",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.1,
            output={
                "sku": "SKU-100",
                "quantity": 3,
            },
        )

    monkeypatch.setattr(
        runner,
        "run_python_source",
        fake_run_python_source,
    )

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
                "source": (
                    "import json\n"
                    "with open('/tmp/workflow_input.json') as f:\n"
                    "    data = json.load(f)\n"
                    "result = {'sku': data['inventory']['sku'], "
                    "'quantity': 3}\n"
                    "with open('/tmp/workflow_output.json', 'w') as f:\n"
                    "    json.dump(result, f)\n"
                ),
                "inputs": {
                    "inventory": {"$ref": "lookup"},
                },
            },
            {
                "id": "reserve",
                "type": "tool",
                "tool": "inventory.reserve",
                "arguments": {
                    "sku": {"$ref": "process.output.sku"},
                    "quantity": {"$ref": "process.output.quantity"},
                    "idempotency_key": "python-output-reservation-001",
                },
            },
        ],
    }

    graph = build_declarative_tool_workflow(
        broker=broker,
        context=context,
        grant=grant,
        specification=specification,
    )

    result = await graph.ainvoke({"step_results": {}})

    assert captured["input_data"]["inventory"]["sku"] == "SKU-100"

    assert result["step_results"]["process"]["output"] == {
        "sku": "SKU-100",
        "quantity": 3,
    }

    reservation = result["step_results"]["reserve"]

    assert reservation["status"] == "reserved"
    assert reservation["quantity"] == 3
    assert reservation["remaining_quantity"] == 9