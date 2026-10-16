import pytest

from secure_agentic_sandbox.runner import (
    MAX_SOURCE_BYTES,
    SubmissionValidationError,
    load_python_source,
)


def test_loads_python_source(tmp_path):
    source_file = tmp_path / "main.py"
    source_file.write_text('print("hello")\n', encoding="utf-8")

    assert load_python_source(source_file) == 'print("hello")\n'


def test_rejects_non_python_file(tmp_path):
    source_file = tmp_path / "main.txt"
    source_file.write_text("hello", encoding="utf-8")

    with pytest.raises(SubmissionValidationError):
        load_python_source(source_file)


def test_rejects_missing_file(tmp_path):
    with pytest.raises(SubmissionValidationError):
        load_python_source(tmp_path / "missing.py")


def test_rejects_oversized_source(tmp_path):
    source_file = tmp_path / "large.py"
    source_file.write_bytes(b"x" * (MAX_SOURCE_BYTES + 1))

    with pytest.raises(SubmissionValidationError):
        load_python_source(source_file)


def test_rejects_invalid_utf8(tmp_path):
    source_file = tmp_path / "invalid.py"
    source_file.write_bytes(b"\xff\xfe")

    with pytest.raises(SubmissionValidationError):
        load_python_source(source_file)