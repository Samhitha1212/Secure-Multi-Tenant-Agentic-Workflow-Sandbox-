"""Centralized resource configuration for sandbox executions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

DEFAULT_CPU = "500m"
DEFAULT_MEMORY = "512Mi"
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024

MIN_CPU_MILLICORES = 100
MAX_CPU_MILLICORES = 2000

MIN_MEMORY_BYTES = 64 * 1024**2
MAX_MEMORY_BYTES = 2 * 1024**3

MIN_OUTPUT_BYTES = 1024
MAX_OUTPUT_BYTES = 10 * 1024**2

MAX_TIMEOUT_SECONDS = 120


def validate_cpu(cpu: str) -> None:
    """Validate CPU quantity in cores or millicores."""

    match = re.fullmatch(r"(\d+(?:\.\d+)?)(m?)", cpu)

    if match is None:
        raise ValueError("Invalid CPU value. Examples: 250m, 500m, 1.")

    try:
        value = Decimal(match.group(1))
    except InvalidOperation as exc:
        raise ValueError("Invalid CPU value.") from exc

    millicores = value if match.group(2) == "m" else value * 1000

    if (
        not value.is_finite()
        or millicores != millicores.to_integral_value()
        or not MIN_CPU_MILLICORES
        <= millicores
        <= MAX_CPU_MILLICORES
    ):
        raise ValueError("CPU must be between 100m and 2000m.")


def validate_memory(memory: str) -> None:
    """Validate memory quantity."""

    match = re.fullmatch(r"([1-9]\d*)(Ki|Mi|Gi|K|M|G)", memory)

    if match is None:
        raise ValueError(
            "Invalid memory value. Examples: 128Mi, 512Mi, 1Gi."
        )

    amount = int(match.group(1))

    multipliers = {
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
    }

    memory_bytes = amount * multipliers[match.group(2)]

    if not MIN_MEMORY_BYTES <= memory_bytes <= MAX_MEMORY_BYTES:
        raise ValueError("Memory must be between 64Mi and 2Gi.")


def parse_output_size(value: str) -> int:
    """Parse output size such as 1KiB, 1MiB or 1048576B."""

    match = re.fullmatch(
        r"([1-9]\d*)(B|KiB|MiB)",
        value,
    )

    if match is None:
        raise ValueError(
            "Invalid output size. Use values such as 64KiB or 1MiB."
        )

    amount = int(match.group(1))
    multipliers = {
        "B": 1,
        "KiB": 1024,
        "MiB": 1024**2,
    }

    size_bytes = amount * multipliers[match.group(2)]

    if not MIN_OUTPUT_BYTES <= size_bytes <= MAX_OUTPUT_BYTES:
        raise ValueError("Output limit must be between 1KiB and 10MiB.")

    return size_bytes


@dataclass(frozen=True)
class ExecutionConfig:
    """Validated resource limits for one sandbox execution."""

    cpu: str = DEFAULT_CPU
    memory: str = DEFAULT_MEMORY
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES

    def __post_init__(self) -> None:
        validate_cpu(self.cpu)
        validate_memory(self.memory)

        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or not 1 <= self.timeout_seconds <= MAX_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"Timeout must be between 1 and {MAX_TIMEOUT_SECONDS} seconds."
            )

        if (
            isinstance(self.max_output_bytes, bool)
            or not isinstance(self.max_output_bytes, int)
            or not MIN_OUTPUT_BYTES
            <= self.max_output_bytes
            <= MAX_OUTPUT_BYTES
        ):
            raise ValueError(
                "Output limit must be between 1KiB and 10MiB."
            )

    def to_sandbox_resources(self) -> dict[str, str]:
        """Return CPU and memory limits for OpenSandbox."""

        return {
            "cpu": self.cpu,
            "memory": self.memory,
        }