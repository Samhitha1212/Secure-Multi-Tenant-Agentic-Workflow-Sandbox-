"""Tests for bounded structured output retrieval."""

import pytest

from secure_agentic_sandbox import runner


class FakeFilesystem:
    def __init__(self, chunks):
        self.chunks = chunks
        self.path = None
        self.chunk_size = None
        self.closed = False

    async def read_bytes_stream(self, path, *, chunk_size):
        self.path = path
        self.chunk_size = chunk_size

        async def generate():
            try:
                for chunk in self.chunks:
                    yield chunk
            finally:
                self.closed = True

        return generate()


class FakeSandbox:
    def __init__(self, chunks):
        self.files = FakeFilesystem(chunks)


@pytest.mark.asyncio
async def test_reads_structured_json_across_chunks():
    sandbox = FakeSandbox([
        b'{"sku": "SKU-',
        b'100", "quantity": 12}',
    ])

    result = await runner._read_workflow_output(sandbox)

    assert result == {
        "sku": "SKU-100",
        "quantity": 12,
    }
    assert sandbox.files.path == runner.SANDBOX_OUTPUT_FILE
    assert sandbox.files.chunk_size == 16 * 1024
    assert sandbox.files.closed is True


@pytest.mark.asyncio
async def test_rejects_invalid_json():
    sandbox = FakeSandbox([b'{"sku":'])

    with pytest.raises(
        runner.SubmissionValidationError,
        match="valid UTF-8 JSON",
    ):
        await runner._read_workflow_output(sandbox)

    assert sandbox.files.closed is True


@pytest.mark.asyncio
async def test_rejects_non_object_json():
    sandbox = FakeSandbox([b'[1, 2, 3]'])

    with pytest.raises(
        runner.SubmissionValidationError,
        match="JSON object",
    ):
        await runner._read_workflow_output(sandbox)


@pytest.mark.asyncio
async def test_rejects_oversized_output():
    sandbox = FakeSandbox([
        b"x" * (runner.MAX_WORKFLOW_OUTPUT_BYTES + 1),
    ])

    with pytest.raises(
        runner.SubmissionValidationError,
        match="exceeds",
    ):
        await runner._read_workflow_output(sandbox)

    assert sandbox.files.closed is True


@pytest.mark.asyncio
async def test_rejects_invalid_utf8():
    sandbox = FakeSandbox([b'{"value": "\xff"}'])

    with pytest.raises(
        runner.SubmissionValidationError,
        match="valid UTF-8 JSON",
    ):
        await runner._read_workflow_output(sandbox)


@pytest.mark.asyncio
async def test_rejects_nonstandard_nan():
    sandbox = FakeSandbox([b'{"value": NaN}'])

    with pytest.raises(
        runner.SubmissionValidationError,
        match="valid UTF-8 JSON",
    ):
        await runner._read_workflow_output(sandbox)


@pytest.mark.asyncio
async def test_rejects_nonstandard_infinity():
    sandbox = FakeSandbox([b'{"value": Infinity}'])

    with pytest.raises(
        runner.SubmissionValidationError,
        match="valid UTF-8 JSON",
    ):
        await runner._read_workflow_output(sandbox)
