"""Compile validated declarative workflows into trusted LangGraph graphs."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from secure_agentic_sandbox import runner
from secure_agentic_sandbox.execution_config import (
    DEFAULT_CPU,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_MEMORY,
    DEFAULT_TIMEOUT_SECONDS,
    ExecutionConfig,
)
from secure_agentic_sandbox.tool_broker import (
    GrantCredential,
    ToolBroker,
    ToolCallContext,
)

MAX_WORKFLOW_STEPS = 20
MAX_WORKFLOW_SPEC_BYTES = 64 * 1024

STEP_ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
TOOL_NAME_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
REFERENCE_PATTERN = re.compile(
    r"[a-z][a-z0-9_-]{0,63}(?:\.[A-Za-z_][A-Za-z0-9_-]*)*\Z"
)


class WorkflowSpecError(ValueError):
    """Raised when a declarative workflow specification is invalid."""


class WorkflowReferenceError(RuntimeError):
    """Raised when a workflow result reference cannot be resolved."""


@dataclass(frozen=True, slots=True)
class ToolWorkflowStep:
    """One validated tool invocation."""

    step_id: str
    tool_name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class PythonWorkflowStep:
    """One validated isolated Python execution."""

    step_id: str
    source: str
    timeout_seconds: int
    cpu: str
    memory: str
    max_output_bytes: int
    inputs: dict[str, object]


WorkflowStep = ToolWorkflowStep | PythonWorkflowStep


@dataclass(frozen=True, slots=True)
class ToolWorkflowDefinition:
    """Validated versioned workflow definition."""

    version: int
    steps: tuple[WorkflowStep, ...]


class DeclarativeWorkflowState(TypedDict):
    """State maintained by the compiled workflow."""

    step_results: dict[str, dict[str, object]]
    pause_requested: bool


def _to_json_value(value: object) -> object:
    """Convert supported JSON values while rejecting invalid object keys."""

    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}

        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings.")

            normalized[key] = _to_json_value(item)

        return normalized

    if isinstance(value, list):
        return [_to_json_value(item) for item in value]

    if value is None or isinstance(value, (str, bool, int, float)):
        return value

    raise TypeError("Value is not JSON-compatible.")


def _normalize_json_object(
    value: object,
    description: str,
) -> dict[str, object]:
    """Validate and normalize a JSON object."""

    if not isinstance(value, Mapping):
        raise WorkflowSpecError(f"{description} must be an object.")

    try:
        normalized = _to_json_value(value)

        if not isinstance(normalized, dict):
            raise TypeError("Expected a JSON object.")

        encoded = json.dumps(normalized, allow_nan=False)
        decoded = json.loads(encoded)

    except (TypeError, ValueError, RecursionError) as exc:
        raise WorkflowSpecError(
            f"{description} must contain JSON-compatible values."
        ) from exc

    if not isinstance(decoded, dict):
        raise WorkflowSpecError(f"{description} must be an object.")

    return decoded


def _validate_references(
    value: object,
    previous_step_ids: frozenset[str],
    description: str,
) -> None:
    """Check that references point only to earlier workflow steps."""

    if isinstance(value, Mapping):
        if "$ref" in value:
            if set(value) != {"$ref"}:
                raise WorkflowSpecError(
                    f"Invalid reference expression in {description}. "
                    "A reference object must contain only '$ref'."
                )

            reference = value["$ref"]

            if (
                not isinstance(reference, str)
                or REFERENCE_PATTERN.fullmatch(reference) is None
            ):
                raise WorkflowSpecError(
                    f"Invalid reference path in {description}."
                )

            referenced_step = reference.split(".", maxsplit=1)[0]

            if referenced_step not in previous_step_ids:
                raise WorkflowSpecError(
                    f"Reference '{reference}' in {description} must point "
                    "to an earlier workflow step."
                )

            return

        for item in value.values():
            _validate_references(
                item,
                previous_step_ids,
                description,
            )

    elif isinstance(value, list):
        for item in value:
            _validate_references(
                item,
                previous_step_ids,
                description,
            )


def _resolve_references(
    value: object,
    step_results: Mapping[str, dict[str, object]],
) -> object:
    """Resolve references recursively against completed step results."""

    if isinstance(value, Mapping):
        if "$ref" in value:
            if set(value) != {"$ref"}:
                raise WorkflowReferenceError(
                    "Invalid runtime reference expression."
                )

            reference = value["$ref"]

            if (
                not isinstance(reference, str)
                or REFERENCE_PATTERN.fullmatch(reference) is None
            ):
                raise WorkflowReferenceError("Invalid runtime reference path.")

            segments = reference.split(".")
            current: object = step_results.get(segments[0])

            if current is None:
                raise WorkflowReferenceError(
                    f"Workflow result is unavailable: {segments[0]}"
                )

            for segment in segments[1:]:
                if not isinstance(current, Mapping):
                    raise WorkflowReferenceError(
                        f"Cannot resolve '{reference}': "
                        f"'{segment}' is not an object field."
                    )

                if segment not in current:
                    raise WorkflowReferenceError(
                        f"Workflow result field does not exist: {reference}"
                    )

                current = current[segment]

            return current

        return {
            key: _resolve_references(item, step_results)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            _resolve_references(item, step_results)
            for item in value
        ]

    return value


def parse_tool_workflow_spec(
    specification: Mapping[str, object],
    *,
    available_tools: Iterable[str],
) -> ToolWorkflowDefinition:
    """Validate and normalize a declarative workflow specification."""

    normalized_spec = _normalize_json_object(
        specification,
        "Workflow specification",
    )

    encoded_spec = json.dumps(
        normalized_spec,
        allow_nan=False,
        separators=(",", ":"),
    )

    if len(encoded_spec.encode("utf-8")) > MAX_WORKFLOW_SPEC_BYTES:
        raise WorkflowSpecError("Workflow specification is too large.")

    if set(normalized_spec) != {"version", "steps"}:
        raise WorkflowSpecError(
            "Workflow specification requires exactly: version and steps."
        )

    version = normalized_spec["version"]

    if isinstance(version, bool) or not isinstance(version, int):
        raise WorkflowSpecError("Workflow version must be an integer.")

    if version != 1:
        raise WorkflowSpecError(f"Unsupported workflow version: {version}")

    raw_steps = normalized_spec["steps"]

    if not isinstance(raw_steps, list):
        raise WorkflowSpecError("Workflow steps must be a list.")

    if not 1 <= len(raw_steps) <= MAX_WORKFLOW_STEPS:
        raise WorkflowSpecError(
            f"Workflow must contain between 1 and {MAX_WORKFLOW_STEPS} steps."
        )

    if isinstance(available_tools, (str, bytes)):
        raise WorkflowSpecError("Available tools must be an iterable of names.")

    try:
        registered_tools = frozenset(available_tools)
    except TypeError as exc:
        raise WorkflowSpecError(
            "Available tools must be an iterable of names."
        ) from exc

    if any(not isinstance(name, str) for name in registered_tools):
        raise WorkflowSpecError("Available tool names must be strings.")

    validated_steps: list[WorkflowStep] = []
    seen_ids: set[str] = set()

    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, Mapping):
            raise WorkflowSpecError(f"Step {index} must be an object.")

        step_id = raw_step.get("id")
        step_type = raw_step.get("type")

        if (
            not isinstance(step_id, str)
            or STEP_ID_PATTERN.fullmatch(step_id) is None
        ):
            raise WorkflowSpecError(f"Invalid step ID at position {index}.")

        if step_id in seen_ids:
            raise WorkflowSpecError(f"Duplicate step ID: {step_id}")

        previous_step_ids = frozenset(seen_ids)

        if step_type == "tool":
            if set(raw_step) != {"id", "type", "tool", "arguments"}:
                raise WorkflowSpecError(
                    f"Tool step '{step_id}' requires exactly: "
                    "id, type, tool and arguments."
                )

            tool_name = raw_step["tool"]
            arguments = _normalize_json_object(
                raw_step["arguments"],
                f"Arguments for step '{step_id}'",
            )

            if (
                not isinstance(tool_name, str)
                or TOOL_NAME_PATTERN.fullmatch(tool_name) is None
            ):
                raise WorkflowSpecError(
                    f"Invalid tool name in step '{step_id}'."
                )

            if tool_name not in registered_tools:
                raise WorkflowSpecError(
                    f"Tool is not registered: {tool_name}"
                )

            _validate_references(
                arguments,
                previous_step_ids,
                f"arguments for step '{step_id}'",
            )

            validated_steps.append(
                ToolWorkflowStep(
                    step_id=step_id,
                    tool_name=tool_name,
                    arguments=arguments,
                )
            )

        elif step_type == "python":
            required_fields = {"id", "type", "source"}
            optional_fields = {
                "timeout_seconds",
                "cpu",
                "memory",
                "max_output_bytes",
                "inputs",
            }

            if (
                not required_fields.issubset(raw_step)
                or set(raw_step) - required_fields - optional_fields
            ):
                raise WorkflowSpecError(
                    f"Python step '{step_id}' requires id, type and source; "
                    "optional fields are timeout_seconds, cpu, memory, "
                    "max_output_bytes and inputs."
                )

            try:
                source = runner.validate_python_source(raw_step["source"])

                execution_config = ExecutionConfig(
                    cpu=raw_step.get("cpu", DEFAULT_CPU),
                    memory=raw_step.get("memory", DEFAULT_MEMORY),
                    timeout_seconds=raw_step.get(
                        "timeout_seconds",
                        DEFAULT_TIMEOUT_SECONDS,
                    ),
                    max_output_bytes=raw_step.get(
                        "max_output_bytes",
                        DEFAULT_MAX_OUTPUT_BYTES,
                    ),
                )

            except (TypeError, ValueError) as exc:
                raise WorkflowSpecError(
                    f"Invalid Python step '{step_id}': {exc}"
                ) from exc

            inputs = _normalize_json_object(
                raw_step.get("inputs", {}),
                f"Inputs for Python step '{step_id}'",
            )

            _validate_references(
                inputs,
                previous_step_ids,
                f"inputs for Python step '{step_id}'",
            )

            validated_steps.append(
                PythonWorkflowStep(
                    step_id=step_id,
                    source=source,
                    timeout_seconds=execution_config.timeout_seconds,
                    cpu=execution_config.cpu,
                    memory=execution_config.memory,
                    max_output_bytes=execution_config.max_output_bytes,
                    inputs=inputs,
                )
            )

        else:
            raise WorkflowSpecError(
                f"Unsupported step type in '{step_id}'. "
                "Supported types are 'tool' and 'python'."
            )

        seen_ids.add(step_id)

    return ToolWorkflowDefinition(
        version=version,
        steps=tuple(validated_steps),
    )


def build_declarative_tool_workflow(
    *,
    broker: ToolBroker,
    context: ToolCallContext,
    grant: GrantCredential | None,
    specification: Mapping[str, object],
    on_step_complete: Callable[
        [int, dict[str, dict[str, object]]], bool
    ] | None = None,
    should_pause: Callable[[], bool] | None = None,
    start_step_index: int = 0,
):
    """Validate and compile a declarative workflow.

    Trusted identity and grant credentials are captured by platform-created
    nodes. They are never read from workflow state or user-provided references.

    Python steps execute through the isolated OpenSandbox runner.
    """

    if not isinstance(broker, ToolBroker):
        raise TypeError("A valid ToolBroker instance is required.")

    if not isinstance(context, ToolCallContext):
        raise TypeError("A trusted ToolCallContext is required.")

    if grant is not None and not isinstance(grant, GrantCredential):
        raise TypeError("A valid GrantCredential is required.")

    if on_step_complete is not None and not callable(on_step_complete):
        raise TypeError("on_step_complete must be callable.")

    if should_pause is not None and not callable(should_pause):
        raise TypeError("should_pause must be callable.")

    definition = parse_tool_workflow_spec(
        specification,
        available_tools=broker.registered_tools,
    )

    if (
        isinstance(start_step_index, bool)
        or not isinstance(start_step_index, int)
        or not 0 <= start_step_index < len(definition.steps)
    ):
        raise ValueError("Invalid workflow start step index.")

    graph = StateGraph(DeclarativeWorkflowState)

    def make_step_node(step: WorkflowStep, step_index: int):
        """Create a trusted node for one validated workflow step."""

        if isinstance(step, ToolWorkflowStep):

            def invoke_tool(
                state: DeclarativeWorkflowState,
            ) -> dict[str, object]:
                if should_pause is not None and should_pause():
                    return {"pause_requested": True}

                if grant is None:
                    raise RuntimeError(
                        "A tool invocation requires a broker grant."
                    )

                previous_results = state.get("step_results", {})
                resolved_arguments = _resolve_references(
                    step.arguments,
                    previous_results,
                )

                if not isinstance(resolved_arguments, Mapping):
                    raise WorkflowReferenceError(
                        f"Resolved arguments for '{step.step_id}' "
                        "must be an object."
                    )

                result = broker.invoke(
                    context=context,
                    grant_token=grant.token,
                    tool_name=step.tool_name,
                    arguments=resolved_arguments,
                )

                updated_results = dict(previous_results)
                updated_results[step.step_id] = result

                pause_requested = (
                    on_step_complete(step_index + 1, updated_results)
                    if on_step_complete is not None
                    else False
                )

                return {
                    "step_results": updated_results,
                    "pause_requested": pause_requested,
                }

            return invoke_tool

        async def execute_python(
            state: DeclarativeWorkflowState,
        ) -> dict[str, object]:
            if should_pause is not None and should_pause():
                return {"pause_requested": True}

            previous_results = state.get("step_results", {})
            resolved_inputs = _resolve_references(
                step.inputs,
                previous_results,
            )

            if not isinstance(resolved_inputs, Mapping):
                raise WorkflowReferenceError(
                    f"Resolved inputs for '{step.step_id}' must be an object."
                )

            result = await runner.run_python_source(
                step.source,
                timeout_seconds=step.timeout_seconds,
                cpu=step.cpu,
                memory=step.memory,
                max_output_bytes=step.max_output_bytes,
                input_data=resolved_inputs,
            )

            updated_results = dict(previous_results)
            updated_results[step.step_id] = asdict(result)

            pause_requested = False
            if (
                on_step_complete is not None
                and result.exit_code == 0
                and not result.timed_out
                and not result.oom_killed
            ):
                pause_requested = on_step_complete(
                    step_index + 1,
                    updated_results,
                )

            return {
                "step_results": updated_results,
                "pause_requested": pause_requested,
            }

        return execute_python

    def make_step_route(step: WorkflowStep):
        """Stop after a pause request or an unsuccessful Python step."""

        def route_after_step(state: DeclarativeWorkflowState) -> str:
            if state.get("pause_requested") is True:
                return "stop"

            if isinstance(step, PythonWorkflowStep):
                result = state.get("step_results", {}).get(step.step_id)

                if result is None:
                    return "stop"

                if result.get("timed_out") is True:
                    return "stop"

                if result.get("oom_killed") is True:
                    return "stop"

                if result.get("exit_code") != 0:
                    return "stop"

            return "continue"

        return route_after_step

    for index, step in enumerate(definition.steps):
        graph.add_node(step.step_id, make_step_node(step, index))

    graph.add_edge(START, definition.steps[start_step_index].step_id)

    for index, step in enumerate(definition.steps):
        if index == len(definition.steps) - 1:
            graph.add_edge(step.step_id, END)
            continue

        next_step = definition.steps[index + 1]

        graph.add_conditional_edges(
            step.step_id,
            make_step_route(step),
            {
                "continue": next_step.step_id,
                "stop": END,
            },
        )

    return graph.compile()
