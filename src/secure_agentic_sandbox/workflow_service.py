"""Platform service for trusted declarative workflow execution."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from secure_agentic_sandbox.declarative_workflow import (
    PythonWorkflowStep,
    ToolWorkflowStep,
    build_declarative_tool_workflow,
    parse_tool_workflow_spec,
)
from secure_agentic_sandbox.tool_broker import (
    GrantCredential,
    ToolBroker,
    ToolCallContext,
)

WorkflowStatus = Literal[
    "succeeded",
    "failed",
    "timed_out",
    "resource_exhausted",
]


class WorkflowAuthorizationError(PermissionError):
    """Raised when a workflow requests unauthorized tools."""


@dataclass(frozen=True, slots=True)
class WorkflowExecutionResult:
    """Result returned by the platform workflow execution service."""

    execution_id: str
    status: WorkflowStatus
    step_results: dict[str, dict[str, object]]
    tools_used: tuple[str, ...]
    grant_id: str | None


class WorkflowExecutionService:
    """Trusted controller for custom declarative workflow executions.

    The caller must supply tenant identity and allowed tools from trusted
    platform authentication and authorization logic, never from workflow JSON.
    """

    def __init__(
        self,
        broker: ToolBroker,
        *,
        grant_ttl_seconds: float = 300,
        max_tool_calls: int = 50,
    ) -> None:
        if not isinstance(broker, ToolBroker):
            raise TypeError("A valid ToolBroker instance is required.")

        self._broker = broker
        self._grant_ttl_seconds = grant_ttl_seconds
        self._max_tool_calls = max_tool_calls

    async def execute(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        specification: Mapping[str, object],
        allowed_tools: Iterable[str],
    ) -> WorkflowExecutionResult:
        """Validate, authorize, compile and execute one workflow."""

        context = ToolCallContext(
            tenant_id=tenant_id,
            execution_id=execution_id,
        )

        if isinstance(allowed_tools, (str, bytes)):
            raise TypeError("Allowed tools must be an iterable of names.")

        try:
            permitted_tools = frozenset(allowed_tools)
        except TypeError as exc:
            raise TypeError(
                "Allowed tools must be an iterable of names."
            ) from exc

        if any(not isinstance(name, str) for name in permitted_tools):
            raise TypeError("Allowed tool names must be strings.")

        definition = parse_tool_workflow_spec(
            specification,
            available_tools=self._broker.registered_tools,
        )

        requested_tools = frozenset(
            step.tool_name
            for step in definition.steps
            if isinstance(step, ToolWorkflowStep)
        )

        denied_tools = requested_tools.difference(permitted_tools)

        if denied_tools:
            denied_names = ", ".join(sorted(denied_tools))
            raise WorkflowAuthorizationError(
                f"Workflow requests tools not permitted by platform policy: "
                f"{denied_names}"
            )

        grant: GrantCredential | None = None

        if requested_tools:
            # Least privilege: grant only tools actually used by this workflow.
            grant = self._broker.create_grant(
                tenant_id=tenant_id,
                execution_id=execution_id,
                allowed_tools=requested_tools,
                ttl_seconds=self._grant_ttl_seconds,
                max_calls=self._max_tool_calls,
            )

        primary_error: BaseException | None = None

        try:
            graph = build_declarative_tool_workflow(
                broker=self._broker,
                context=context,
                grant=grant,
                specification=specification,
            )

            state = await graph.ainvoke({"step_results": {}})
            step_results = state.get("step_results", {})

            status = self._classify_status(
                definition.steps,
                step_results,
            )

            return WorkflowExecutionResult(
                execution_id=execution_id,
                status=status,
                step_results=step_results,
                tools_used=tuple(sorted(requested_tools)),
                grant_id=grant.grant_id if grant else None,
            )

        except BaseException as exc:
            primary_error = exc
            raise

        finally:
            if grant is not None:
                try:
                    self._broker.revoke_grant(
                        grant.grant_id,
                        context=context,
                    )
                except Exception as cleanup_error:
                    if primary_error is None:
                        raise

                    primary_error.add_note(
                        "Broker grant revocation also failed: "
                        f"{cleanup_error!r}"
                    )

    @staticmethod
    def _classify_status(
        steps: tuple[ToolWorkflowStep | PythonWorkflowStep, ...],
        step_results: Mapping[str, dict[str, object]],
    ) -> WorkflowStatus:
        """Classify the overall result using Python execution outcomes."""

        for step in steps:
            if not isinstance(step, PythonWorkflowStep):
                continue

            result = step_results.get(step.step_id)

            if result is None:
                return "failed"

            if result.get("oom_killed") is True:
                return "resource_exhausted"

            if result.get("timed_out") is True:
                return "timed_out"

            if result.get("exit_code") != 0:
                return "failed"

        return "succeeded"
