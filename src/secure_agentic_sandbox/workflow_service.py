"""Platform service for trusted declarative workflow execution."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from secure_agentic_sandbox.checkpoint_store import (
    CheckpointNotFoundError,
    CheckpointStore,
)
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
    "paused",
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
        checkpoint_store: CheckpointStore | None = None,
    ) -> None:
        if not isinstance(broker, ToolBroker):
            raise TypeError("A valid ToolBroker instance is required.")

        if (
            checkpoint_store is not None
            and not isinstance(checkpoint_store, CheckpointStore)
        ):
            raise TypeError("A valid CheckpointStore instance is required.")

        self._broker = broker
        self._grant_ttl_seconds = grant_ttl_seconds
        self._max_tool_calls = max_tool_calls
        self._checkpoint_store = checkpoint_store

    async def execute(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        specification: Mapping[str, object],
        allowed_tools: Iterable[str],
    ) -> WorkflowExecutionResult:
        """Start a new workflow execution."""

        return await self._execute_internal(
            tenant_id=tenant_id,
            execution_id=execution_id,
            specification=specification,
            allowed_tools=allowed_tools,
            resume_mode=False,
        )

    async def resume(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        allowed_tools: Iterable[str],
    ) -> WorkflowExecutionResult:
        """Resume a paused workflow using its original tool permissions."""

        if self._checkpoint_store is None:
            raise RuntimeError(
                "Checkpoint storage is required to resume workflows."
            )

        checkpoint = self._checkpoint_store.get_checkpoint(
            tenant_id=tenant_id,
            execution_id=execution_id,
        )

        if checkpoint is None:
            raise CheckpointNotFoundError(
                "Checkpoint not found for this tenant."
            )

        # Old checkpoints do not contain the original permission policy.
        # Never infer permissions from the current tenant policy.
        if checkpoint.allowed_tools is None:
            raise WorkflowAuthorizationError(
                "Cannot resume this checkpoint because its original "
                "tool permissions are unavailable."
            )

        if isinstance(allowed_tools, (str, bytes)):
            raise TypeError("Allowed tools must be an iterable of names.")

        try:
            current_tools = frozenset(allowed_tools)
        except TypeError as exc:
            raise TypeError(
                "Allowed tools must be an iterable of names."
            ) from exc

        if any(not isinstance(name, str) for name in current_tools):
            raise TypeError("Allowed tool names must be strings.")

        # Never expand the original permissions.
        resume_allowed_tools = (
            checkpoint.allowed_tools
            & current_tools
            & frozenset(self._broker.registered_tools)
        )

        return await self._execute_internal(
            tenant_id=tenant_id,
            execution_id=execution_id,
            specification=checkpoint.specification,
            allowed_tools=resume_allowed_tools,
            resume_mode=True,
        )

    async def _execute_internal(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        specification: Mapping[str, object],
        allowed_tools: Iterable[str],
        resume_mode: bool,
    ) -> WorkflowExecutionResult:
        """Validate, authorize and execute or resume a workflow."""

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
        checkpoint_created = False
        checkpoint_next_step_index = 0
        checkpointed_step_results: dict[str, dict[str, object]] = {}
        initial_step_results: dict[str, dict[str, object]] = {}

        try:
            checkpoint_callback = None
            pause_check = None

            if self._checkpoint_store is not None:
                if resume_mode:
                    resumed_checkpoint = (
                        self._checkpoint_store.claim_for_resume(
                            tenant_id=tenant_id,
                            execution_id=execution_id,
                        )
                    )
                    checkpoint_next_step_index = (
                        resumed_checkpoint.next_step_index
                    )
                    checkpointed_step_results = (
                        self._checkpoint_safe_step_results(
                            resumed_checkpoint.step_results
                        )
                    )
                    initial_step_results = checkpointed_step_results
                else:
                   self._checkpoint_store.create_checkpoint(
                        tenant_id=tenant_id,
                        execution_id=execution_id,
                        workflow_version=definition.version,
                        specification=specification,
                        allowed_tools=permitted_tools,
                    )

                checkpoint_created = True

                def persist_step(
                    next_step_index: int,
                    step_results: dict[str, dict[str, object]],
                ) -> bool:
                    nonlocal checkpoint_next_step_index
                    nonlocal checkpointed_step_results

                    safe_results = self._checkpoint_safe_step_results(
                        step_results
                    )
                    saved_checkpoint = self._checkpoint_store.update_progress(
                        tenant_id=tenant_id,
                        execution_id=execution_id,
                        next_step_index=next_step_index,
                        step_results=safe_results,
                        status="running",
                        expected_next_step_index=checkpoint_next_step_index,
                    )
                    checkpoint_next_step_index = next_step_index
                    checkpointed_step_results = safe_results
                    return saved_checkpoint.status == "paused"

                def should_pause() -> bool:
                    return self._checkpoint_store.pause_if_requested(
                        tenant_id=tenant_id,
                        execution_id=execution_id,
                    )

                checkpoint_callback = persist_step
                pause_check = should_pause

            graph = build_declarative_tool_workflow(
                broker=self._broker,
                context=context,
                grant=grant,
                specification=specification,
                on_step_complete=checkpoint_callback,
                should_pause=pause_check,
                start_step_index=checkpoint_next_step_index,
            )

            state = await graph.ainvoke(
                {"step_results": initial_step_results}
            )
            step_results = state.get("step_results", {})

            checkpoint = None
            if self._checkpoint_store is not None:
                checkpoint = self._checkpoint_store.get_checkpoint(
                    tenant_id=tenant_id,
                    execution_id=execution_id,
                )

            if checkpoint is not None and checkpoint.status == "paused":
                status: WorkflowStatus = "paused"
            else:
                status = self._classify_status(
                    definition.steps,
                    step_results,
                )

                if self._checkpoint_store is not None:
                    self._checkpoint_store.update_progress(
                        tenant_id=tenant_id,
                        execution_id=execution_id,
                        next_step_index=checkpoint_next_step_index,
                        step_results=checkpointed_step_results,
                        status=(
                            "completed"
                            if status == "succeeded"
                            else "failed"
                        ),
                        expected_next_step_index=checkpoint_next_step_index,
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

            if checkpoint_created and self._checkpoint_store is not None:
                try:
                    checkpoint = self._checkpoint_store.get_checkpoint(
                        tenant_id=tenant_id,
                        execution_id=execution_id,
                    )
                    if checkpoint is not None and checkpoint.status == "running":
                        self._checkpoint_store.update_progress(
                            tenant_id=tenant_id,
                            execution_id=execution_id,
                            next_step_index=checkpoint.next_step_index,
                            step_results=checkpoint.step_results,
                            status="failed",
                            expected_next_step_index=checkpoint.next_step_index,
                        )
                # Preserve the original error if best-effort cleanup fails.
                except Exception as checkpoint_error:  # noqa: BLE001
                    exc.add_note(
                        "Checkpoint failure handling also failed: "
                        f"{checkpoint_error!r}"
                    )

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
    def _checkpoint_safe_step_results(
        step_results: Mapping[str, dict[str, object]],
    ) -> dict[str, dict[str, object]]:
        """Remove ephemeral sandbox identifiers before persistence."""

        return {
            step_id: {
                key: value
                for key, value in result.items()
                if key != "sandbox_id"
            }
            for step_id, result in step_results.items()
        }

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
