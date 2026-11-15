# Project Tests

## Unit tests

The unit tests use pytest and cover source validation and mocked execution
lifecycle behaviour.

Run:

    pytest -q

## Real sandbox fixtures

`fixtures/m1/` contains Python programs used to exercise the actual
OpenSandbox execution service:

- `real_success.py` — normal execution and output capture.
- `exit_7.py` — nonzero exit status.
- `timeout.py` — bounded execution timeout.
- `isolation_a.py` — creates a private file in execution A.
- `isolation_b.py` — checks whether execution B can see that file.

These programs must be run through the sandbox CLI, not directly with
the host Python interpreter, when testing sandbox behaviour.

The OpenSandbox server must be running for real integration tests.

## Live M4 integration tests

The live M4 integration tests require:
- Docker running with the `runsc` runtime installed.
- OpenSandbox server running at `http://127.0.0.1:8080`.
- The `python:3.11` execution image available locally.

### 1. Live pause and resume

Run:

    uv run python tests/integration/m4_live_pause_resume.py

This test verifies:
- Real Python execution using OpenSandbox and gVisor.
- Pause at a workflow step boundary.
- Persistence of completed step results.
- Resume from the first unfinished step in a new sandbox.
- Container cleanup after pause and completion.

### 2. API crash recovery

Run:

    uv run python tests/integration/m4_api_crash_recovery.py

This test verifies:
- Workflow submission through the FastAPI endpoint.
- Recovery after abrupt API process termination.
- Restoration of checkpoint state after API restart.
- HTTP resume from the recovered checkpoint.
- Avoidance of replaying completed workflow steps.
- Execution of the unfinished step in a new gVisor sandbox.

The test manually removes the interrupted container to avoid leaving
an orphaned sandbox behind.

### Recorded evidence

Recorded integration output is available under:

    tests/evidence/2026-11-15/

See `M4_TEST_RECORD.md` for implementation details, test results,
limitations and acceptance notes.