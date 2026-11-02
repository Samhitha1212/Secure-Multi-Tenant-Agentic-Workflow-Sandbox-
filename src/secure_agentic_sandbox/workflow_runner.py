"""Command-line interface for LangGraph workflow execution."""

from __future__ import annotations

import argparse
import asyncio
import json

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
        default=10,
        help="Maximum execution time in seconds (default: 10).",
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
