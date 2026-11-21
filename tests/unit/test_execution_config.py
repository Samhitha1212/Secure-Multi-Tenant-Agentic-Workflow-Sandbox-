"""Tests for centralized execution resource configuration."""

import pytest

from secure_agentic_sandbox.execution_config import ExecutionConfig


@pytest.mark.parametrize(
    ("cpu", "memory", "timeout"),
    [
        ("500m", "512Mi", 10),
        ("250m", "128Mi", 20),
        ("1", "1Gi", 30),
        ("2", "2Gi", 120),
        ("100m", "64Mi", 1),
    ],
)
def test_valid_execution_config(cpu, memory, timeout):
    config = ExecutionConfig(
        cpu=cpu,
        memory=memory,
        timeout_seconds=timeout,
    )

    assert config.cpu == cpu
    assert config.memory == memory
    assert config.timeout_seconds == timeout


@pytest.mark.parametrize(
    ("cpu", "memory"),
    [
        ("50m", "512Mi"),
        ("3", "512Mi"),
        ("invalid", "512Mi"),
        ("500m", "32Mi"),
        ("500m", "3Gi"),
        ("500m", "invalid"),
    ],
)
def test_invalid_resource_config(cpu, memory):
    with pytest.raises(ValueError):
        ExecutionConfig(cpu=cpu, memory=memory)


@pytest.mark.parametrize("timeout", [0, -1, 121, True])
def test_invalid_timeout(timeout):
    with pytest.raises(ValueError):
        ExecutionConfig(timeout_seconds=timeout)


def test_sandbox_resource_conversion():
    config = ExecutionConfig(cpu="1", memory="1Gi")

    assert config.to_sandbox_resources() == {
        "cpu": "1",
        "memory": "1Gi",
    }


from secure_agentic_sandbox.execution_config import parse_output_size


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1KiB", 1024),
        ("64KiB", 65536),
        ("1MiB", 1048576),
        ("10MiB", 10485760),
    ],
)
def test_parse_output_size(value, expected):
    assert parse_output_size(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "0B",
        "500B",
        "11MiB",
        "invalid",
        "1GiB",
    ],
)
def test_invalid_output_size(value):
    with pytest.raises(ValueError):
        parse_output_size(value)


def test_execution_config_default_output_limit():
    config = ExecutionConfig()

    assert config.max_output_bytes == 1024 * 1024


def test_execution_config_custom_output_limit():
    config = ExecutionConfig(max_output_bytes=2 * 1024 * 1024)

    assert config.max_output_bytes == 2 * 1024 * 1024