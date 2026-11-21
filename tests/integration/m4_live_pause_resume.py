"""Live M4.6 pause/resume test using OpenSandbox and gVisor."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from secure_agentic_sandbox.checkpoint_store import CheckpointStore
from secure_agentic_sandbox.tool_broker import create_mock_tool_broker
from secure_agentic_sandbox.workflow_service import WorkflowExecutionService

TENANT_ID = "m46-test-tenant"
EXECUTION_ID = "m46-live-resume-20261115"


async def docker_running_python_containers() -> set[str]:
    process = await asyncio.create_subprocess_exec(
        "docker",
        "ps",
        "--filter",
        "ancestor=python:3.11",
        "--format",
        "{{.ID}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        raise RuntimeError(
            f"docker ps failed: {stderr.decode(errors='replace')}"
        )

    return {
        line.strip()
        for line in stdout.decode().splitlines()
        if line.strip()
    }


async def wait_for_new_container(
    previous: set[str],
    *,
    timeout_seconds: int = 45,
) -> str:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds

    while loop.time() < deadline:
        current = await docker_running_python_containers()
        new_containers = current - previous

        if new_containers:
            if len(new_containers) > 1:
                print(
                    "Multiple new Python containers detected:",
                    sorted(new_containers),
                )
            return min(new_containers)

        await asyncio.sleep(0.2)

    raise TimeoutError(
        "No new python:3.11 Docker container appeared."
    )


async def inspect_runtime(container_id: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "docker",
        "inspect",
        "--format",
        "{{.HostConfig.Runtime}}",
        container_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        raise RuntimeError(
            f"Could not inspect container {container_id}: "
            f"{stderr.decode(errors='replace')}"
        )

    return stdout.decode().strip()


async def wait_for_container_removal(
    container_id: str,
    *,
    timeout_seconds: int = 20,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds

    while loop.time() < deadline:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            container_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.communicate()

        if process.returncode != 0:
            return

        await asyncio.sleep(0.2)

    raise AssertionError(
        f"Sandbox container was not removed: {container_id}"
    )


async def main() -> None:
    tasks: list[asyncio.Task] = []

    with tempfile.TemporaryDirectory(prefix="m4-live-resume-") as temp_dir:
        database = Path(temp_dir) / "m4-checkpoints.sqlite3"
        store = CheckpointStore(database)

        try:
            store.acquire_runtime_lock()
            store.recover_interrupted_executions()

            broker = create_mock_tool_broker()
            service = WorkflowExecutionService(
                broker,
                checkpoint_store=store,
            )

            specification = {
                "version": 1,
                "steps": [
                    {
                        "id": "first_python",
                        "type": "python",
                        "source": (
                            "import time\n"
                            "print('M46_FIRST_STEP_STARTED', flush=True)\n"
                            "time.sleep(8)\n"
                            "print('M46_FIRST_STEP_FINISHED', flush=True)\n"
                        ),
                        "timeout_seconds": 30,
                    },
                    {
                        "id": "second_python",
                        "type": "python",
                        "source": (
                            "import time\n"
                            "print('M46_SECOND_STEP_STARTED', flush=True)\n"
                            "time.sleep(4)\n"
                            "print('M46_SECOND_STEP_FINISHED', flush=True)\n"
                        ),
                        "timeout_seconds": 30,
                    },
                ],
            }

            baseline = await docker_running_python_containers()

            print("Starting first workflow execution...")
            execute_task = asyncio.create_task(
                service.execute(
                    tenant_id=TENANT_ID,
                    execution_id=EXECUTION_ID,
                    specification=specification,
                    allowed_tools=set(),
                )
            )
            tasks.append(execute_task)

            # Wait until the actual Python sandbox is running.
            first_container = await wait_for_new_container(baseline)
            first_runtime = await inspect_runtime(first_container)

            print(f"First Docker container: {first_container}")
            print(f"First container runtime: {first_runtime}")

            # Give the Python program time to start before requesting pause.
            await asyncio.sleep(1)

            store.request_pause(
                tenant_id=TENANT_ID,
                execution_id=EXECUTION_ID,
            )
            print("Pause requested while first Python step was running.")

            first_result = await asyncio.wait_for(
                execute_task,
                timeout=90,
            )

            assert first_result.status == "paused", (
                f"Expected paused, got {first_result.status}"
            )

            first_step = first_result.step_results["first_python"]
            first_sandbox_id = first_step["sandbox_id"]

            assert first_step["exit_code"] == 0
            assert "M46_FIRST_STEP_STARTED" in first_step["stdout"]
            assert "M46_FIRST_STEP_FINISHED" in first_step["stdout"]
            assert "second_python" not in first_result.step_results

            checkpoint = store.get_checkpoint(
                tenant_id=TENANT_ID,
                execution_id=EXECUTION_ID,
            )
            assert checkpoint is not None
            assert checkpoint.status == "paused"
            assert checkpoint.next_step_index == 1
            assert "second_python" not in checkpoint.step_results

            await wait_for_container_removal(first_container)
            print("First sandbox container removed after pause.")

            # Resume from the saved cursor. The second Python step must
            # execute in a newly created sandbox.
            baseline = await docker_running_python_containers()

            print("Resuming workflow from checkpoint...")
            resume_task = asyncio.create_task(
                service.resume(
                    tenant_id=TENANT_ID,
                    execution_id=EXECUTION_ID,
                    allowed_tools=set(),
                )
            )
            tasks.append(resume_task)

            second_container = await wait_for_new_container(baseline)
            second_runtime = await inspect_runtime(second_container)

            print(f"Second Docker container: {second_container}")
            print(f"Second container runtime: {second_runtime}")

            second_result = await asyncio.wait_for(
                resume_task,
                timeout=90,
            )

            assert second_result.status == "succeeded"
            second_step = second_result.step_results["second_python"]
            second_sandbox_id = second_step["sandbox_id"]

            assert second_step["exit_code"] == 0
            assert "M46_SECOND_STEP_STARTED" in second_step["stdout"]
            assert "M46_SECOND_STEP_FINISHED" in second_step["stdout"]

            # The resumed step must use a different OpenSandbox sandbox.
            assert first_sandbox_id != second_sandbox_id
            assert first_container != second_container

            # Both actual containers must have used gVisor.
            assert first_runtime == "runsc", first_runtime
            assert second_runtime == "runsc", second_runtime

            await wait_for_container_removal(second_container)
            print("Second sandbox container removed after completion.")

            completed = store.get_checkpoint(
                tenant_id=TENANT_ID,
                execution_id=EXECUTION_ID,
            )
            assert completed is not None
            assert completed.status == "completed"
            assert completed.next_step_index == 2

            print(
                json.dumps(
                    {
                        "result": "PASS",
                        "execution_id": EXECUTION_ID,
                        "pause_status": first_result.status,
                        "resume_status": second_result.status,
                        "first_sandbox_id": first_sandbox_id,
                        "second_sandbox_id": second_sandbox_id,
                        "first_docker_container": first_container,
                        "second_docker_container": second_container,
                        "first_runtime": first_runtime,
                        "second_runtime": second_runtime,
                        "first_step_output": first_step["stdout"],
                        "second_step_output": second_step["stdout"],
                        "first_container_removed": True,
                        "second_container_removed": True,
                        "final_checkpoint_status": completed.status,
                        "final_next_step_index": completed.next_step_index,
                    },
                    indent=2,
                )
            )

        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            store.close()


if __name__ == "__main__":
    asyncio.run(main())
