"""Unit tests for the LangGraph execution workflow."""

from uuid import UUID

import pytest

from secure_agentic_sandbox import runner
from secure_agentic_sandbox import workflow as workflow_module


@pytest.fixture
def valid_submission(tmp_path):
    """Create a valid Python submission for workflow tests."""

    path = tmp_path / "submission.py"
    path.write_text('print("Hello from workflow")\n', encoding="utf-8")
    return path


def make_result(exit_code, timed_out=False):
    """Create a representative sandbox execution result."""

    return runner.ExecutionResult(
        sandbox_id="test-sandbox-id",
        stdout="Hello from workflow\n",
        stderr="",
        exit_code=exit_code,
        timed_out=timed_out,
        duration_seconds=0.5,
    )


@pytest.mark.asyncio
async def test_workflow_success(valid_submission, monkeypatch):
    """A successful sandbox execution should produce succeeded status."""

    async def fake_run(path, timeout_seconds):
        return make_result(exit_code=0)

    monkeypatch.setattr(
        workflow_module.runner,
        "run_python_file",
        fake_run,
    )

    result = await workflow_module.run_workflow(valid_submission)

    assert result["status"] == "succeeded"
    assert result["execution_result"]["exit_code"] == 0
    assert result["execution_result"]["stdout"] == (
        "Hello from workflow\n"
    )


@pytest.mark.asyncio
async def test_workflow_nonzero_exit(valid_submission, monkeypatch):
    """A nonzero program exit should produce failed status."""

    async def fake_run(path, timeout_seconds):
        return make_result(exit_code=7)

    monkeypatch.setattr(
        workflow_module.runner,
        "run_python_file",
        fake_run,
    )

    result = await workflow_module.run_workflow(valid_submission)

    assert result["status"] == "failed"
    assert result["execution_result"]["exit_code"] == 7


@pytest.mark.asyncio
async def test_workflow_timeout(valid_submission, monkeypatch):
    """A timed-out execution should produce timed_out status."""

    async def fake_run(path, timeout_seconds):
        return make_result(exit_code=None, timed_out=True)

    monkeypatch.setattr(
        workflow_module.runner,
        "run_python_file",
        fake_run,
    )

    result = await workflow_module.run_workflow(valid_submission)

    assert result["status"] == "timed_out"
    assert result["execution_result"]["timed_out"] is True


@pytest.mark.asyncio
async def test_workflow_validation_failure(tmp_path, monkeypatch):
    """An invalid submission should not reach the execution node."""

    path = tmp_path / "submission.txt"
    path.write_text("invalid submission", encoding="utf-8")

    async def fake_run(path, timeout_seconds):
        raise AssertionError("Sandbox must not be invoked")

    monkeypatch.setattr(
        workflow_module.runner,
        "run_python_file",
        fake_run,
    )

    result = await workflow_module.run_workflow(path)

    assert result["status"] == "validation_failed"
    assert result["validation_error"] is not None
    assert result["execution_result"] is None


@pytest.mark.asyncio
async def test_workflow_invalid_timeout(valid_submission, monkeypatch):
    """An invalid timeout should prevent sandbox execution."""

    async def fake_run(path, timeout_seconds):
        raise AssertionError("Sandbox must not be invoked")

    monkeypatch.setattr(
        workflow_module.runner,
        "run_python_file",
        fake_run,
    )

    result = await workflow_module.run_workflow(
        valid_submission,
        timeout_seconds=0,
    )

    assert result["status"] == "validation_failed"
    assert "Timeout must be between" in result["validation_error"]


@pytest.mark.asyncio
async def test_workflow_propagates_infrastructure_error(
    valid_submission,
    monkeypatch,
):
    """Unexpected infrastructure errors should propagate."""

    async def fake_run(path, timeout_seconds):
        raise ConnectionError("OpenSandbox server unavailable")

    monkeypatch.setattr(
        workflow_module.runner,
        "run_python_file",
        fake_run,
    )

    with pytest.raises(
        ConnectionError,
        match="OpenSandbox server unavailable",
    ):
        await workflow_module.run_workflow(valid_submission)



@pytest.mark.asyncio
async def test_workflow_generates_execution_id(
    valid_submission,
    monkeypatch,
):
    """Workflow should generate a valid UUID automatically."""

    async def fake_run(path, timeout_seconds):
        return make_result(exit_code=0)

    monkeypatch.setattr(
        workflow_module.runner,
        "run_python_file",
        fake_run,
    )

    result = await workflow_module.run_workflow(valid_submission)

    assert result["execution_id"]
    UUID(result["execution_id"])


@pytest.mark.asyncio
async def test_workflow_preserves_supplied_execution_id(
    valid_submission,
    monkeypatch,
):
    """Workflow should preserve a platform-supplied execution ID."""

    async def fake_run(path, timeout_seconds):
        return make_result(exit_code=0)

    monkeypatch.setattr(
        workflow_module.runner,
        "run_python_file",
        fake_run,
    )

    result = await workflow_module.run_workflow(
        valid_submission,
        execution_id="exec-test-001",
    )

    assert result["execution_id"] == "exec-test-001"


@pytest.mark.asyncio
async def test_workflow_rejects_empty_execution_id(valid_submission):
    """Workflow should reject an empty execution ID."""

    with pytest.raises(
        ValueError,
        match="Execution ID cannot be empty",
    ):
        await workflow_module.run_workflow(
            valid_submission,
            execution_id=" ",
        )