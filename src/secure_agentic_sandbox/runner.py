"""Python execution service using OpenSandbox."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path

from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig
from opensandbox.exceptions import SandboxException
from opensandbox.models import WriteEntry
from opensandbox.models.execd import ExecutionHandlers

from secure_agentic_sandbox.execution_config import (
    DEFAULT_CPU,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_MEMORY,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    ExecutionConfig,
    parse_output_size,
)

PYTHON_IMAGE = "python:3.11"
SANDBOX_FILE = "/tmp/submission.py"
SANDBOX_INPUT_FILE = "/tmp/workflow_input.json"

MAX_SOURCE_BYTES = 128 * 1024
MAX_WORKFLOW_INPUT_BYTES = 64 * 1024
MAX_WORKFLOW_OUTPUT_BYTES = 64 * 1024
SANDBOX_OUTPUT_FILE = "/tmp/workflow_output.json"


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
    output_truncated: bool = False
    oom_killed: bool = False
    output: dict[str, object] | None = None


@dataclass
class BoundedOutputCollector:
    """Collect stdout and stderr within one shared byte limit."""

    max_bytes: int
    stdout_chunks: list[str] = field(default_factory=list)
    stderr_chunks: list[str] = field(default_factory=list)
    bytes_used: int = 0
    truncated: bool = False

    def _append(self, chunks: list[str], text: str) -> None:
        """Append text without exceeding the combined output limit."""

        if not text or self.truncated:
            return

        encoded = text.encode("utf-8")
        remaining = self.max_bytes - self.bytes_used

        if remaining <= 0:
            self.truncated = True
            return

        if len(encoded) <= remaining:
            chunks.append(text)
            self.bytes_used += len(encoded)
            return

        # Preserve valid UTF-8 if the limit cuts through a character.
        retained = encoded[:remaining].decode(
            "utf-8",
            errors="ignore",
        )

        if retained:
            chunks.append(retained)
            self.bytes_used += len(retained.encode("utf-8"))

        self.truncated = True

    def append_stdout(self, text: str) -> None:
        """Collect stdout within the shared output budget."""

        self._append(self.stdout_chunks, text)

    def append_stderr(self, text: str) -> None:
        """Collect stderr within the shared output budget."""

        self._append(self.stderr_chunks, text)

    @property
    def stdout(self) -> str:
        return "".join(self.stdout_chunks)

    @property
    def stderr(self) -> str:
        return "".join(self.stderr_chunks)


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

    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise SubmissionValidationError(
            f"Source file exceeds the {MAX_SOURCE_BYTES}-byte limit."
        )

    return source


def validate_python_source(source: str) -> str:
    """Validate inline Python source before creating a sandbox."""

    if not isinstance(source, str):
        raise SubmissionValidationError(
            "Python source must be a string."
        )

    try:
        encoded_source = source.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SubmissionValidationError(
            "The Python source must be valid UTF-8."
        ) from exc

    if len(encoded_source) > MAX_SOURCE_BYTES:
        raise SubmissionValidationError(
            f"Source file exceeds the {MAX_SOURCE_BYTES}-byte limit."
        )

    return source



def _validate_json_value(value: object) -> None:
    """Reject values that cannot be represented as strict JSON."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings.")
            _validate_json_value(item)
        return

    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return

    if value is None or isinstance(value, (str, bool, int, float)):
        return

    raise TypeError("Value is not JSON-compatible.")


def serialize_workflow_input(
    input_data: Mapping[str, object] | None,
) -> str | None:
    """Validate and serialize selected previous-step results."""

    if input_data is None:
        return None

    if not isinstance(input_data, Mapping):
        raise SubmissionValidationError(
            "Workflow input must be a JSON object."
        )

    try:
        _validate_json_value(input_data)
        serialized = json.dumps(
            dict(input_data),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise SubmissionValidationError(
            "Workflow input must contain JSON-compatible values."
        ) from exc

    if len(serialized.encode("utf-8")) > MAX_WORKFLOW_INPUT_BYTES:
        raise SubmissionValidationError(
            f"Workflow input exceeds the {MAX_WORKFLOW_INPUT_BYTES}-byte limit."
        )

    return serialized



def make_connection_config() -> ConnectionConfig:
    """Build the local OpenSandbox client configuration."""

    kwargs: dict[str, object] = {
        "domain": os.getenv(
            "OPEN_SANDBOX_DOMAIN",
            "127.0.0.1:8080",
        ),
        "protocol": os.getenv("OPEN_SANDBOX_PROTOCOL", "http"),
        "use_server_proxy": True,
        "request_timeout": timedelta(seconds=30),
    }

    api_key = os.getenv("OPEN_SANDBOX_API_KEY")

    if api_key:
        kwargs["api_key"] = api_key

    return ConnectionConfig(**kwargs)


async def _sandbox_was_oom_killed(sandbox: Sandbox) -> bool:
    """Return True only when OpenSandbox explicitly reports a Docker OOM kill.

    Diagnostics are best-effort. If the diagnostics API is unavailable or
    returns an unexpected response, the caller retains the original timeout
    or non-zero-exit classification.
    """

    try:
        events = await sandbox.get_diagnostic_events("runtime")
    except (SandboxException, AttributeError):
        # AttributeError also covers older SDKs and lightweight test doubles
        # that do not expose the diagnostics method.
        return False

    content = events.content or ""
    return (
        "OOMKilled - container was killed due to out-of-memory"
        in content
    )



async def _read_workflow_output(
    sandbox: Sandbox,
) -> dict[str, object]:
    """Read and validate bounded JSON output from the sandbox."""

    stream = await sandbox.files.read_bytes_stream(
        SANDBOX_OUTPUT_FILE,
        chunk_size=16 * 1024,
    )

    chunks: list[bytes] = []
    total_bytes = 0

    try:
        async for chunk in stream:
            if not isinstance(chunk, bytes):
                raise SubmissionValidationError(
                    "Sandbox workflow output must be binary data."
                )

            total_bytes += len(chunk)

            if total_bytes > MAX_WORKFLOW_OUTPUT_BYTES:
                raise SubmissionValidationError(
                    "Workflow output exceeds the "
                    f"{MAX_WORKFLOW_OUTPUT_BYTES}-byte limit."
                )

            chunks.append(chunk)
    finally:
        await stream.aclose()

    def reject_nonstandard_constant(value: str) -> None:
        raise ValueError(f"Invalid JSON constant: {value}")

    try:
        decoded = b"".join(chunks).decode("utf-8")
        payload = json.loads(
            decoded,
            parse_constant=reject_nonstandard_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise SubmissionValidationError(
            "Sandbox workflow output must be valid UTF-8 JSON."
        ) from exc

    if not isinstance(payload, dict):
        raise SubmissionValidationError(
            "Sandbox workflow output must be a JSON object."
        )

    return payload


async def _execute_python_source(
    source: str,
    execution_config: ExecutionConfig,
    input_data_json: str | None = None,
) -> ExecutionResult:
    """Execute validated Python source in a disposable sandbox."""

    sandbox: Sandbox | None = None
    primary_error: BaseException | None = None

    try:
        sandbox = await Sandbox.create(
            PYTHON_IMAGE,
            connection_config=make_connection_config(),
            timeout=timedelta(
                seconds=max(
                    60,
                    execution_config.timeout_seconds + 30,
                )
            ),
            resource=execution_config.to_sandbox_resources(),
            metadata={"purpose": "python-execution"},
        )

        write_entries = [
            WriteEntry(
                path=SANDBOX_FILE,
                data=source,
                mode=0o644,
            )
        ]

        if input_data_json is not None:
            write_entries.append(
                WriteEntry(
                    path=SANDBOX_INPUT_FILE,
                    data=input_data_json,
                    mode=0o600,
                )
            )

        # Structured output is enabled only for workflow executions.
        if input_data_json is not None:
            write_entries.append(
                WriteEntry(
                    path=SANDBOX_OUTPUT_FILE,
                    data="{}",
                    mode=0o600,
                )
            )

        await sandbox.files.write_files(write_entries)

        # stdout and stderr share one output budget.
        output = BoundedOutputCollector(
            max_bytes=execution_config.max_output_bytes,
        )

        async def collect_stdout(message) -> None:
            output.append_stdout(message.text)

        async def collect_stderr(message) -> None:
            output.append_stderr(message.text)

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
                timeout=execution_config.timeout_seconds,
            )

        except TimeoutError:
            # Inspect diagnostics before the finally block destroys the sandbox.
            oom_killed = await _sandbox_was_oom_killed(sandbox)

            if oom_killed:
                return ExecutionResult(
                    sandbox_id=sandbox.id,
                    stdout=output.stdout,
                    stderr=(
                        output.stderr
                        + "Sandbox was terminated by the OOM killer "
                        "(memory limit exceeded)."
                    ),
                    exit_code=137,
                    timed_out=False,
                    duration_seconds=time.monotonic() - started,
                    output_truncated=output.truncated,
                    oom_killed=True,
                )

            return ExecutionResult(
                sandbox_id=sandbox.id,
                stdout=output.stdout,
                stderr=(
                    output.stderr
                    + (
                        "Execution timed out after "
                        f"{execution_config.timeout_seconds} seconds."
                    )
                ),
                exit_code=None,
                timed_out=True,
                duration_seconds=time.monotonic() - started,
                output_truncated=output.truncated,
                oom_killed=False,
            )

        # A command can also finish with a non-zero exit code before the
        # client-side timeout. Check diagnostics for explicit OOM evidence.
        oom_killed = (
            execution.exit_code != 0
            and await _sandbox_was_oom_killed(sandbox)
        )

        if oom_killed:
            return ExecutionResult(
                sandbox_id=sandbox.id,
                stdout=output.stdout,
                stderr=(
                    output.stderr
                    + "Sandbox was terminated by the OOM killer "
                    "(memory limit exceeded)."
                ),
                exit_code=137,
                timed_out=False,
                duration_seconds=time.monotonic() - started,
                output_truncated=output.truncated,
                oom_killed=True,
            )

        structured_output = None

        if input_data_json is not None and execution.exit_code == 0:
            structured_output = await _read_workflow_output(sandbox)

        return ExecutionResult(
            sandbox_id=sandbox.id,
            stdout=output.stdout,
            stderr=output.stderr,
            exit_code=execution.exit_code,
            timed_out=False,
            duration_seconds=time.monotonic() - started,
            output_truncated=output.truncated,
            oom_killed=False,
            output=structured_output,
        )

    except BaseException as exc:
        # Preserve the original execution/upload/cancellation error.
        primary_error = exc
        raise

    finally:
        # Always attempt to destroy the remote sandbox.
        if sandbox is not None:
            try:
                await sandbox.destroy()

            except Exception as cleanup_error:
                if primary_error is None:
                    # Execution succeeded, but cleanup failed.
                    # Do not silently report success.
                    raise

                # Preserve the original exception and attach
                # the cleanup failure as additional diagnostic info.
                primary_error.add_note(
                    "Sandbox cleanup also failed: "
                    f"{cleanup_error!r}"
                )



async def run_python_file(
    submission_path: str | Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    *,
    cpu: str = DEFAULT_CPU,
    memory: str = DEFAULT_MEMORY,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> ExecutionResult:
    """Run one Python file in a disposable OpenSandbox sandbox."""

    execution_config = ExecutionConfig(
        cpu=cpu,
        memory=memory,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )

    source = load_python_source(submission_path)

    return await _execute_python_source(source, execution_config)


async def run_python_source(
    source: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    *,
    cpu: str = DEFAULT_CPU,
    memory: str = DEFAULT_MEMORY,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    input_data: Mapping[str, object] | None = None,
) -> ExecutionResult:
    """Run inline Python source in a disposable OpenSandbox sandbox."""

    execution_config = ExecutionConfig(
        cpu=cpu,
        memory=memory,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )

    validated_source = validate_python_source(source)
    serialized_input = serialize_workflow_input(input_data)

    if serialized_input is None:
        return await _execute_python_source(
            validated_source,
            execution_config,
        )

    return await _execute_python_source(
        validated_source,
        execution_config,
        serialized_input,
    )


def main() -> None:
    """CLI entry point for direct Python execution."""

    parser = argparse.ArgumentParser(
        description="Execute one Python file through OpenSandbox."
    )

    parser.add_argument(
        "submission",
        help="Path to a .py file",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Execution deadline in seconds (1-{MAX_TIMEOUT_SECONDS})",
    )

    parser.add_argument(
        "--cpu",
        default=DEFAULT_CPU,
        help="CPU limit, e.g. 250m, 500m or 1",
    )

    parser.add_argument(
        "--memory",
        default=DEFAULT_MEMORY,
        help="Memory limit, e.g. 128Mi, 512Mi or 1Gi",
    )

    parser.add_argument(
        "--max-output",
        type=parse_output_size,
        default=DEFAULT_MAX_OUTPUT_BYTES,
        help="Maximum combined stdout/stderr output, e.g. 64KiB or 1MiB",
    )

    args = parser.parse_args()

    try:
        result = asyncio.run(
            run_python_file(
                args.submission,
                timeout_seconds=args.timeout,
                cpu=args.cpu,
                memory=args.memory,
                max_output_bytes=args.max_output,
            )
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