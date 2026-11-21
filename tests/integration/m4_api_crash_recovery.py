"""Test API crash recovery using real subprocesses and OpenSandbox."""

from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "tests/evidence/2026-11-15"

API_KEY = "m4-crash-recovery-test-key"
TENANT_ID = "m4-crash-tenant"
EXECUTION_ID = "m4-api-crash-execution"


def get_free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: int = 10,
) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return response.status, json.loads(body or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return exc.code, json.loads(body or b"{}")


def launch_api(
    database: Path,
    port: int,
    log_path: Path,
) -> tuple[subprocess.Popen, Any]:
    environment = os.environ.copy()
    environment["SECURE_SANDBOX_CHECKPOINT_DB"] = str(database)
    environment["SECURE_SANDBOX_API_KEYS"] = json.dumps(
        {
            API_KEY: {
                "tenant_id": TENANT_ID,
                "allowed_tools": [],
            }
        }
    )
    environment["PYTHONUNBUFFERED"] = "1"

    log_file = log_path.open("w", encoding="utf-8")

    process = subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "secure_agentic_sandbox.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "info",
        ],
        cwd=ROOT,
        env=environment,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    return process, log_file


def wait_for_health(
    process: subprocess.Popen,
    base_url: str,
    *,
    timeout_seconds: int = 40,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "No health response received."

    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"API process exited early with code {process.returncode}."
            )

        try:
            status, body = http_json(
                "GET",
                f"{base_url}/health",
                timeout=2,
            )
            if status == 200 and body.get("status") == "healthy":
                return
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        time.sleep(0.25)

    raise TimeoutError(f"API did not become healthy: {last_error}")


def launch_http_request(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: int = 150,
) -> tuple[threading.Thread, dict[str, Any]]:
    outcome: dict[str, Any] = {}

    def invoke() -> None:
        try:
            outcome["response"] = http_json(
                method,
                url,
                payload=payload,
                timeout=timeout,
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            outcome["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=invoke, daemon=True)
    thread.start()
    return thread, outcome


def read_checkpoint(database: Path) -> dict[str, Any] | None:
    try:
        with sqlite3.connect(database, timeout=2) as connection:
            row = connection.execute(
                """
                SELECT status, next_step_index, step_results_json
                FROM workflow_checkpoints
                WHERE tenant_id = ? AND execution_id = ?
                """,
                (TENANT_ID, EXECUTION_ID),
            ).fetchone()
    except sqlite3.OperationalError:
        return None

    if row is None:
        return None

    return {
        "status": row[0],
        "next_step_index": row[1],
        "step_results": json.loads(row[2]),
    }


def wait_for_checkpoint(
    database: Path,
    *,
    status: str,
    next_step_index: int,
    timeout_seconds: int = 90,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        checkpoint = read_checkpoint(database)

        if (
            checkpoint is not None
            and checkpoint["status"] == status
            and checkpoint["next_step_index"] == next_step_index
        ):
            return checkpoint

        time.sleep(0.2)

    raise TimeoutError(
        f"Checkpoint did not reach status={status}, "
        f"next_step_index={next_step_index}."
    )


def docker_python_containers() -> set[str]:
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            "ancestor=python:3.11",
            "--format",
            "{{.ID}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return {
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip()
    }


def wait_for_new_container(
    previous: set[str],
    *,
    timeout_seconds: int = 60,
) -> str:
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        new_containers = docker_python_containers() - previous
        if new_containers:
            return min(new_containers)
        time.sleep(0.2)

    raise TimeoutError("No new running python:3.11 container appeared.")


def inspect_runtime(container_id: str) -> str:
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.HostConfig.Runtime}}",
            container_id,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def remove_orphan_container(container_id: str) -> None:
    """Remove a sandbox left behind by the deliberately killed API."""

    result = subprocess.run(
        ["docker", "rm", "-f", container_id],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not remove orphan container {container_id}: "
            f"{result.stderr}"
        )


def stop_api(
    process: subprocess.Popen | None,
    log_file: Any,
    *,
    force: bool = False,
) -> None:
    if process is not None and process.poll() is None:
        termination_signal = signal.SIGKILL if force else signal.SIGTERM

        try:
            os.killpg(process.pid, termination_signal)
        except ProcessLookupError:
            pass

        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)

    if log_file is not None and not log_file.closed:
        log_file.close()


def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    port = get_free_port()
    base_url = f"http://127.0.0.1:{port}"

    first_api = None
    second_api = None
    first_log = None
    second_log = None
    orphan_container: str | None = None

    workflow_specification = {
        "version": 1,
        "steps": [
            {
                "id": "first_python",
                "type": "python",
                "source": (
                    "print('M46_CRASH_FIRST_COMPLETED', flush=True)\n"
                ),
                "timeout_seconds": 30,
            },
            {
                "id": "second_python",
                "type": "python",
                "source": (
                    "import time\n"
                    "print('M46_CRASH_SECOND_STARTED', flush=True)\n"
                    "time.sleep(25)\n"
                    "print('M46_CRASH_SECOND_COMPLETED', flush=True)\n"
                ),
                "timeout_seconds": 60,
            },
        ],
    }

    with tempfile.TemporaryDirectory(
        prefix="m4-api-crash-recovery-"
    ) as temporary_directory:
        database = Path(temporary_directory) / "checkpoints.sqlite3"

        try:
            print(f"Starting API on {base_url}", flush=True)

            first_api, first_log = launch_api(
                database,
                port,
                EVIDENCE / "m4-api-crash-before.log",
            )
            wait_for_health(first_api, base_url)
            print("First API process is healthy.", flush=True)

            baseline = docker_python_containers()

            # Submit the workflow asynchronously. The HTTP request remains
            # in flight while the second Python step runs.
            workflow_thread, workflow_outcome = launch_http_request(
                "POST",
                f"{base_url}/workflows/execute",
                payload={
                    "execution_id": EXECUTION_ID,
                    "specification": workflow_specification,
                },
            )

            print("Workflow submitted.", flush=True)

            # First step must be checkpointed before we kill the API.
            checkpoint = wait_for_checkpoint(
                database,
                status="running",
                next_step_index=1,
            )

            assert "first_python" in checkpoint["step_results"]
            assert "M46_CRASH_FIRST_COMPLETED" in (
                checkpoint["step_results"]["first_python"]["stdout"]
            )
            assert "second_python" not in checkpoint["step_results"]

            # The first sandbox has been destroyed before its checkpoint
            # callback. A newly running Python container is the second step.
            orphan_container = wait_for_new_container(baseline)
            orphan_runtime = inspect_runtime(orphan_container)

            print(
                f"Second-step container running: {orphan_container}",
                flush=True,
            )
            print(f"Runtime: {orphan_runtime}", flush=True)
            assert orphan_runtime == "runsc"

            time.sleep(2)

            # Simulate an abrupt process death. SIGKILL prevents FastAPI
            # lifespan shutdown and runner finally-block cleanup.
            print("Sending SIGKILL to the first API process.", flush=True)
            stop_api(first_api, first_log, force=True)
            first_api = None
            first_log = None

            workflow_thread.join(timeout=3)
            print(
                "Original HTTP request outcome:",
                workflow_outcome,
                flush=True,
            )

            # The active sandbox is now orphaned because the runner's
            # finally block could not run. Remove it explicitly.
            remove_orphan_container(orphan_container)
            print(
                f"Removed orphaned container: {orphan_container}",
                flush=True,
            )

            # Restart the API against exactly the same database.
            print("Restarting API against the same SQLite database.", flush=True)

            second_api, second_log = launch_api(
                database,
                port,
                EVIDENCE / "m4-api-crash-after.log",
            )
            wait_for_health(second_api, base_url)
            print("Restarted API is healthy.", flush=True)

            recovered = wait_for_checkpoint(
                database,
                status="paused",
                next_step_index=1,
            )

            assert "first_python" in recovered["step_results"]
            assert "second_python" not in recovered["step_results"]

            print(
                "Startup recovery succeeded:",
                {
                    "status": recovered["status"],
                    "next_step_index": recovered["next_step_index"],
                },
                flush=True,
            )

            # Resume the workflow. It should execute only the unfinished
            # second Python step, in a newly created sandbox.
            resume_baseline = docker_python_containers()

            resume_thread, resume_outcome = launch_http_request(
                "POST",
                f"{base_url}/executions/{EXECUTION_ID}/resume",
                timeout=120,
            )

            resumed_container = wait_for_new_container(resume_baseline)
            resumed_runtime = inspect_runtime(resumed_container)

            print(
                f"Resumed-step container: {resumed_container}",
                flush=True,
            )
            print(f"Resumed runtime: {resumed_runtime}", flush=True)
            assert resumed_runtime == "runsc"
            assert resumed_container != orphan_container

            resume_thread.join(timeout=100)
            if resume_thread.is_alive():
                raise TimeoutError("Resume request did not finish.")

            assert "response" in resume_outcome, resume_outcome
            response_status, response_body = resume_outcome["response"]

            assert response_status == 200, response_body
            assert response_body["status"] == "succeeded"

            results = response_body["step_results"]
            assert "M46_CRASH_FIRST_COMPLETED" in (
                results["first_python"]["stdout"]
            )
            assert "M46_CRASH_SECOND_STARTED" in (
                results["second_python"]["stdout"]
            )
            assert "M46_CRASH_SECOND_COMPLETED" in (
                results["second_python"]["stdout"]
            )

            final_checkpoint = read_checkpoint(database)
            assert final_checkpoint is not None
            assert final_checkpoint["status"] == "completed"
            assert final_checkpoint["next_step_index"] == 2

            print(
                json.dumps(
                    {
                        "result": "PASS",
                        "execution_id": EXECUTION_ID,
                        "first_api_pid": first_api.pid if first_api else None,
                        "interrupted_container": orphan_container,
                        "interrupted_runtime": orphan_runtime,
                        "orphan_cleanup": "manual docker rm -f confirmed",
                        "restarted_api_pid": second_api.pid,
                        "recovered_checkpoint_status": recovered["status"],
                        "recovered_cursor": recovered["next_step_index"],
                        "resume_http_status": response_status,
                        "resume_workflow_status": response_body["status"],
                        "resumed_container": resumed_container,
                        "resumed_runtime": resumed_runtime,
                        "first_step_not_replayed": True,
                        "second_step_completed": True,
                        "final_checkpoint_status": final_checkpoint["status"],
                        "final_cursor": final_checkpoint["next_step_index"],
                    },
                    indent=2,
                ),
                flush=True,
            )

        finally:
            if second_api is not None:
                stop_api(second_api, second_log)

            if first_api is not None:
                stop_api(first_api, first_log, force=True)

            # Safety cleanup if an earlier assertion failed.
            if orphan_container is not None:
                subprocess.run(
                    ["docker", "rm", "-f", orphan_container],
                    capture_output=True,
                    text=True,
                    check=False,
                )


if __name__ == "__main__":
    main()
