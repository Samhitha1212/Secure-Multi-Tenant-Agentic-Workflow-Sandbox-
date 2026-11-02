# M1 Test Record — Isolated Python Execution Service

Date of recorded runs: 2026-10-29
Branch: feat/m1-execution-service
Initial implementation commit: 3bd12ae

## Purpose

Record unit tests and real OpenSandbox/gVisor tests for M1.
Distinguish observed results from security properties that remain unverified.

## Environment

- Host: Ubuntu 22.04.5 LTS
- Docker: 29.1.3
- Host Python: 3.13.x
- Execution image: python:3.11
- Docker runtime: runsc (gVisor)
- OpenSandbox endpoint: http://127.0.0.1:8080

The OpenSandbox health endpoint returned {"status":"healthy"}.
Docker listed runsc as an available runtime.
The python:3.11 image was available locally.

## Automated unit tests

Command:

    pytest -q

Observed result after adding four mocked lifecycle tests:

    9 passed in 1.50s

Command:

    ruff check .

Observed result:

    All checks passed!

The initial five tests cover Python source validation.
The four additional lifecycle tests use a fake sandbox and cover:
- successful result handling and cleanup attempt;
- nonzero exit code and cleanup attempt;
- timeout, partial output and cleanup attempt;
- execution exception and cleanup attempt.

These mocked tests do not independently verify Docker, gVisor or OpenSandbox.

## Real sandbox tests

### INT-01: Successful Python execution

Program:
- Prints M1_REAL_SANDBOX_OK.
- Prints the result of 2 + 3.
- Sleeps briefly to allow runtime inspection.
- Prints M1_REAL_SANDBOX_FINISHED.

Observed:
- exit_code: 0
- timed_out: false
- duration: approximately 9.08 seconds
- Docker inspection while running: Runtime=runsc
- Container was removed after execution.

Issue observed:
The returned stdout concatenated separate print lines without the expected
line separators. Investigate output streaming and capture before closing M1.

### INT-02: Nonzero exit code

Program:
- Prints M1_FAILURE_TEST_STARTED.
- Exits with status 7.

Observed:
- Result exit_code: 7
- CLI exit status: 7
- timed_out: false

This verifies failure-status propagation. Container cleanup was not
independently inspected during this particular run.

### INT-03: Timeout

Program:
- Prints M1_TIMEOUT_TEST_STARTED.
- Sleeps for 30 seconds.

Command used a two-second deadline.

Observed:
- timed_out: true
- exit_code in result: null
- CLI exit status: 124
- Partial output preserved.
- Timeout information appeared in stderr.
- Duration: approximately 2.003 seconds.
- Docker inspection while running: Runtime=runsc
- Container was removed after execution.
  
### INT-04: Concurrent filesystem isolation — PASSED

Objective:
Verify that one sandbox cannot access files created
inside another sandbox during concurrent execution.

Tenant A:
- Created a private file inside its sandbox.
- Printed EXECUTION_A_FILE_CREATED.
- Completed execution successfully.
- Exit code: 0
- Duration: 15.51 seconds

Tenant B:
- Attempted to access Tenant A's private file.
- Printed ISOLATION_PASS_FOREIGN_FILE_NOT_VISIBLE.
- Completed execution successfully.
- Exit code: 0
- Duration: 5.49 seconds

Result:
Tenant B could not access Tenant A's private file
while both executions were running.

Conclusion:
Basic filesystem isolation between concurrent
sandbox executions was successfully demonstrated.

Evidence:
- m1-isolation-a-rerun.log
- m1-isolation-b-rerun.log
  

### INT-05: Proxy behaviour

The OpenSandbox client repeatedly logged an HTTP 502 retry against the
sandbox proxy ping endpoint before the executions completed successfully.

Status: unresolved; investigate separately. Do not silently discard
these warnings from future evidence.

## What is demonstrated so far

- Python can execute through the real OpenSandbox service.
- Docker showed runsc for the inspected execution containers.
- A nonzero exit status was returned to the CLI.
- The tested timeout returned the expected timeout result and CLI status.
- Cleanup was observed after successful execution and timeout.
- Two concurrent executions used separate containers, and the second
  could not see the first execution's test file.

## What is not yet demonstrated

- Correct newline preservation in captured stdout.
- Explicitly bounded stdout/stderr accumulation.
- Cleanup independently verified after nonzero exit and each creation,
  upload and execution failure.
- Full multi-tenant authorization, cross-tenant result access or
  management/control API isolation.
- Successful completion of both concurrent executions in one test.
- All required M1 execution modes, including the supported workflow mode.
- M2 resource and network enforcement. Those require separate acceptance
  tests and must not be inferred from the M1 results.

## Reproducibility commands

Run the unit suite:

    pytest -q

Run lint:

    ruff check .

Start OpenSandbox using the existing local configuration, then run the
fixtures from the project environment:

    uv run --group dev secure-agentic-sandbox tests/fixtures/m1/real_success.py --timeout 20
    uv run --group dev secure-agentic-sandbox tests/fixtures/m1/exit_7.py --timeout 20
    uv run --group dev secure-agentic-sandbox tests/fixtures/m1/timeout.py --timeout 2

For the isolation case, start isolation_a.py with --timeout 20, wait
until its container is running and its private file has been created,
then launch isolation_b.py while A is still running.

Inspect the runtime while each test container is running:

    docker inspect CONTAINER_ID --format 'Runtime={{.HostConfig.Runtime}} Status={{.State.Status}}'

After completion, verify removal:

    docker inspect CONTAINER_ID

A successful cleanup check means Docker can no longer inspect that
container ID. Record the observed result rather than assuming cleanup.

## Evidence standard

For each test, preserve:
- the exact command and fixture version;
- the test output and CLI exit status;
- runtime inspection while the container is running;
- post-execution cleanup result where applicable;
- the commit hash and environment information.

A passing test supports only the behaviour it actually exercises.

### INT-06: Output newline preservation

Problem:
OpenSandbox v1.1.0 execd removed newline terminators
from streamed stdout/stderr events.

Fix:
Aligned the OpenSandbox server, Python SDK and execd
image to the 1.2.0 release.

Verification:
- Separate stdout lines preserve newline characters.
- Separate stderr lines preserve newline characters.
- Blank lines are preserved.
- Partial output fragments are reconstructed correctly.
- Unterminated final output remains unterminated.
- Returned execution logs preserve the same content.

Runner modification: Not required.
The existing chunk concatenation logic remains correct.

Status: Fixed and verified using a real sandbox diagnostic.


### INT-05: OpenSandbox proxy readiness

Observation:
- Initial proxy ping returned HTTP 502.
- A subsequent ping returned HTTP 200.
- File upload and command execution returned HTTP 200.
- Sandbox cleanup completed successfully.
- DELETE request returned HTTP 204.

Conclusion:
The proxy experienced a transient startup/readiness issue.
The retry succeeded, and execution was not affected in this run.

Status:
Execution successful; transient proxy warning observed.
Further repeated-run validation required.


#### INT-04 Controlled concurrent execution — PASSED

Objective:
Demonstrate filesystem isolation while two sandbox
programs are genuinely executing concurrently.

Tenant A:
- Created /tmp/m1_tenant_a_private.txt.
- Printed EXECUTION_A_FILE_CREATED.
- Remained active for 45 seconds.
- Printed EXECUTION_A_FINISHED.
- Sandbox ID: 3cf876df-bd6a-46e8-a442-5b0b785dbf33
- Exit code: 0
- Timed out: false
- Duration: 45.48 seconds

Tenant B:
- Started approximately 5 seconds after Tenant A.
- Checked for Tenant A's private file.
- Printed ISOLATION_PASS_FOREIGN_FILE_NOT_VISIBLE.
- Sandbox ID: 4b2b6763-60f7-4271-82a0-699f82d3f9ac
- Exit code: 0
- Timed out: false
- Duration: 5.46 seconds

Concurrency evidence:
- Tenant A started at 16:33:12.
- Tenant B started at 16:33:17.
- Tenant B finished at 16:33:24.
- Tenant A finished at 16:33:59.

Result:
Both sandbox executions overlapped.
Tenant B could not access Tenant A's private file.

Conclusion:
Concurrent filesystem isolation between separate
sandbox executions was successfully demonstrated.

Evidence:
- tests/evidence/2026-11-02/m1-isolation-a-concurrent.log
- tests/evidence/2026-11-02/m1-isolation-b-concurrent.log

#### Concurrent isolation cleanup verification — PASSED

Tenant A:
- Sandbox ID: 3cf876df-bd6a-46e8-a442-5b0b785dbf33
- Container kill logged.
- Container removal logged.
- DELETE request returned HTTP 204.
- Cleanup completed at 16:33:59.

Tenant B:
- Sandbox ID: 4b2b6763-60f7-4271-82a0-699f82d3f9ac
- Container kill logged.
- Container removal logged.
- DELETE request returned HTTP 204.
- Cleanup completed at 16:33:24.

Conclusion:
Both sandbox containers were successfully removed
after their executions.

Evidence:
OpenSandbox server logs dated 2026-11-02.


### INT-07: Real execution cleanup after nonzero exit — PASSED

Objective:
Verify that a sandbox is cleaned up when the executed
program exits with a nonzero status.

Execution:
- Fixture: tests/fixtures/m1/exit_7_cleanup.py
- Sandbox ID: 3f13c55a-98a9-41d2-95f8-091e3cbb542f
- Docker image: python:3.11
- Runtime: runsc
- Container status during execution: running
- Expected output captured successfully.
- Program exit code: 7
- Runner exit code: 7
- Timed out: false
- Duration: 8.32 seconds

Cleanup verification:
- Container kill logged by OpenSandbox.
- Container removal logged by OpenSandbox.
- DELETE request returned HTTP 204.
- docker inspect confirmed that the container no longer exists.

Result:
The runner correctly propagated the nonzero exit code,
and the sandbox container was successfully removed.

Evidence:
- tests/evidence/2026-11-02/m1-exit-7-cleanup.log
- OpenSandbox server logs dated 2026-11-02


### INT-08: Real LangGraph + OpenSandbox integration — PASSED

Objective:
Verify that a platform-defined LangGraph workflow can
execute a Python submission through the existing
OpenSandbox execution service.

Workflow:
- Validate submission.
- Execute through runner.run_python_file().
- Classify execution result.

Execution:
- Fixture: tests/fixtures/m1/real_success.py
- Sandbox ID: d802f2e4-55ce-49ad-abed-83e25d4097f6
- Docker image: python:3.11
- Runtime: runsc
- Exit code: 0
- Timed out: false
- Duration: 8.26 seconds
- Workflow status: succeeded

Output:
- M1_REAL_SANDBOX_OK
- 2 + 3 = 5
- M1_REAL_SANDBOX_FINISHED

Cleanup:
- Container removal confirmed.
- Sandbox DELETE request succeeded.

Evidence:
- tests/evidence/2026-11-02/m1-workflow-real-run.log
- OpenSandbox server logs dated 2026-11-02
- Docker runtime inspection confirmed runsc.

Conclusion:
LangGraph successfully orchestrated a real Python
execution through OpenSandbox using the gVisor runtime.


### INT-09: LangGraph execution ID integration — PASSED

Objective:
Verify that a platform-managed execution ID is generated
and returned alongside a real sandbox execution result.

Execution:
- Fixture: tests/fixtures/m1/real_success.py
- Execution ID: 1a73d9cb-6f0a-45ac-bb53-72e62ec2e6f6
- Sandbox ID: 94c08df6-5e3a-4a2a-8b82-0e517fb4cb5e
- Exit code: 0
- Timed out: false
- Duration: 8.24 seconds
- Workflow status: succeeded

Output:
- M1_REAL_SANDBOX_OK
- 2 + 3 = 5
- M1_REAL_SANDBOX_FINISHED

Result:
LangGraph successfully executed the submission
through OpenSandbox and returned the execution ID
alongside the sandbox execution result.

Evidence:
- tests/evidence/2026-11-02/m1-workflow-execution-id.log

Note:
Execution ID is currently maintained in workflow state.
Persistent storage and tenant ownership are not implemented.