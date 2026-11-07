"""Tests for the trusted LangGraph tool workflow."""

import pytest

from secure_agentic_sandbox.tool_broker import (
    GrantScopeError,
    ToolCallContext,
    create_mock_tool_broker,
)
from secure_agentic_sandbox.tool_workflow import build_tool_workflow


def create_workflow(
    *,
    allowed_tools=None,
    include_reservation=False,
):
    """Create a broker, grant and trusted tool workflow."""

    broker = create_mock_tool_broker(
        include_reservation=include_reservation,
    )

    context = ToolCallContext(
        tenant_id="tenant-a",
        execution_id="execution-a",
    )

    if allowed_tools is None:
        allowed_tools = {"inventory.lookup"}

    grant = broker.create_grant(
        tenant_id=context.tenant_id,
        execution_id=context.execution_id,
        allowed_tools=allowed_tools,
        max_calls=20,
    )

    graph = build_tool_workflow(
        broker=broker,
        context=context,
        grant=grant,
    )

    return broker, context, grant, graph


def test_langgraph_can_invoke_inventory_lookup():
    _, _, _, graph = create_workflow()

    result = graph.invoke(
        {
            "tool_name": "inventory.lookup",
            "arguments": {"sku": "SKU-100"},
        }
    )

    tool_result = result["tool_result"]

    assert tool_result["found"] is True
    assert tool_result["sku"] == "SKU-100"
    assert tool_result["quantity"] == 12


def test_langgraph_can_invoke_inventory_reservation():
    _, _, _, graph = create_workflow(
        allowed_tools={
            "inventory.lookup",
            "inventory.reserve",
        },
        include_reservation=True,
    )

    result = graph.invoke(
        {
            "tool_name": "inventory.reserve",
            "arguments": {
                "sku": "SKU-100",
                "quantity": 3,
                "idempotency_key": "workflow-reservation-1",
            },
        }
    )

    tool_result = result["tool_result"]

    assert tool_result["status"] == "reserved"
    assert tool_result["quantity"] == 3
    assert tool_result["remaining_quantity"] == 9


def test_tool_context_cannot_be_overridden_through_graph_state():
    _, _, _, graph = create_workflow()

    # These additional fields are untrusted input.
    # The graph's trusted node must ignore them.
    result = graph.invoke(
        {
            "tool_name": "inventory.lookup",
            "arguments": {"sku": "SKU-100"},
            "tenant_id": "tenant-attacker",
            "execution_id": "execution-attacker",
            "grant_token": "attacker-token",
        }
    )

    assert result["tool_result"]["found"] is True
    assert result["tool_result"]["sku"] == "SKU-100"


def test_grant_from_another_execution_is_rejected():
    broker = create_mock_tool_broker()

    context_a = ToolCallContext(
        tenant_id="tenant-a",
        execution_id="execution-a",
    )

    context_b = ToolCallContext(
        tenant_id="tenant-a",
        execution_id="execution-b",
    )

    grant_a = broker.create_grant(
        tenant_id=context_a.tenant_id,
        execution_id=context_a.execution_id,
        allowed_tools={"inventory.lookup"},
    )

    # Deliberately bind execution B to execution A's credential.
    graph_b = build_tool_workflow(
        broker=broker,
        context=context_b,
        grant=grant_a,
    )

    with pytest.raises(GrantScopeError):
        graph_b.invoke(
            {
                "tool_name": "inventory.lookup",
                "arguments": {"sku": "SKU-100"},
            }
        )


def test_tool_outside_grant_scope_is_rejected():
    _, _, _, graph = create_workflow(
        allowed_tools={"inventory.lookup"},
        include_reservation=True,
    )

    with pytest.raises(GrantScopeError):
        graph.invoke(
            {
                "tool_name": "inventory.reserve",
                "arguments": {
                    "sku": "SKU-100",
                    "quantity": 1,
                    "idempotency_key": "unauthorized-reservation",
                },
            }
        )