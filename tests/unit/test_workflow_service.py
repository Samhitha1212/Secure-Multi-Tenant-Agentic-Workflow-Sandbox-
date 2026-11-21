"""Tests for the platform workflow execution service."""

import pytest

from secure_agentic_sandbox import runner
from secure_agentic_sandbox.checkpoint_store import CheckpointStore
from secure_agentic_sandbox.tool_broker import create_mock_tool_broker
from secure_agentic_sandbox.workflow_service import (
    WorkflowAuthorizationError,
    WorkflowExecutionService,
)


def create_specification():
    return {
        "version": 1,
        "steps": [
            {
                "id": "lookup",
                "type": "tool",
                "tool": "inventory.lookup",
                "arguments": {"sku": "SKU-100"},
            }
        ],
    }


@pytest.mark.asyncio
async def test_service_executes_authorized_workflow():
    broker = create_mock_tool_broker()
    service = WorkflowExecutionService(broker)

    result = await service.execute(
        tenant_id="tenant-a",
        execution_id="execution-a",
        specification=create_specification(),
        allowed_tools={"inventory.lookup"},
    )

    assert result.execution_id == "execution-a"
    assert result.status == "succeeded"
    assert result.tools_used == ("inventory.lookup",)
    assert result.step_results["lookup"]["found"] is True
    assert result.grant_id is not None

    assert any(
        record.event == "grant.revoke"
        and record.outcome == "succeeded"
        for record in broker.audit_log
    )


@pytest.mark.asyncio
async def test_service_rejects_tool_outside_platform_policy():
    broker = create_mock_tool_broker()
    service = WorkflowExecutionService(broker)

    with pytest.raises(WorkflowAuthorizationError):
        await service.execute(
            tenant_id="tenant-a",
            execution_id="execution-a",
            specification=create_specification(),
            allowed_tools=set(),
        )

    # Authorization rejection must happen before grant creation.
    assert not any(
        record.event == "grant.create"
        for record in broker.audit_log
    )


@pytest.mark.asyncio
async def test_service_supports_python_only_workflow(monkeypatch):
    broker = create_mock_tool_broker()
    service = WorkflowExecutionService(broker)

    async def fake_run_python_source(source, **options):
        return runner.ExecutionResult(
            sandbox_id="mock-sandbox",
            stdout="Python completed\n",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.1,
            output={"answer": 42},
        )

    monkeypatch.setattr(
        runner,
        "run_python_source",
        fake_run_python_source,
    )

    result = await service.execute(
        tenant_id="tenant-a",
        execution_id="execution-python",
        specification={
            "version": 1,
            "steps": [
                {
                    "id": "calculate",
                    "type": "python",
                    "source": "print('calculate')",
                }
            ],
        },
        allowed_tools=set(),
    )

    assert result.status == "succeeded"
    assert result.tools_used == ()
    assert result.grant_id is None
    assert result.step_results["calculate"]["output"] == {"answer": 42}


@pytest.mark.asyncio
async def test_service_rejects_invalid_execution_identity():
    broker = create_mock_tool_broker()
    service = WorkflowExecutionService(broker)

    with pytest.raises(ValueError):
        await service.execute(
            tenant_id="",
            execution_id="execution-a",
            specification=create_specification(),
            allowed_tools={"inventory.lookup"},
        )


@pytest.mark.asyncio
async def test_service_stops_after_python_timeout(monkeypatch):
    broker = create_mock_tool_broker()
    service = WorkflowExecutionService(broker)

    async def fake_run_python_source(source, **options):
        return runner.ExecutionResult(
            sandbox_id="mock-sandbox",
            stdout="",
            stderr="Execution timed out",
            exit_code=None,
            timed_out=True,
            duration_seconds=10,
        )

    monkeypatch.setattr(
        runner,
        "run_python_source",
        fake_run_python_source,
    )

    result = await service.execute(
        tenant_id="tenant-a",
        execution_id="execution-timeout",
        specification={
            "version": 1,
            "steps": [
                {
                    "id": "run",
                    "type": "python",
                    "source": "while True: pass",
                }
            ],
        },
        allowed_tools=set(),
    )

    assert result.status == "timed_out"


@pytest.mark.asyncio
async def test_service_persists_successful_python_checkpoint(
    tmp_path,
    monkeypatch,
):
    broker = create_mock_tool_broker()

    async def fake_run_python_source(source, **options):
        return runner.ExecutionResult(
            sandbox_id="temporary-sandbox",
            stdout="Python completed\n",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.1,
            output={"answer": 42},
        )

    monkeypatch.setattr(
        runner,
        "run_python_source",
        fake_run_python_source,
    )

    with CheckpointStore(tmp_path / "checkpoints.sqlite3") as store:
        service = WorkflowExecutionService(
            broker,
            checkpoint_store=store,
        )

        result = await service.execute(
            tenant_id="tenant-checkpoint",
            execution_id="execution-python-checkpoint",
            specification={
                "version": 1,
                "steps": [
                    {
                        "id": "calculate",
                        "type": "python",
                        "source": "print('calculate')",
                    }
                ],
            },
            allowed_tools=set(),
        )

        checkpoint = store.get_checkpoint(
            tenant_id="tenant-checkpoint",
            execution_id="execution-python-checkpoint",
        )

        assert result.status == "succeeded"
        assert checkpoint is not None
        assert checkpoint.next_step_index == 1
        assert checkpoint.status == "completed"
        assert checkpoint.step_results["calculate"]["output"] == {
            "answer": 42
        }
        assert "sandbox_id" not in checkpoint.step_results["calculate"]


@pytest.mark.asyncio
async def test_service_persists_progress_after_each_tool_step(
    tmp_path,
    monkeypatch,
):
    broker = create_mock_tool_broker()
    updates = []

    with CheckpointStore(tmp_path / "checkpoints.sqlite3") as store:
        original_update = store.update_progress

        def recording_update(**kwargs):
            updates.append(
                (kwargs["next_step_index"], kwargs["status"])
            )
            return original_update(**kwargs)

        monkeypatch.setattr(store, "update_progress", recording_update)

        service = WorkflowExecutionService(
            broker,
            checkpoint_store=store,
        )

        result = await service.execute(
            tenant_id="tenant-checkpoint",
            execution_id="execution-two-tools",
            specification={
                "version": 1,
                "steps": [
                    {
                        "id": "lookup_first",
                        "type": "tool",
                        "tool": "inventory.lookup",
                        "arguments": {"sku": "SKU-100"},
                    },
                    {
                        "id": "lookup_second",
                        "type": "tool",
                        "tool": "inventory.lookup",
                        "arguments": {"sku": "SKU-100"},
                    },
                ],
            },
            allowed_tools={"inventory.lookup"},
        )

        checkpoint = store.get_checkpoint(
            tenant_id="tenant-checkpoint",
            execution_id="execution-two-tools",
        )

        assert result.status == "succeeded"
        assert checkpoint is not None
        assert checkpoint.next_step_index == 2
        assert checkpoint.status == "completed"
        assert set(checkpoint.step_results) == {
            "lookup_first",
            "lookup_second",
        }
        assert updates == [
            (1, "running"),
            (2, "running"),
            (2, "completed"),
        ]


@pytest.mark.asyncio
async def test_failed_python_step_does_not_advance_checkpoint(
    tmp_path,
    monkeypatch,
):
    broker = create_mock_tool_broker()

    async def fake_run_python_source(source, **options):
        return runner.ExecutionResult(
            sandbox_id="temporary-sandbox",
            stdout="",
            stderr="Execution failed",
            exit_code=1,
            timed_out=False,
            duration_seconds=0.1,
        )

    monkeypatch.setattr(
        runner,
        "run_python_source",
        fake_run_python_source,
    )

    with CheckpointStore(tmp_path / "checkpoints.sqlite3") as store:
        service = WorkflowExecutionService(
            broker,
            checkpoint_store=store,
        )

        result = await service.execute(
            tenant_id="tenant-checkpoint",
            execution_id="execution-failed-python",
            specification={
                "version": 1,
                "steps": [
                    {
                        "id": "run",
                        "type": "python",
                        "source": "raise RuntimeError('failure')",
                    }
                ],
            },
            allowed_tools=set(),
        )

        checkpoint = store.get_checkpoint(
            tenant_id="tenant-checkpoint",
            execution_id="execution-failed-python",
        )

        assert result.status == "failed"
        assert "run" in result.step_results
        assert checkpoint is not None
        assert checkpoint.next_step_index == 0
        assert checkpoint.status == "failed"
        assert checkpoint.step_results == {}


@pytest.mark.asyncio
async def test_service_preserves_checkpoint_when_later_step_raises(
    tmp_path,
    monkeypatch,
):
    broker = create_mock_tool_broker()

    async def failing_run_python_source(source, **options):
        raise RuntimeError("Sandbox execution unavailable")

    monkeypatch.setattr(
        runner,
        "run_python_source",
        failing_run_python_source,
    )

    with CheckpointStore(tmp_path / "checkpoints.sqlite3") as store:
        service = WorkflowExecutionService(
            broker,
            checkpoint_store=store,
        )

        with pytest.raises(
            RuntimeError,
            match="Sandbox execution unavailable",
        ):
            await service.execute(
                tenant_id="tenant-checkpoint",
                execution_id="execution-interrupted",
                specification={
                    "version": 1,
                    "steps": [
                        {
                            "id": "lookup",
                            "type": "tool",
                            "tool": "inventory.lookup",
                            "arguments": {"sku": "SKU-100"},
                        },
                        {
                            "id": "process",
                            "type": "python",
                            "source": "print('process')",
                        },
                    ],
                },
                allowed_tools={"inventory.lookup"},
            )

        checkpoint = store.get_checkpoint(
            tenant_id="tenant-checkpoint",
            execution_id="execution-interrupted",
        )

        assert checkpoint is not None
        assert checkpoint.status == "failed"
        assert checkpoint.next_step_index == 1
        assert set(checkpoint.step_results) == {"lookup"}
        assert checkpoint.step_results["lookup"]["found"] is True


@pytest.mark.asyncio
async def test_pause_during_python_step_stops_before_next_step(
    tmp_path,
    monkeypatch,
):
    broker = create_mock_tool_broker()
    events = []

    with CheckpointStore(tmp_path / "checkpoints.sqlite3") as store:
        async def fake_run_python_source(source, **options):
            events.append("python_started")

            # Simulate an authenticated pause request while Python is running.
            store.request_pause(
                tenant_id="tenant-checkpoint",
                execution_id="execution-pause",
            )

            events.append("python_finished")

            return runner.ExecutionResult(
                sandbox_id="temporary-sandbox",
                stdout="Completed\n",
                stderr="",
                exit_code=0,
                timed_out=False,
                duration_seconds=0.1,
                output={"answer": 42},
            )

        monkeypatch.setattr(
            runner,
            "run_python_source",
            fake_run_python_source,
        )

        service = WorkflowExecutionService(
            broker,
            checkpoint_store=store,
        )

        result = await service.execute(
            tenant_id="tenant-checkpoint",
            execution_id="execution-pause",
            specification={
                "version": 1,
                "steps": [
                    {
                        "id": "calculate",
                        "type": "python",
                        "source": "print('calculate')",
                    },
                    {
                        "id": "lookup",
                        "type": "tool",
                        "tool": "inventory.lookup",
                        "arguments": {"sku": "SKU-100"},
                    },
                ],
            },
            allowed_tools={"inventory.lookup"},
        )

        checkpoint = store.get_checkpoint(
            tenant_id="tenant-checkpoint",
            execution_id="execution-pause",
        )

        assert result.status == "paused"
        assert events == ["python_started", "python_finished"]
        assert checkpoint is not None
        assert checkpoint.status == "paused"
        assert checkpoint.next_step_index == 1
        assert set(checkpoint.step_results) == {"calculate"}
        assert checkpoint.step_results["calculate"]["output"] == {
            "answer": 42
        }
        assert "sandbox_id" not in checkpoint.step_results["calculate"]


@pytest.mark.asyncio
async def test_resume_continues_without_replaying_completed_steps(
    tmp_path,
    monkeypatch,
):
    broker = create_mock_tool_broker()

    specification = {
        "version": 1,
        "steps": [
            {
                "id": "lookup",
                "type": "tool",
                "tool": "inventory.lookup",
                "arguments": {"sku": "SKU-100"},
            },
            {
                "id": "process",
                "type": "python",
                "source": "print('resumed')",
                "inputs": {
                    "quantity": {"$ref": "lookup.quantity"},
                },
            },
        ],
    }

    saved_lookup = {
        "sku": "SKU-100",
        "found": True,
        "quantity": 12,
    }

    captured = {}
    tool_calls = []
    original_invoke = broker.invoke

    def recording_invoke(**kwargs):
        tool_calls.append(kwargs)
        return original_invoke(**kwargs)

    monkeypatch.setattr(broker, "invoke", recording_invoke)

    async def fake_run_python_source(source, **options):
        captured["source"] = source
        captured["input_data"] = options["input_data"]

        return runner.ExecutionResult(
            sandbox_id="fresh-resume-sandbox",
            stdout="Resumed successfully\n",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.1,
            output={"processed_quantity": 12},
        )

    monkeypatch.setattr(
        runner,
        "run_python_source",
        fake_run_python_source,
    )

    with CheckpointStore(tmp_path / "resume-checkpoints.sqlite3") as store:
        store.create_checkpoint(
            tenant_id="tenant-resume",
            execution_id="execution-resume",
            workflow_version=1,
            specification=specification,
            allowed_tools={"inventory.lookup"},
            next_step_index=1,
            step_results={"lookup": saved_lookup},
            status="paused",
        )

        service = WorkflowExecutionService(
            broker,
            checkpoint_store=store,
        )

        result = await service.resume(
            tenant_id="tenant-resume",
            execution_id="execution-resume",
            allowed_tools={"inventory.lookup"},
        )

        checkpoint = store.get_checkpoint(
            tenant_id="tenant-resume",
            execution_id="execution-resume",
        )

        assert result.status == "succeeded"
        assert result.execution_id == "execution-resume"
        assert result.grant_id is not None

        # The completed tool step must not be invoked again.
        assert tool_calls == []

        # The remaining Python step receives data from the saved result.
        assert captured["source"] == "print('resumed')"
        assert captured["input_data"] == {"quantity": 12}

        assert checkpoint is not None
        assert checkpoint.status == "completed"
        assert checkpoint.next_step_index == 2
        assert checkpoint.step_results["lookup"] == saved_lookup
        assert checkpoint.step_results["process"]["output"] == {
            "processed_quantity": 12
        }
        assert (
            checkpoint.step_results["process"].get("sandbox_id") is None
        )


@pytest.mark.asyncio
async def test_workflow_can_pause_and_resume_from_saved_checkpoint(
    tmp_path,
    monkeypatch,
):
    broker = create_mock_tool_broker()
    executed = []
    sandbox_ids = []

    specification = {
        "version": 1,
        "steps": [
            {
                "id": "first",
                "type": "python",
                "source": "print('first')",
            },
            {
                "id": "second",
                "type": "python",
                "source": "print('second')",
                "inputs": {
                    "previous_value": {"$ref": "first.output.value"},
                },
            },
        ],
    }

    with CheckpointStore(tmp_path / "pause-resume.sqlite3") as store:

        async def fake_run_python_source(source, **options):
            executed.append(source)
            sandbox_id = f"fresh-sandbox-{len(executed)}"
            sandbox_ids.append(sandbox_id)

            if source == "print('first')":
                # Simulate a pause request while the first step is running.
                store.request_pause(
                    tenant_id="tenant-lifecycle",
                    execution_id="execution-pause-resume",
                )
                output = {"value": 7}
            else:
                assert options["input_data"] == {"previous_value": 7}
                output = {"consumed": 7}

            return runner.ExecutionResult(
                sandbox_id=sandbox_id,
                stdout="completed\n",
                stderr="",
                exit_code=0,
                timed_out=False,
                duration_seconds=0.1,
                output=output,
            )

        monkeypatch.setattr(
            runner,
            "run_python_source",
            fake_run_python_source,
        )

        service = WorkflowExecutionService(
            broker,
            checkpoint_store=store,
        )

        first_result = await service.execute(
            tenant_id="tenant-lifecycle",
            execution_id="execution-pause-resume",
            specification=specification,
            allowed_tools=set(),
        )

        paused_checkpoint = store.get_checkpoint(
            tenant_id="tenant-lifecycle",
            execution_id="execution-pause-resume",
        )

        assert first_result.status == "paused"
        assert paused_checkpoint is not None
        assert paused_checkpoint.status == "paused"
        assert paused_checkpoint.next_step_index == 1
        assert set(paused_checkpoint.step_results) == {"first"}
        assert (
            paused_checkpoint.step_results["first"]["output"]["value"] == 7
        )
        assert "sandbox_id" not in paused_checkpoint.step_results["first"]

        resumed_result = await service.resume(
            tenant_id="tenant-lifecycle",
            execution_id="execution-pause-resume",
            allowed_tools=set(),
        )

        completed_checkpoint = store.get_checkpoint(
            tenant_id="tenant-lifecycle",
            execution_id="execution-pause-resume",
        )

        assert resumed_result.status == "succeeded"
        assert resumed_result.execution_id == "execution-pause-resume"

        # The first step ran once; resume executed only the second step.
        assert executed == ["print('first')", "print('second')"]

        # Each Python step used a separate sandbox execution.
        assert len(set(sandbox_ids)) == 2

        assert completed_checkpoint is not None
        assert completed_checkpoint.status == "completed"
        assert completed_checkpoint.next_step_index == 2
        assert set(completed_checkpoint.step_results) == {
            "first",
            "second",
        }
        assert (
            completed_checkpoint.step_results["second"]["output"]["consumed"]
            == 7
        )
        assert all(
            "sandbox_id" not in result
            for result in completed_checkpoint.step_results.values()
        )

@pytest.mark.asyncio
async def test_resume_rejects_checkpoint_without_original_permissions(
    tmp_path,
):
    broker = create_mock_tool_broker()

    specification = {
        "version": 1,
        "steps": [
            {
                "id": "process",
                "type": "python",
                "source": "print('resume')",
            },
        ],
    }

    with CheckpointStore(tmp_path / "legacy-resume.sqlite3") as store:
        store.create_checkpoint(
            tenant_id="tenant-legacy",
            execution_id="execution-legacy",
            workflow_version=1,
            specification=specification,
            status="paused",
        )

        service = WorkflowExecutionService(
            broker,
            checkpoint_store=store,
        )

        with pytest.raises(WorkflowAuthorizationError):
            await service.resume(
                tenant_id="tenant-legacy",
                execution_id="execution-legacy",
                allowed_tools=broker.registered_tools,
            )

        # Rejected before the checkpoint is claimed.
        checkpoint = store.get_checkpoint(
            tenant_id="tenant-legacy",
            execution_id="execution-legacy",
        )

        assert checkpoint is not None
        assert checkpoint.status == "paused"
