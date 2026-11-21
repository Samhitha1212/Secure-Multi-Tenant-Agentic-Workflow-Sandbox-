"""Trusted LangGraph workflow for secure tool invocation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

from secure_agentic_sandbox.tool_broker import (
    GrantCredential,
    ToolBroker,
    ToolCallContext,
)


class ToolWorkflowState(TypedDict):
    """State for one trusted tool invocation."""

    tool_name: str
    arguments: Mapping[str, object]
    tool_result: NotRequired[dict[str, object] | None]


def build_tool_workflow(
    *,
    broker: ToolBroker,
    context: ToolCallContext,
    grant: GrantCredential,
):
    """Build a tool workflow bound to trusted execution credentials.

    Security properties:
    - Tenant and execution identity come from the trusted controller.
    - Grant credentials are captured by the trusted node.
    - Neither identity nor grant token is accepted from workflow state.
    - All calls pass through ToolBroker authorization and validation.
    """

    if not isinstance(broker, ToolBroker):
        raise TypeError("A valid ToolBroker instance is required.")

    if not isinstance(context, ToolCallContext):
        raise TypeError("A trusted ToolCallContext is required.")

    if not isinstance(grant, GrantCredential):
        raise TypeError("A valid GrantCredential is required.")

    # These values are captured from trusted platform-side configuration.
    # They are deliberately not part of ToolWorkflowState.
    trusted_context = context
    trusted_grant_token = grant.token

    def invoke_tool(
        state: ToolWorkflowState,
    ) -> dict[str, object]:
        """Invoke one approved tool through the secure broker."""

        result = broker.invoke(
            context=trusted_context,
            grant_token=trusted_grant_token,
            tool_name=state["tool_name"],
            arguments=state["arguments"],
        )

        return {"tool_result": result}

    graph = StateGraph(ToolWorkflowState)

    graph.add_node("invoke_tool", invoke_tool)

    graph.add_edge(START, "invoke_tool")
    graph.add_edge("invoke_tool", END)

    return graph.compile()