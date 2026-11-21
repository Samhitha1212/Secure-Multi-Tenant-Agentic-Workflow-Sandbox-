"""Platform-managed registry of Python and workflow questions."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Literal

from secure_agentic_sandbox.execution_config import (
    DEFAULT_CPU,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_MEMORY,
    DEFAULT_TIMEOUT_SECONDS,
    ExecutionConfig,
)

QuestionType = Literal["python", "workflow"]

QUESTION_ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class QuestionRegistryError(ValueError):
    """Raised when a question definition or registry is invalid."""


class QuestionNotFoundError(LookupError):
    """Raised when a requested question does not exist."""


@dataclass(frozen=True, slots=True)
class QuestionDefinition:
    """One platform-defined coding or workflow question."""

    id: str
    title: str
    type: QuestionType
    description: str
    statement: str
    requirements: tuple[str, ...]
    allowed_tools: frozenset[str] = frozenset()
    resource_config: ExecutionConfig = field(
        default_factory=ExecutionConfig
    )
    starter_code: str | None = None
    starter_workflow: dict[str, object] | None = None

    def __post_init__(self) -> None:
        """Validate question metadata and starter submission."""

        if (
            not isinstance(self.id, str)
            or QUESTION_ID_PATTERN.fullmatch(self.id) is None
        ):
            raise QuestionRegistryError("Invalid question ID.")

        for name, value in (
            ("title", self.title),
            ("description", self.description),
            ("statement", self.statement),
        ):
            if not isinstance(value, str) or not value.strip():
                raise QuestionRegistryError(
                    f"Question {name} cannot be empty."
                )

        if self.type not in ("python", "workflow"):
            raise QuestionRegistryError("Unsupported question type.")

        if not isinstance(self.resource_config, ExecutionConfig):
            raise QuestionRegistryError(
                "Question requires a valid ExecutionConfig."
            )

        if not isinstance(self.requirements, tuple):
            raise QuestionRegistryError(
                "Question requirements must be a tuple."
            )

        if any(
            not isinstance(item, str) or not item.strip()
            for item in self.requirements
        ):
            raise QuestionRegistryError(
                "Question requirements must be non-empty strings."
            )

        if isinstance(self.allowed_tools, (str, bytes)):
            raise QuestionRegistryError(
                "Allowed tools must be a collection of names."
            )

        try:
            tools = frozenset(self.allowed_tools)
        except TypeError as exc:
            raise QuestionRegistryError(
                "Allowed tools must be a collection of names."
            ) from exc

        if any(
            not isinstance(tool, str) or not tool.strip()
            for tool in tools
        ):
            raise QuestionRegistryError(
                "Allowed tool names must be non-empty strings."
            )

        object.__setattr__(self, "allowed_tools", tools)

        if self.type == "python":
            if not isinstance(self.starter_code, str):
                raise QuestionRegistryError(
                    "Python questions require starter_code."
                )

            if self.starter_workflow is not None or tools:
                raise QuestionRegistryError(
                    "Python questions cannot define workflow tools."
                )

        if self.type == "workflow":
            if self.starter_workflow is None:
                raise QuestionRegistryError(
                    "Workflow questions require starter_workflow."
                )

            if self.starter_code is not None:
                raise QuestionRegistryError(
                    "Workflow questions cannot define starter_code."
                )

            try:
                encoded = json.dumps(
                    self.starter_workflow,
                    allow_nan=False,
                )
                normalized = json.loads(encoded)
            except (TypeError, ValueError) as exc:
                raise QuestionRegistryError(
                    "Starter workflow must be JSON-compatible."
                ) from exc

            if not isinstance(normalized, dict):
                raise QuestionRegistryError(
                    "Starter workflow must be a JSON object."
                )

            object.__setattr__(self, "starter_workflow", normalized)

    def resource_limits_dict(self) -> dict[str, object]:
        """Return the platform-controlled resource limits."""

        return {
            "cpu": self.resource_config.cpu,
            "memory": self.resource_config.memory,
            "timeout_seconds": self.resource_config.timeout_seconds,
            "max_output_bytes": self.resource_config.max_output_bytes,
        }

    def to_summary_dict(self) -> dict[str, object]:
        """Return fields needed for the question selection page."""

        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "description": self.description,
            "allowed_tools": sorted(self.allowed_tools),
            "resource_limits": self.resource_limits_dict(),
        }

    def to_detail_dict(self) -> dict[str, object]:
        """Return the complete question definition for the workspace."""

        result = self.to_summary_dict()

        result.update(
            {
                "statement": self.statement,
                "requirements": list(self.requirements),
                "starter_code": self.starter_code,
                "starter_workflow": deepcopy(self.starter_workflow),
            }
        )

        return result


class QuestionRegistry:
    """Read-only collection of platform-defined questions."""

    def __init__(
        self,
        questions: Iterable[QuestionDefinition],
    ) -> None:
        question_map: dict[str, QuestionDefinition] = {}

        for question in questions:
            if not isinstance(question, QuestionDefinition):
                raise QuestionRegistryError(
                    "Registry entries must be QuestionDefinition objects."
                )

            if question.id in question_map:
                raise QuestionRegistryError(
                    f"Duplicate question ID: {question.id}"
                )

            question_map[question.id] = question

        if not question_map:
            raise QuestionRegistryError(
                "Question registry cannot be empty."
            )

        self._questions = question_map

    def list_questions(self) -> tuple[QuestionDefinition, ...]:
        """Return all questions in stable ID order."""

        return tuple(
            self._questions[question_id]
            for question_id in sorted(self._questions)
        )

    def get(self, question_id: str) -> QuestionDefinition:
        """Retrieve one question by its ID."""

        if not isinstance(question_id, str):
            raise QuestionNotFoundError("Invalid question ID.")

        question = self._questions.get(question_id)

        if question is None:
            raise QuestionNotFoundError(
                f"Question not found: {question_id}"
            )

        return question

    def __len__(self) -> int:
        return len(self._questions)


def create_default_question_registry() -> QuestionRegistry:
    """Create the initial demonstration question catalogue."""

    python_question = QuestionDefinition(
        id="python-list-sum",
        title="Python: Sum of a List",
        type="python",
        description=(
            "Write a Python program to calculate the sum of a list "
            "of integers."
        ),
        statement=(
            "Given a predefined list of integers, write a Python program "
            "that calculates and prints the sum of all its elements."
        ),
        requirements=(
            "Use Python 3.11.",
            "Calculate the sum using Python code.",
            "Print the final sum.",
        ),
        resource_config=ExecutionConfig(
            cpu=DEFAULT_CPU,
            memory=DEFAULT_MEMORY,
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            max_output_bytes=DEFAULT_MAX_OUTPUT_BYTES,
        ),
        starter_code=(
            "numbers = [10, 20, 30, 40]\n"
            "\n"
            "# Calculate and print the sum\n"
            "total = sum(numbers)\n"
            "print(total)\n"
        ),
    )

    inventory_question = QuestionDefinition(
        id="inventory-reservation",
        title="Inventory Management Workflow",
        type="workflow",
        description=(
            "Build a workflow that checks inventory and reserves stock "
            "using approved platform tools."
        ),
        statement=(
            "Create a workflow that first checks the availability of "
            "SKU-100 and then reserves two units if the inventory "
            "lookup succeeds. Use the approved inventory tools."
        ),
        requirements=(
            "Use inventory.lookup to retrieve inventory information.",
            "Use inventory.reserve to reserve stock.",
            "Give every reservation an idempotency key.",
            "Use references to pass the SKU from the lookup step.",
        ),
        allowed_tools=frozenset(
            {
                "inventory.lookup",
                "inventory.reserve",
            }
        ),
        resource_config=ExecutionConfig(),
        starter_workflow={
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
                        "sku": {"$ref": "lookup.sku"},
                        "quantity": 2,
                        "idempotency_key": "demo-reservation-001",
                    },
                },
            ],
        },
    )

    hybrid_inventory_question = QuestionDefinition(
        id="inventory-python-fulfillment",
        title="Inventory Fulfillment with Python Steps",
        type="workflow",
        description=(
            "Combine trusted inventory tools with Python validation "
            "and finalization steps."
        ),
        statement=(
            "Create a workflow that looks up SKU-100, validates a "
            "requested quantity of 3 using an isolated Python step, "
            "reserves that quantity through the trusted inventory tool, "
            "and generates a structured confirmation with a second "
            "Python step. The workflow must not report success unless "
            "the reservation tool confirms the reservation."
        ),
        requirements=(
    (
        "Keep the steps in this order: lookup, Python validation, "
        "reservation, Python finalization."
    ),
    (
        "The validation step must consume the lookup result through "
        "its inputs field."
    ),
    (
        "Reject unknown, unavailable, or insufficient stock before "
        "attempting a reservation."
    ),
    (
        "Pass the validated SKU and quantity to inventory.reserve "
        "using $ref references."
    ),
    "Use an idempotency key for the reservation.",
    (
        "The final Python step must verify that the reservation "
        "status is 'reserved' before reporting success."
    ),
    (
        "Python steps must read /tmp/workflow_input.json and write "
        "a JSON object to /tmp/workflow_output.json."
    ),
),
        allowed_tools=frozenset(
            {
                "inventory.lookup",
                "inventory.reserve",
            }
        ),
        resource_config=ExecutionConfig(),
        starter_workflow={
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
                    "id": "validate_request",
                    "type": "python",
                    "source": """import json
from pathlib import Path

inputs = json.loads(
    Path("/tmp/workflow_input.json").read_text(encoding="utf-8")
)
inventory = inputs["inventory"]
requested_quantity = inputs["requested_quantity"]

if not isinstance(inventory, dict) or not inventory.get("found"):
    raise SystemExit("The requested SKU does not exist.")

if inventory.get("available") is not True:
    raise SystemExit("The requested SKU is not available.")

if (
    isinstance(requested_quantity, bool)
    or not isinstance(requested_quantity, int)
    or requested_quantity <= 0
):
    raise SystemExit("Requested quantity must be a positive integer.")

available_quantity = inventory.get("quantity")
if (
    isinstance(available_quantity, bool)
    or not isinstance(available_quantity, int)
    or available_quantity < requested_quantity
):
    raise SystemExit("Insufficient inventory for this reservation.")

result = {
    "sku": inventory["sku"],
    "quantity_to_reserve": requested_quantity,
    "available_quantity": available_quantity,
}
Path("/tmp/workflow_output.json").write_text(
    json.dumps(result), encoding="utf-8"
)
print(
    f"Validated {requested_quantity} unit(s) of {inventory['sku']} "
    f"from {available_quantity} available."
)
""",
                    "inputs": {
                        "inventory": {"$ref": "lookup"},
                        "requested_quantity": 3,
                    },
                },
                {
                    "id": "reserve",
                    "type": "tool",
                    "tool": "inventory.reserve",
                    "arguments": {
                        "sku": {
                            "$ref": "validate_request.output.sku"
                        },
                        "quantity": {
                            "$ref": "validate_request.output.quantity_to_reserve"
                        },
                        "idempotency_key": "hybrid-inventory-reservation-001",
                    },
                },
                {
                    "id": "finalize",
                    "type": "python",
                    "source": """import json
from pathlib import Path

inputs = json.loads(
    Path("/tmp/workflow_input.json").read_text(encoding="utf-8")
)
reservation = inputs["reservation"]
validation = inputs["validation"]

if not isinstance(reservation, dict):
    raise SystemExit("Invalid reservation result.")

if reservation.get("status") != "reserved":
    raise SystemExit(
        f"Reservation was not completed: {reservation.get('status')}"
    )

summary = {
    "status": reservation["status"],
    "reservation_id": reservation["reservation_id"],
    "sku": reservation["sku"],
    "reserved_quantity": reservation["quantity"],
    "remaining_quantity": reservation["remaining_quantity"],
    "validated_available_quantity": validation["available_quantity"],
}
Path("/tmp/workflow_output.json").write_text(
    json.dumps(summary, indent=2), encoding="utf-8"
)
print(json.dumps(summary, indent=2))
""",
                    "inputs": {
                        "validation": {"$ref": "validate_request.output"},
                        "reservation": {"$ref": "reserve"},
                    },
                },
            ],
        },
    )

    return QuestionRegistry(
        questions=(
            python_question,
            inventory_question,
            hybrid_inventory_question,
        )
    )
