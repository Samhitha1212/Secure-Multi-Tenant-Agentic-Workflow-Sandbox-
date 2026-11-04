import asyncio
from types import SimpleNamespace

import pytest

from secure_agentic_sandbox import runner


class FakeFiles:
    def __init__(self):
        self.entries = None

    async def write_files(self, entries):
        self.entries = entries

class FakeCommands:
    def __init__(
        self,
        exit_code=0,
        delay=0,
        error=None,
        stdout_events=None,
        stderr_events=None,
    ):
        self.exit_code = exit_code
        self.delay = delay
        self.error = error

        self.stdout_events = (
            ["hello stdout\n"]
            if stdout_events is None
            else stdout_events
        )

        self.stderr_events = (
            ["hello stderr\n"]
            if stderr_events is None
            else stderr_events
        )

    async def run(self, command, handlers):
        assert command == f"python -u {runner.SANDBOX_FILE}"

        for chunk in self.stdout_events:
            await handlers.on_stdout(
                SimpleNamespace(text=chunk)
            )

        for chunk in self.stderr_events:
            await handlers.on_stderr(
                SimpleNamespace(text=chunk)
            )

        if self.error is not None:
            raise self.error

        if self.delay:
            await asyncio.sleep(self.delay)

        return SimpleNamespace(exit_code=self.exit_code)

class FakeSandbox:
    def __init__(self, commands):
        self.id = "fake-sandbox-id"
        self.files = FakeFiles()
        self.commands = commands
        self.destroy_calls = 0

    async def destroy(self):
        self.destroy_calls += 1


def install_fake_sandbox(monkeypatch, sandbox):
    async def fake_create(*args, **kwargs):
        return sandbox

    monkeypatch.setattr(
        runner.Sandbox, "create", fake_create
    )
    monkeypatch.setattr(
        runner, "make_connection_config", lambda: object()
    )


def make_submission(tmp_path):
    source = tmp_path / "submission.py"
    source.write_text(
        'print("hello")\n',
        encoding="utf-8",
    )
    return source


def test_success_returns_output_and_cleans_up(
    tmp_path, monkeypatch
):
    sandbox = FakeSandbox(FakeCommands())
    install_fake_sandbox(monkeypatch, sandbox)

    result = asyncio.run(
        runner.run_python_file(make_submission(tmp_path))
    )

    assert result.sandbox_id == "fake-sandbox-id"
    assert result.stdout == "hello stdout\n"
    assert result.stderr == "hello stderr\n"
    assert result.exit_code == 0
    assert result.timed_out is False
    assert sandbox.destroy_calls == 1


def test_nonzero_exit_code_and_cleanup(
    tmp_path, monkeypatch
):
    sandbox = FakeSandbox(FakeCommands(exit_code=7))
    install_fake_sandbox(monkeypatch, sandbox)

    result = asyncio.run(
        runner.run_python_file(make_submission(tmp_path))
    )

    assert result.exit_code == 7
    assert result.timed_out is False
    assert sandbox.destroy_calls == 1


def test_timeout_preserves_partial_output_and_cleans_up(
    tmp_path, monkeypatch
):
    sandbox = FakeSandbox(FakeCommands(delay=2))
    install_fake_sandbox(monkeypatch, sandbox)

    result = asyncio.run(
        runner.run_python_file(
            make_submission(tmp_path),
            timeout_seconds=1,
        )
    )

    assert result.timed_out is True
    assert result.exit_code is None
    assert result.stdout == "hello stdout\n"
    assert "timed out" in result.stderr.lower()
    assert sandbox.destroy_calls == 1


def test_command_exception_still_cleans_up(
    tmp_path, monkeypatch
):
    sandbox = FakeSandbox(
        FakeCommands(error=RuntimeError("simulated failure"))
    )
    install_fake_sandbox(monkeypatch, sandbox)

    with pytest.raises(RuntimeError, match="simulated failure"):
        asyncio.run(
            runner.run_python_file(make_submission(tmp_path))
        )

    assert sandbox.destroy_calls == 1


def test_output_preserves_newlines_and_partial_chunks(
    tmp_path, monkeypatch
):
    stdout_events = [
        "LINE_1\n",
        "\n",
        "PARTIAL",
        " + CONTINUED\n",
        "FINAL_LINE_WITHOUT_NEWLINE",
    ]

    stderr_events = [
        "ERROR_LINE_1\n",
        "ERROR_LINE_2\n",
    ]

    sandbox = FakeSandbox(
        FakeCommands(
            stdout_events=stdout_events,
            stderr_events=stderr_events,
        )
    )

    install_fake_sandbox(monkeypatch, sandbox)

    result = asyncio.run(
        runner.run_python_file(make_submission(tmp_path))
    )

    assert result.stdout == (
        "LINE_1\n\n"
        "PARTIAL + CONTINUED\n"
        "FINAL_LINE_WITHOUT_NEWLINE"
    )

    assert result.stdout == "".join(stdout_events)
    assert result.stderr == "".join(stderr_events)
    assert sandbox.destroy_calls == 1


def test_upload_failure_still_cleans_up(
    tmp_path, monkeypatch
):
    sandbox = FakeSandbox(FakeCommands())
    install_fake_sandbox(monkeypatch, sandbox)

    async def fail_upload(entries):
        raise RuntimeError("simulated upload failure")

    monkeypatch.setattr(
        sandbox.files,
        "write_files",
        fail_upload,
    )

    with pytest.raises(RuntimeError, match="simulated upload failure"):
        asyncio.run(
            runner.run_python_file(make_submission(tmp_path))
        )

    assert sandbox.destroy_calls == 1


def test_destroy_failure_is_reported(
    tmp_path, monkeypatch
):
    sandbox = FakeSandbox(FakeCommands())

    async def fail_destroy():
        sandbox.destroy_calls += 1
        raise RuntimeError("simulated destroy failure")

    monkeypatch.setattr(sandbox, "destroy", fail_destroy)
    install_fake_sandbox(monkeypatch, sandbox)

    with pytest.raises(
        RuntimeError, match="simulated destroy failure"
    ):
        asyncio.run(
            runner.run_python_file(make_submission(tmp_path))
        )

    assert sandbox.destroy_calls == 1


def test_upload_error_preserved_when_destroy_also_fails(
    tmp_path, monkeypatch
):
    sandbox = FakeSandbox(FakeCommands())

    async def fail_upload(entries):
        raise RuntimeError("simulated upload failure")

    async def fail_destroy():
        sandbox.destroy_calls += 1
        raise RuntimeError("simulated destroy failure")

    monkeypatch.setattr(
        sandbox.files, "write_files", fail_upload
    )
    monkeypatch.setattr(
        sandbox, "destroy", fail_destroy
    )

    install_fake_sandbox(monkeypatch, sandbox)

    with pytest.raises(
        RuntimeError, match="simulated upload failure"
    ) as exc_info:
        asyncio.run(
            runner.run_python_file(make_submission(tmp_path))
        )

    assert sandbox.destroy_calls == 1
    assert any(
        "simulated destroy failure" in note
        for note in exc_info.value.__notes__
    )

def test_runner_passes_resource_limits_to_sandbox_create(
    tmp_path, monkeypatch
):
    sandbox = FakeSandbox(FakeCommands())
    received = {}

    async def fake_create(*args, **kwargs):
        received.update(kwargs)
        return sandbox

    monkeypatch.setattr(
        runner.Sandbox,
        "create",
        fake_create,
    )
    monkeypatch.setattr(
        runner,
        "make_connection_config",
        lambda: object(),
    )

    result = asyncio.run(
        runner.run_python_file(
            make_submission(tmp_path),
            cpu="750m",
            memory="768Mi",
            max_output_bytes=64 * 1024,
        )
    )

    assert received["resource"] == {
        "cpu": "750m",
        "memory": "768Mi",
    }

    assert sandbox.destroy_calls == 1
    assert result.exit_code == 0