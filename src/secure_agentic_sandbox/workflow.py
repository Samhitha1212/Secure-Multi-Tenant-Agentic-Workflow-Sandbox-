"""Initial platform-defined LangGraph workflow."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Literal, TypedDict, cast
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from secure_agentic_sandbox import runner

WorkflowStatus = Literal[
    "pending",
    "validation_failed",
    "succeeded",
    "failed",
    "timed_out",
]


class WorkflowState(TypedDict):
    """State maintained throughout one workflow execution."""

    execution_id: str
    submission_path: str
    timeout_seconds: int
    validation_error: str | None
    execution_result: dict[str, object] | None
    status: WorkflowStatus


def validate_submission(state: WorkflowState) -> dict[str, object]:
    """Validate the submitted Python file before execution."""

    timeout = state["timeout_seconds"]

    if (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or not 1 <= timeout <= runner.MAX_TIMEOUT_SECONDS
    ):
        return {
            "validation_error": (
                f"Timeout must be between 1 and "
                f"{runner.MAX_TIMEOUT_SECONDS} seconds."
            ),
            "status": "validation_failed",
        }

    try:
        runner.load_python_source(state["submission_path"])
    except (OSError, ValueError) as exc:
        return {
            "validation_error": str(exc),
            "status": "validation_failed",
        }

    return {"validation_error": None}


def route_after_validation(
    state: WorkflowState,
) -> Literal["execute", "finish"]:
    """Skip execution when submission validation fails."""

    if state["validation_error"] is not None:
        return "finish"

    return "execute"


async def execute_submission(
    state: WorkflowState,
) -> dict[str, object]:
    """Execute submitted code through the existing sandbox runner."""

    result = await runner.run_python_file(
        state["submission_path"],
        timeout_seconds=state["timeout_seconds"],
    )

    return {
        "execution_result": asdict(result),
    }


def classify_execution(
    state: WorkflowState,
) -> dict[str, object]:
    """Determine the final workflow status."""

    result = state["execution_result"]

    if result is None:
        return {"status": "failed"}

    if result["timed_out"]:
        return {"status": "timed_out"}

    if result["exit_code"] == 0:
        return {"status": "succeeded"}

    return {"status": "failed"}


def build_workflow():
    """Build the platform-defined execution workflow."""

    graph = StateGraph(WorkflowState)

    graph.add_node("validate", validate_submission)
    graph.add_node("execute", execute_submission)
    graph.add_node("classify", classify_execution)

    graph.add_edge(START, "validate")

    graph.add_conditional_edges(
        "validate",
        route_after_validation,
        {
            "execute": "execute",
            "finish": END,
        },
    )

    graph.add_edge("execute", "classify")
    graph.add_edge("classify", END)

    return graph.compile()


workflow_graph = build_workflow()


async def run_workflow(
    submission_path: str | Path,
    timeout_seconds: int = runner.DEFAULT_TIMEOUT_SECONDS,
    *,
    execution_id: str | None = None,
) -> WorkflowState:
    """Create and execute one platform-managed workflow instance."""

    if execution_id is None:
        execution_id = str(uuid4())
    elif not execution_id.strip():
        raise ValueError("Execution ID cannot be empty.")

    initial_state: WorkflowState = {
        "execution_id": execution_id,
        "submission_path": str(submission_path),
        "timeout_seconds": timeout_seconds,
        "validation_error": None,
        "execution_result": None,
        "status": "pending",
    }

    result = await workflow_graph.ainvoke(initial_state)

    return cast(WorkflowState, result)