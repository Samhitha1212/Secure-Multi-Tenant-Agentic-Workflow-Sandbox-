# Project Tests

## Unit tests

The unit tests use pytest and cover source validation, workflow execution, API behaviour, checkpoint persistence, tenant isolation and mocked execution lifecycle behaviour.

Run the complete test suite from the repository root:

```bash
uv run pytest -q
```

## Real sandbox fixtures

`fixtures/m1/` contains Python programs used to exercise the actual OpenSandbox execution service:

- `real_success.py` — normal execution and output capture.
- `exit_7.py` — nonzero exit status.
- `timeout.py` — bounded execution timeout.
- `isolation_a.py` — creates a private file in execution A.
- `isolation_b.py` — checks whether execution B can see that file.

These programs must be run through the sandbox CLI, not directly with the host Python interpreter, when testing sandbox behaviour.

The OpenSandbox server must be running for real sandbox integration tests.

## Live M4 integration tests

The live M4 integration tests require:

- Docker running with the `runsc` runtime installed.
- OpenSandbox server running at `http://127.0.0.1:8080`.
- The `python:3.11` execution image available locally.
- The project Python environment installed.

### 1. Live pause and resume

Run:

```bash
uv run python tests/integration/m4_live_pause_resume.py
```

This test verifies:

- Real Python execution using OpenSandbox and gVisor.
- Pause at a workflow step boundary.
- Persistence of completed step results.
- Resume from the first unfinished step in a new sandbox.
- Container cleanup after pause and completion.

### 2. API crash recovery

Run:

```bash
uv run python tests/integration/m4_api_crash_recovery.py
```

This test verifies:

- Workflow submission through the FastAPI endpoint.
- Recovery after abrupt API process termination using SIGKILL.
- Restoration of checkpoint state after API restart.
- HTTP resume from the recovered checkpoint.
- Avoidance of replaying completed workflow steps.
- Execution of the unfinished step in a new gVisor sandbox.

The test manually removes the interrupted container because automatic orphan-container cleanup after an abrupt API crash is not implemented.

## Recorded evidence

Integration-test output and supporting evidence are stored under:

```text
tests/evidence/
├── 2026-10-29/
├── 2026-11-02/
├── 2026-11-03/
├── 2026-11-04/
├── 2026-11-15/
└── 2026-11-21/
```

The latest live M4 integration logs are:

- `tests/evidence/2026-11-21/m4-live-pause-resume.log`
- `tests/evidence/2026-11-21/m4-api-crash-recovery.log`

## Detailed test records

The milestone-specific test records document the test scenarios, expected behaviour, actual results, limitations and acceptance notes:

- `M1_TEST_RECORD.md`
- `M2_TEST_RECORD.md`
- `M3_TEST_RECORD.md`
- `M4_TEST_RECORD.md`

The latest M4 live integration results are recorded in `M4_TEST_RECORD.md`, with captured output under `evidence/2026-11-21/`.