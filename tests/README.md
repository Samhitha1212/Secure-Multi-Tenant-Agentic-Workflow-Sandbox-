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

## Test results and evidence

See `M1_TEST_RECORD.md` for test IDs, observed results, limitations,
reproduction instructions and remaining acceptance tests.

Captured command output from the recorded session is under
`evidence/2026-10-29/`.

## Code quality

Run:

    ruff check .
