from secure_agentic_sandbox.runner import BoundedOutputCollector


def test_collects_stdout_and_stderr_within_limit():
    collector = BoundedOutputCollector(max_bytes=20)

    collector.append_stdout("stdout\n")
    collector.append_stderr("stderr\n")

    assert collector.stdout == "stdout\n"
    assert collector.stderr == "stderr\n"
    assert collector.bytes_used == 14
    assert collector.truncated is False


def test_stdout_and_stderr_share_combined_byte_budget():
    collector = BoundedOutputCollector(max_bytes=10)

    collector.append_stdout("123456")
    collector.append_stderr("abcdef")

    assert collector.stdout == "123456"
    assert collector.stderr == "abcd"
    assert collector.bytes_used == 10
    assert collector.truncated is True


def test_output_beyond_limit_is_truncated():
    collector = BoundedOutputCollector(max_bytes=5)

    collector.append_stdout("1234567")

    assert collector.stdout == "12345"
    assert collector.bytes_used == 5
    assert collector.truncated is True


def test_exact_limit_is_not_marked_truncated_until_more_output_arrives():
    collector = BoundedOutputCollector(max_bytes=5)

    collector.append_stdout("12345")

    assert collector.stdout == "12345"
    assert collector.bytes_used == 5
    assert collector.truncated is False

    collector.append_stderr("x")

    assert collector.stderr == ""
    assert collector.bytes_used == 5
    assert collector.truncated is True


def test_utf8_character_is_not_split_at_limit():
    collector = BoundedOutputCollector(max_bytes=4)

    collector.append_stdout("ab€x")

    assert collector.stdout == "ab"
    assert collector.stdout.encode("utf-8") == b"ab"
    assert collector.bytes_used == 2
    assert collector.truncated is True


def test_later_chunks_are_discarded_after_truncation():
    collector = BoundedOutputCollector(max_bytes=3)

    collector.append_stdout("abcd")

    assert collector.stdout == "abc"
    assert collector.truncated is True

    collector.append_stderr("error")
    collector.append_stdout("more output")

    assert collector.stdout == "abc"
    assert collector.stderr == ""
    assert collector.bytes_used == 3


def test_empty_chunks_do_not_consume_output_budget():
    collector = BoundedOutputCollector(max_bytes=3)

    collector.append_stdout("")
    collector.append_stderr("")

    collector.append_stdout("abc")

    assert collector.stdout == "abc"
    assert collector.stderr == ""
    assert collector.bytes_used == 3
    assert collector.truncated is False
