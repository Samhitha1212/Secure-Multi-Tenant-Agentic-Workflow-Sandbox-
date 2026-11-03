"""Command-line interface for LangGraph workflow execution."""

from __future__ import annotations

import argparse
import asyncio
import json

from secure_agentic_sandbox.execution_config import (
    DEFAULT_CPU,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_MEMORY,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    parse_output_size,
)
from secure_agentic_sandbox.workflow import run_workflow


def main() -> None:
    """Execute a Python submission through the LangGraph workflow."""

    parser = argparse.ArgumentParser(
        description="Execute a Python file through the LangGraph sandbox workflow."
    )

    parser.add_argument(
        "submission",
        help="Path to the Python file to execute.",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Execution timeout in seconds (1-{MAX_TIMEOUT_SECONDS}).",
    )

    parser.add_argument(
        "--cpu",
        default=DEFAULT_CPU,
        help="CPU limit, e.g. 250m, 500m or 1.",
    )

    parser.add_argument(
        "--memory",
        default=DEFAULT_MEMORY,
        help="Memory limit, e.g. 128Mi, 512Mi or 1Gi.",
    )

    parser.add_argument(
        "--max-output",
        type=parse_output_size,
        default=DEFAULT_MAX_OUTPUT_BYTES,
        help="Combined stdout/stderr limit, e.g. 64KiB or 1MiB.",
    )

    parser.add_argument(
        "--execution-id",
        default=None,
        help="Optional platform-provided execution ID.",
    )

    args = parser.parse_args()

    result = asyncio.run(
        run_workflow(
            submission_path=args.submission,
            timeout_seconds=args.timeout,
            execution_id=args.execution_id,
            cpu=args.cpu,
            memory=args.memory,
            max_output_bytes=args.max_output,
        )
    )

    print(json.dumps(result, indent=2))

    status = result["status"]

    if status == "succeeded":
        return

    if status == "timed_out":
        raise SystemExit(124)

    if status == "validation_failed":
        raise SystemExit(2)

    if status == "failed":
        execution_result = result["execution_result"]

        if execution_result is not None:
            exit_code = execution_result["exit_code"]

            if isinstance(exit_code, int) and exit_code != 0:
                raise SystemExit(exit_code)

        raise SystemExit(1)


if __name__ == "__main__":
    main()