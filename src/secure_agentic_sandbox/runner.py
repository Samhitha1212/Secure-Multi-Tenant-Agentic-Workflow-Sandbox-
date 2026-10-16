"""Initial M1 Python execution service using OpenSandbox."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig
from opensandbox.models import WriteEntry
from opensandbox.models.execd import ExecutionHandlers

PYTHON_IMAGE = "python:3.11"
SANDBOX_FILE = "/tmp/submission.py"

MAX_SOURCE_BYTES = 128 * 1024
DEFAULT_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 120


class SubmissionValidationError(ValueError):
    """Raised when a submitted Python file is invalid."""


@dataclass(frozen=True)
class ExecutionResult:
    """Structured result returned by the execution service."""

    sandbox_id: str
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    duration_seconds: float


def load_python_source(path: str | Path) -> str:
    """Read and validate a single Python source file."""

    source_path = Path(path)

    if source_path.suffix.lower() != ".py":
        raise SubmissionValidationError(
            "Only .py submission files are supported."
        )

    try:
        source_path = source_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise SubmissionValidationError(
            f"Submission file not found: {path}"
        ) from exc

    if not source_path.is_file():
        raise SubmissionValidationError(
            "The submission must be a regular file."
        )

    if source_path.stat().st_size > MAX_SOURCE_BYTES:
        raise SubmissionValidationError(
            f"Source file exceeds the {MAX_SOURCE_BYTES}-byte limit."
        )

    try:
        source = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SubmissionValidationError(
            "The Python source must be valid UTF-8."
        ) from exc

    # Check the encoded size too, not only the initial filesystem size.
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise SubmissionValidationError(
            f"Source file exceeds the {MAX_SOURCE_BYTES}-byte limit."
        )

    return source


def make_connection_config() -> ConnectionConfig:
    """Build the local OpenSandbox client configuration."""

    kwargs: dict[str, object] = {
        "domain": os.getenv(
            "OPEN_SANDBOX_DOMAIN", "127.0.0.1:8080"
        ),
        "protocol": os.getenv("OPEN_SANDBOX_PROTOCOL", "http"),
        "use_server_proxy": True,
        "request_timeout": timedelta(seconds=30),
    }

    api_key = os.getenv("OPEN_SANDBOX_API_KEY")
    if api_key:
        kwargs["api_key"] = api_key

    return ConnectionConfig(**kwargs)


async def run_python_file(
    submission_path: str | Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> ExecutionResult:
    """Run one Python file in a disposable OpenSandbox sandbox.

    This is the initial M1 implementation. It is not yet a complete
    security boundary or a production-ready online code execution API.
    """

    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError(
            f"Timeout must be between 1 and {MAX_TIMEOUT_SECONDS} seconds."
        )

    source = load_python_source(submission_path)
    sandbox: Sandbox | None = None

    try:
        sandbox = await Sandbox.create(
            PYTHON_IMAGE,
            connection_config=make_connection_config(),
            # timeout=timedelta(seconds=timeout_seconds + 30),
            timeout=timedelta(seconds=max(60, timeout_seconds + 30)),
            resource={"cpu": "500m", "memory": "512Mi"},
            metadata={"purpose": "m1-python-execution"},
        )

        await sandbox.files.write_files(
            [
                WriteEntry(
                    path=SANDBOX_FILE,
                    data=source,
                    mode=0o644,
                )
            ]
        )


        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        async def collect_stdout(message) -> None:
            stdout_chunks.append(message.text)

        async def collect_stderr(message) -> None:
            stderr_chunks.append(message.text)

        handlers = ExecutionHandlers(
            on_stdout=collect_stdout,
            on_stderr=collect_stderr,
        )

        started = time.monotonic()

        try:
            execution = await asyncio.wait_for(
                sandbox.commands.run(
                    f"python -u {SANDBOX_FILE}",
                    handlers=handlers,
                ),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            return ExecutionResult(
                sandbox_id=sandbox.id,
                stdout="".join(stdout_chunks),
                stderr=(
                    "".join(stderr_chunks)
                    + f"Execution timed out after "
                    f"{timeout_seconds} seconds."
                ),
                exit_code=None,
                timed_out=True,
                duration_seconds=time.monotonic() - started,
            )

        return ExecutionResult(
            sandbox_id=sandbox.id,
            stdout="".join(stdout_chunks),
            stderr="".join(stderr_chunks),
            exit_code=execution.exit_code,
            timed_out=False,
            duration_seconds=time.monotonic() - started,
        )
    finally:
        # Destroy the remote sandbox even when writing or execution fails.
        if sandbox is not None:
            await sandbox.destroy()


def main() -> None:
    """CLI entry point for the initial execution service."""

    parser = argparse.ArgumentParser(
        description="Execute one Python file through OpenSandbox."
    )
    parser.add_argument("submission", help="Path to a .py file")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Execution deadline in seconds (1-120)",
    )
    args = parser.parse_args()

    try:
        result = asyncio.run(
            run_python_file(args.submission, args.timeout)
        )
    except (OSError, SubmissionValidationError, ValueError) as exc:
        parser.error(str(exc))
        return

    print(json.dumps(asdict(result), indent=2))

    if result.timed_out:
        raise SystemExit(124)

    if result.exit_code not in (None, 0):
        raise SystemExit(result.exit_code)


if __name__ == "__main__":
    main()