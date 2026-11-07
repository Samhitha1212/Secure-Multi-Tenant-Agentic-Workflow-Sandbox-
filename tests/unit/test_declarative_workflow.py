
"""Tests for declarative LangGraph tool workflows."""

import pytest

from secure_agentic_sandbox.declarative_workflow import (
    WorkflowSpecError,
    build_declarative_tool_workflow,
    parse_tool_workflow_spec,
)
from secure_agentic_sandbox.tool_broker import (
    GrantScopeError,
    ToolCallContext,
    create_mock_tool_broker,
)


def create_context():
    return ToolCallContext(
        tenant_id="tenant-a",
        execution_id="execution-a",
    )


def create_broker_and_grant(
    *,
    include_reservation=False,
    allowed_tools=None,
):
    broker = create_mock_tool_broker(
        include_reservation=include_reservation,
    )
    context = create_context()

    if allowed_tools is None:
        allowed_tools = {"inventory.lookup"}

    grant = broker.create_grant(
        tenant_id=context.tenant_id,
        execution_id=context.execution_id,
        allowed_tools=allowed_tools,
        max_calls=50,
    )

    return broker, context, grant


def test_single_tool_workflow():
    broker, context, grant = create_broker_and_grant()

    specification = {
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

    graph = build_declarative_tool_workflow(
        broker=broker,
        context=context,
        grant=grant,
        specification=specification,
    )

    result = graph.invoke({"step_results": {}})

    assert result["step_results"]["lookup"]["found"] is True
    assert result["step_results"]["lookup"]["quantity"] == 12


def test_multiple_tool_steps_execute_in_sequence():
    broker, context, grant = create_broker_and_grant(
        include_reservation=True,
        allowed_tools={
            "inventory.lookup",
            "inventory.reserve",
        },
    )

    specification = {
        "version": 1,
        "steps": [
            {
                "id": "before",
                "type": "tool",
                "tool": "inventory.lookup",
                "arguments": {"sku": "SKU-100"},
            },
            {
                "id": "reserve",
                "type": "tool",
                "tool": "inventory.reserve",
                "arguments": {
                    "sku": "SKU-100",
                    "quantity": 2,
                    "idempotency_key": "declarative-order-001",
                },
            },
            {
                "id": "after",
                "type": "tool",
                "tool": "inventory.lookup",
                "arguments": {"sku": "SKU-100"},
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
    results = result["step_results"]

    assert results["before"]["quantity"] == 12
    assert results["reserve"]["status"] == "reserved"
    assert results["reserve"]["remaining_quantity"] == 10
    assert results["after"]["quantity"] == 10


def test_unregistered_tool_is_rejected_during_validation():
    broker, _, _ = create_broker_and_grant()

    specification = {
        "version": 1,
        "steps": [
            {
                "id": "unknown",
                "type": "tool",
                "tool": "filesystem.read",
                "arguments": {},
            }
        ],
    }

    with pytest.raises(WorkflowSpecError):
        parse_tool_workflow_spec(
            specification,
            available_tools=broker.registered_tools,
        )


def test_duplicate_step_ids_are_rejected():
    broker, _, _ = create_broker_and_grant()

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
                "id": "lookup",
                "type": "tool",
                "tool": "inventory.lookup",
                "arguments": {"sku": "SKU-200"},
            },
        ],
    }

    with pytest.raises(WorkflowSpecError, match="Duplicate step ID"):
        parse_tool_workflow_spec(
            specification,
            available_tools=broker.registered_tools,
        )


def test_unsupported_step_type_is_rejected():
    broker, _, _ = create_broker_and_grant()

    specification = {
        "version": 1,
        "steps": [
            {
                "id": "python",
                "type": "shell",
                "tool": "inventory.lookup",
                "arguments": {},
            }
        ],
    }

    with pytest.raises(WorkflowSpecError, match="Unsupported step type"):
        parse_tool_workflow_spec(
            specification,
            available_tools=broker.registered_tools,
        )


def test_invalid_workflow_version_is_rejected():
    broker, _, _ = create_broker_and_grant()

    specification = {
        "version": 2,
        "steps": [
            {
                "id": "lookup",
                "type": "tool",
                "tool": "inventory.lookup",
                "arguments": {"sku": "SKU-100"},
            }
        ],
    }

    with pytest.raises(WorkflowSpecError, match="Unsupported workflow version"):
        parse_tool_workflow_spec(
            specification,
            available_tools=broker.registered_tools,
        )


def test_grant_scope_is_still_enforced():
    broker, _context_a, grant_a = create_broker_and_grant()

    context_b = ToolCallContext(
        tenant_id="tenant-a",
        execution_id="execution-b",
    )

    specification = {
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

    graph = build_declarative_tool_workflow(
        broker=broker,
        context=context_b,
        grant=grant_a,
        specification=specification,
    )

    with pytest.raises(GrantScopeError):
        graph.invoke({"step_results": {}})


def test_identity_cannot_be_overridden_through_workflow_state():
    broker, context, grant = create_broker_and_grant()

    specification = {
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

    graph = build_declarative_tool_workflow(
        broker=broker,
        context=context,
        grant=grant,
        specification=specification,
    )

    result = graph.invoke(
        {
            "step_results": {},
            "tenant_id": "tenant-attacker",
            "execution_id": "execution-attacker",
            "grant_token": "attacker-token",
        }
    )

    assert result["step_results"]["lookup"]["found"] is True
