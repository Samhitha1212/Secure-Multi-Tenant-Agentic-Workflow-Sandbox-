"""Tests for inline Python source execution."""

import pytest

from secure_agentic_sandbox import runner
from secure_agentic_sandbox.execution_config import ExecutionConfig


def test_validate_python_source_accepts_valid_source():
    source = "print('Hello from sandbox')"

    assert runner.validate_python_source(source) == source


def test_validate_python_source_rejects_non_string():
    with pytest.raises(runner.SubmissionValidationError):
        runner.validate_python_source(None)


def test_validate_python_source_rejects_oversized_source():
    source = "x" * (runner.MAX_SOURCE_BYTES + 1)

    with pytest.raises(
        runner.SubmissionValidationError,
        match="exceeds",
    ):
        runner.validate_python_source(source)


def test_validate_python_source_rejects_invalid_unicode():
    source = "\ud800"

    with pytest.raises(
        runner.SubmissionValidationError,
        match="UTF-8",
    ):
        runner.validate_python_source(source)


@pytest.mark.asyncio
async def test_run_python_source_uses_shared_execution_path(monkeypatch):
    captured = {}

    expected_result = runner.ExecutionResult(
        sandbox_id="test-sandbox",
        stdout="Hello\n",
        stderr="",
        exit_code=0,
        timed_out=False,
        duration_seconds=0.1,
    )

    async def fake_execute(source, execution_config):
        captured["source"] = source
        captured["config"] = execution_config
        return expected_result

    monkeypatch.setattr(
        runner,
        "_execute_python_source",
        fake_execute,
    )

    source = "print('Hello')"

    result = await runner.run_python_source(
        source,
        timeout_seconds=5,
        cpu="500m",
        memory="512Mi",
        max_output_bytes=65536,
    )

    assert result is expected_result
    assert captured["source"] == source
    assert captured["config"] == ExecutionConfig(
        cpu="500m",
        memory="512Mi",
        timeout_seconds=5,
        max_output_bytes=65536,
    )


@pytest.mark.asyncio
async def test_run_python_file_uses_shared_execution_path(monkeypatch):
    captured = {}

    expected_result = runner.ExecutionResult(
        sandbox_id="test-sandbox",
        stdout="File execution\n",
        stderr="",
        exit_code=0,
        timed_out=False,
        duration_seconds=0.1,
    )

    async def fake_execute(source, execution_config):
        captured["source"] = source
        captured["config"] = execution_config
        return expected_result

    monkeypatch.setattr(
        runner,
        "load_python_source",
        lambda path: "print('File execution')",
    )

    monkeypatch.setattr(
        runner,
        "_execute_python_source",
        fake_execute,
    )

    result = await runner.run_python_file(
        "submission.py",
        timeout_seconds=5,
    )

    assert result is expected_result
    assert captured["source"] == "print('File execution')"
    assert isinstance(captured["config"], ExecutionConfig)


@pytest.mark.asyncio
async def test_invalid_resource_configuration_is_rejected(monkeypatch):
    async def should_not_execute(*args, **kwargs):
        pytest.fail("Sandbox execution must not start.")

    monkeypatch.setattr(
        runner,
        "_execute_python_source",
        should_not_execute,
    )

    with pytest.raises(ValueError):
        await runner.run_python_source(
            "print('Hello')",
            timeout_seconds=500,
        )
    
def test_serialize_workflow_input_accepts_mapping():
    data = {
        "inventory": {
            "sku": "SKU-100",
            "quantity": 12,
        }
    }

    serialized = runner.serialize_workflow_input(data)

    import json
    assert json.loads(serialized) == data


def test_serialize_workflow_input_accepts_none():
    assert runner.serialize_workflow_input(None) is None


def test_serialize_workflow_input_rejects_non_mapping():
    with pytest.raises(
        runner.SubmissionValidationError,
        match="JSON object",
    ):
        runner.serialize_workflow_input(["not", "an", "object"])


def test_serialize_workflow_input_rejects_non_json_values():
    with pytest.raises(
        runner.SubmissionValidationError,
        match="JSON-compatible",
    ):
        runner.serialize_workflow_input({"value": object()})


def test_serialize_workflow_input_rejects_non_string_keys():
    with pytest.raises(
        runner.SubmissionValidationError,
        match="JSON-compatible",
    ):
        runner.serialize_workflow_input({123: "invalid key"})


def test_serialize_workflow_input_rejects_oversized_data():
    data = {
        "value": "x" * runner.MAX_WORKFLOW_INPUT_BYTES,
    }

    with pytest.raises(
        runner.SubmissionValidationError,
        match="exceeds",
    ):
        runner.serialize_workflow_input(data)


@pytest.mark.asyncio
async def test_run_python_source_forwards_workflow_input(monkeypatch):
    captured = {}

    async def fake_execute(
        source,
        execution_config,
        input_data_json=None,
    ):
        captured["source"] = source
        captured["input_data_json"] = input_data_json

        return runner.ExecutionResult(
            sandbox_id="test-sandbox",
            stdout="Input received\n",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.1,
        )

    monkeypatch.setattr(
        runner,
        "_execute_python_source",
        fake_execute,
    )

    data = {
        "inventory": {
            "sku": "SKU-100",
            "quantity": 12,
        }
    }

    result = await runner.run_python_source(
        "print('Reading workflow input')",
        input_data=data,
    )

    import json
    assert result.exit_code == 0
    assert json.loads(captured["input_data_json"]) == data