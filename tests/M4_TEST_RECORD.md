# M4.1 - Workflow Checkpoint Storage Test Record

**Date:** 2026-11-10
**Status:** Checkpoint storage implementation and tests passed.

## 1. Objective

Implement the trusted storage foundation for workflow checkpointing.

The checkpoint store must persist validated workflow state so that a workflow's progress can be recovered later. Checkpoints are scoped by tenant and execution, and workflow specifications are versioned and hashed to detect changes.

## 2. Implemented Features

- SQLite-based workflow checkpoint storage.
- Tenant and execution ownership using a composite key.
- Checkpoint schema versioning.
- Workflow version and specification storage.
- SHA-256 hash of the workflow specification.
- Storage of the next step index and completed step results.
- Checkpoint status tracking: `running`, `paused`, `completed`, and `failed`.
- JSON validation of workflow specifications and step results.
- Rejection of unsupported JSON values.
- Duplicate checkpoint creation prevention.
- Progress updates with expected-step-index validation to reject stale updates.
- Checkpoint retrieval and persistence across store restarts.

## 3. Checkpoint Store Tests

**File:** `tests/unit/test_checkpoint_store.py`

| Test case | What it verifies |
|---|---|
| Create and load checkpoint | A checkpoint can be created and retrieved with its stored fields. |
| Persistence across store restart | Checkpoint data remains available after closing and reopening the store. |
| Tenant isolation | A checkpoint belonging to one tenant cannot be retrieved using another tenant's identity. |
| Duplicate checkpoint rejection | Creating another checkpoint with the same tenant and execution identity is rejected. |
| Progress update | The next step index, results and status can be updated. |
| Stale progress rejection | An update with an outdated expected step index is rejected. |
| Invalid workflow version | A workflow version inconsistent with its specification is rejected. |
| Non-JSON value rejection | Values that cannot be represented as valid JSON are rejected. |
| Invalid step results | Step results that do not have the required JSON-object structure are rejected. |
| Specification hash | The specification hash is stored consistently. |

## 4. Validation Results

| Validation | Result |
|---|---|
| Ruff checks (`uv run ruff check .`) | PASS |
| Checkpoint store tests | 10 passed |
| Full test suite (`uv run pytest -q`) | 175 passed |
| `git diff --check` | PASS |

## 5. Scope at the M4.1 Milestone (Historical)

M4.1 implements the checkpoint storage layer and validates its behavior independently.

At the M4.1 milestone, workflow execution did not yet automatically save checkpoints after each step. Pause, cleanup, resume and crash recovery were implemented and tested in subsequent M4 stages.

**Result:** PASS

---

# M4.2 - Checkpoint Integration Test Record

**Date:** 2026-11-12
**Status:** Workflow checkpoint integration and regression tests passed.

## 1. Objective

Integrate the SQLite checkpoint store with declarative workflow execution so that progress is persisted after each successfully completed workflow step.

## 2. Implemented Features

- Optional trusted progress callback in the declarative workflow graph.
- Initial checkpoint creation before workflow execution.
- Progress persistence after each successful tool step.
- Progress persistence after each successful isolated Python step.
- Atomic optimistic progress updates using the expected next-step index.
- Failed Python steps do not advance the checkpoint cursor.
- Previously completed steps remain persisted when a later step raises an exception.
- Failed executions are marked as failed in the checkpoint store.
- Temporary sandbox IDs are excluded from persisted step results.
- WorkflowExecutionService accepts an injected CheckpointStore.
- API integration with a persistent SQLite database.
- Configurable database path through `SECURE_SANDBOX_CHECKPOINT_DB`.
- API shutdown closes the checkpoint-store connection.
- API tests use isolated temporary databases.

## 3. Integration Tests

**File:** `tests/unit/test_workflow_service.py`

The tests cover:

- Successful Python workflow checkpoint persistence.
- Progress updates after multiple successful tool steps.
- Failed Python step cursor preservation.
- Preservation of successful earlier steps when a later Python execution raises an exception.
- Exclusion of `sandbox_id` from stored results.

**File:** `tests/unit/test_question_api.py`

- Workflow submission creates a checkpoint accessible through the tenant and execution identity.

## 4. Validation Results

| Validation | Result |
|---|---|
| Ruff checks (`uv run ruff check src/secure_agentic_sandbox tests/unit`) | PASS |
| Workflow service tests | 9 passed |
| Full test suite (`uv run pytest -q`) | 180 passed |
| `git diff --check` | PASS |
| `git diff --cached --check` | PASS |

## 5. Scope at the M4.2 Milestone (Historical)

M4.2 established automatic persistence of completed workflow steps. Pause, resume and crash recovery were addressed in subsequent M4 stages.

**Result:** PASS
---

# M4.3 - Safe Pause and Execution Cleanup Test Record

**Date:** 2026-11-12
**Status:** Pause-request, safe-boundary and API tests passed.

## 1. Objective

Allow an authenticated tenant to request that a running workflow pause safely,
without discarding the result of a step that is already executing.

## 2. Implemented Features

- Persisted `pause_requested` flag in the SQLite checkpoint.
- Database migration for checkpoint databases created before pause support.
- Tenant- and execution-scoped pause requests.
- Authenticated `POST /executions/{execution_id}/pause` endpoint.
- Pause checks before starting each workflow step.
- If a pause arrives during a step, the step is allowed to finish and its result
  is persisted before the checkpoint transitions to `paused`.
- The workflow stops before executing the next step.
- Pause requests for missing or non-running checkpoints are rejected.
- Python sandbox destruction is performed in the runner's cleanup path.

## 3. Tests and Evidence

**Files:** `tests/unit/test_checkpoint_store.py`,
`tests/unit/test_workflow_service.py`, and
`tests/unit/test_question_api.py`.

Coverage includes:

- Pause request persistence across checkpoint-store recreation.
- Transition from `running` to `paused`.
- Persistence of a completed step before acknowledging a pending pause.
- Tenant isolation for pause requests.
- Authenticated pause endpoint behavior.
- Rejection of unauthenticated, cross-tenant and invalid-state pause requests.
- A pause arriving during Python execution stops the workflow before its next step.

The workflow-service pause test uses a controlled runner to exercise the
step-boundary behavior. It is not a live OpenSandbox process-kill test.

## 4. Result

**Result:** PASS

---

# M4.4 - Workflow Resume Test Record

**Date:** 2026-11-12
**Status:** Checkpoint resume and workflow-service tests passed.

## 1. Objective

Resume a paused workflow from its saved progress without replaying steps that
were already successfully checkpointed.

## 2. Implemented Features

- Authenticated `POST /executions/{execution_id}/resume` endpoint.
- Tenant-scoped checkpoint retrieval.
- Atomic claim of a paused checkpoint for resumption.
- Rejection of repeated or competing resume claims.
- Restoration of saved step results and the next-step cursor.
- Declarative workflow graph starts at the first unfinished step.
- A new broker grant is created for the resumed execution and revoked when
  execution finishes.
- Python steps run through the sandbox runner when resumed; sandbox identifiers
  are not persisted in checkpoint results.

## 3. Tests and Evidence

**Files:** `tests/unit/test_checkpoint_store.py`,
`tests/unit/test_declarative_python_step.py`,
`tests/unit/test_workflow_service.py`, and
`tests/unit/test_question_api.py`.

Coverage includes:

- Resume claim preserves saved progress.
- A running checkpoint cannot be claimed again.
- Cross-tenant resume is rejected.
- Resume endpoint requires authentication and enforces tenant ownership.
- Saved results are supplied to the remaining workflow steps.
- Previously completed steps are not executed again.
- Pause followed by resume continues from the saved cursor.
- The controlled runner test verifies a distinct sandbox ID for the resumed
  Python execution.

The fresh-sandbox behavior is verified with a mocked runner in the unit tests;
a live Docker/gVisor restart-and-resume test remains an integration-test item.

## 4. Result

**Result:** PASS

---

# M4.5 - Crash Recovery and Durable Tool Idempotency Test Record

**Date:** 2026-11-12
**Status:** Startup recovery, durable mock reservation and regression tests passed.

## 1. Objective

Recover workflow checkpoints left running after an application restart and
prevent a replayed mock inventory reservation from applying the same stock
change twice.

## 2. Implemented Features

### Interrupted execution recovery

- API startup acquires an exclusive OS-level runtime lock associated with the
  checkpoint database.
- A second API process attempting to use the same database is rejected.
- Startup recovery atomically changes checkpoints left in `running` state to
  `paused`.
- Pending pause flags are cleared during recovery.
- The saved next-step index and completed step results are preserved.
- The runtime lock is released during application shutdown and is also released
  by the operating system when the process exits.

The current runtime-lock implementation uses `fcntl` and targets the Linux
deployment environment. The API is intended to run as a single process/worker
per checkpoint database.

### Durable mock inventory idempotency

- `MockInventoryService` supports an optional SQLite-backed mode.
- Inventory quantities are stored in `mock_inventory_items`.
- Idempotency receipts are stored in `mock_inventory_idempotency`.
- Receipts are scoped by tenant, execution and idempotency key.
- A request fingerprint rejects reuse of a key with different request data.
- The stock update and idempotency receipt are committed in the same SQLite
  transaction.
- A repeated request returns its original result without decrementing stock
  again.
- The API's default mock broker uses the same SQLite database file as workflow
  checkpoints.
- Existing callers can continue to use the in-memory mock inventory mode.

## 3. Tests and Evidence

**Files:** `tests/unit/test_checkpoint_store.py`,
`tests/unit/test_inventory_reservations.py`, and `tests/unit/test_api.py`.

Coverage includes:

- Recovery converts interrupted `running` checkpoints to `paused`.
- Recovery preserves the next-step cursor and saved step results.
- Completed and already-paused checkpoints are not changed.
- Runtime lock prevents another store instance from acquiring ownership while
  the first instance holds the lock.
- A reservation retry through a newly created broker returns the original
  reservation result.
- Reusing a durable idempotency key with different input is rejected.
- API restart recovery followed by `/resume` replays the tool step using its
  original idempotency key.
- The integration test verifies that the reservation ID remains unchanged and
  inventory quantity is decremented only once.

The restart tests recreate application/broker instances against the same
SQLite file. They simulate the crash window by performing the tool side effect
without recording step progress before recreating the application; they do not
send an actual SIGKILL to a running server process.

## 4. Scope and Limitations

- Durable idempotency is implemented for the default SQLite-backed mock
  inventory service.
- A custom injected broker must provide its own persistence and idempotency
  guarantees.
- Arbitrary external tools must implement durable idempotency themselves.
  The platform cannot guarantee exactly-once side effects for an external
  service that does not support idempotent requests.
- A process-kill integration test and live OpenSandbox/gVisor pause-resume
  validation remain candidates for M4.6.

## 5. Result

**Result:** PASS

---

# M4 - Consolidated Regression Snapshot

**Date:** 2026-11-12

| Validation | Result |
|---|---|
| Ruff (`uv run ruff check src/secure_agentic_sandbox tests/unit`) | PASS |
| Full test suite (`uv run pytest -q`) | 205 passed |
| `git diff --check` | PASS |
| `git diff --cached --check` | PASS |

The M4.1–M4.5 implementation and unit/API regression tests pass at this
snapshot. M4.6 integration and acceptance-test review remains separate.

---

# M4.6 - Live OpenSandbox Pause and Resume Integration Test

**Date:** 2026-11-12
**Status:** PASSED using real OpenSandbox and gVisor containers.

## 1. Objective

Verify that a declarative workflow can pause after a running Python step,
persist its progress, and resume the remaining steps in a newly created
OpenSandbox sandbox.

## 2. Test Environment

- OpenSandbox server: http://127.0.0.1:8080
- OpenSandbox health endpoint: HTTP 200
- Execution image: python:3.11
- Docker runtime: runsc (gVisor)
- OpenSandbox secure runtime configuration: gvisor
- Test script: tests/integration/m4_live_pause_resume.py

## 3. Execution

The workflow contained two Python steps.

First step:
- Printed M46_FIRST_STEP_STARTED.
- Ran for approximately 8 seconds.
- Printed M46_FIRST_STEP_FINISHED.
- A pause was requested while this step was running.

Second step:
- Was not executed before the pause.
- Ran after the workflow was resumed.
- Printed M46_SECOND_STEP_STARTED.
- Ran for approximately 4 seconds.
- Printed M46_SECOND_STEP_FINISHED.

## 4. Observed Results

| Check | Observed result |
|---|---|
| First workflow status | paused |
| Checkpoint cursor after pause | 1 |
| First step result persisted | Yes |
| Second step absent before resume | Yes |
| First sandbox ID | 86f0cd0b-1f02-456c-bc6d-819cc48c2078 |
| First Docker container | fe129a1bff38 |
| First container runtime | runsc |
| First container cleanup | Confirmed |
| Resume workflow status | succeeded |
| Second sandbox ID | 0ddd0504-6221-4e4e-9068-2064c853ab5f |
| Second Docker container | ccda2e9c826e |
| Second container runtime | runsc |
| Second container cleanup | Confirmed |
| Final checkpoint status | completed |
| Final checkpoint cursor | 2 |

The first and second sandbox IDs were different. Both Docker containers
were inspected while running and reported the runsc runtime.

## 5. Proxy Observation

Both sandbox startups initially encountered an HTTP 502 response from the
OpenSandbox proxy ping endpoint. The SDK retried the requests, and both
executions completed successfully.

This is consistent with the transient proxy readiness behavior observed
during earlier M1 tests. The warning is retained rather than treated as
a successful first-attempt connection.

## 6. Evidence

- tests/integration/m4_live_pause_resume.py
- tests/evidence/2026-11-15/m4-live-pause-resume.log
- tests/evidence/2026-11-21/m4-live-pause-resume.log (latest validation)

## 7. Conclusion

PASS.

A real two-step Python workflow was paused safely after its first step.
The checkpoint preserved the completed step and cursor. Resumption executed
the remaining step in a new OpenSandbox container. Both containers used
gVisor through runsc and were removed after execution.


# M4.7 - Live API Process Crash and Recovery Integration Test

**Date:** 2026-11-21
**Status:** PASSED using a real API process restart and persistent SQLite checkpoint storage.

## 1. Objective

Verify that workflow progress can be recovered when the API process is forcibly terminated while a Python workflow step is executing.

The test checks that the checkpoint survives the process crash, the interrupted execution can be resumed after restarting the API, and previously completed workflow steps are not replayed.

## 2. Test Environment

- API: FastAPI application
- Checkpoint storage: persistent SQLite database
- Sandbox runtime: OpenSandbox with Docker and gVisor (`runsc`)
- Test script: `tests/integration/m4_api_crash_recovery.py`

## 3. Execution

1. Started a two-step Python workflow through the API.
2. Allowed the first step to complete and verified that its progress was checkpointed.
3. Waited until the second Python step was running in its sandbox.
4. Forcefully terminated the API process using SIGKILL.
5. Confirmed that the original HTTP request was disconnected.
6. Manually removed the orphaned sandbox container left by the terminated API process.
7. Restarted the API using the same SQLite checkpoint database.
8. Verified that startup recovery changed the interrupted checkpoint to `paused`, preserving cursor 1.
9. Resumed the workflow through the API.
10. Verified that the workflow completed without replaying the first step.

## 4. Observed Results

| Check | Observed result |
|---|---|
| API process termination | SIGKILL |
| Original HTTP request | Disconnected |
| Checkpoint database | Reused after restart |
| Recovered checkpoint status | paused |
| Recovered checkpoint cursor | 1 |
| Previously completed first step | Not replayed |
| Resume API response | HTTP 200 |
| Second workflow step | Executed successfully |
| Final workflow status | succeeded |
| Final checkpoint status | completed |
| Final checkpoint cursor | 2 |

## 5. Scope and Limitations

- This test verifies checkpoint recovery after an actual API process termination and restart.
- The orphaned sandbox container was removed manually during the test. Automatic orphan-container cleanup after an abrupt API crash is not implemented or demonstrated.
- Recovery is based on persisted application-level workflow checkpoints; it does not restore the interrupted Python process itself.
- Exactly-once side effects for arbitrary external tools are not guaranteed. The default SQLite-backed mock inventory service provides its own durable idempotency mechanism.

## 6. Evidence

- `tests/integration/m4_api_crash_recovery.py`
- `tests/evidence/2026-11-21/m4-api-crash-recovery.log`

## 7. Conclusion

**Result: PASS**

The API was forcibly terminated while a Python step was running. After restarting against the same SQLite database, the application recovered the checkpoint in a paused state and successfully resumed the workflow. The previously completed step was not replayed, and the workflow reached its completed state.



---

# Final Project Validation Snapshot

**Date:** 2026-11-21

| Validation | Result |
|---|---|
| Full Python test suite (`uv run pytest -v`) | 230 passed |
| Python lint (`uv run ruff check .`) | PASS |
| Frontend lint (`cd frontend && npm run lint`) | PASS |
| Frontend production build (`cd frontend && npm run build`) | PASS |
| M4 live pause/resume integration | PASS |
| M4 API crash-recovery integration | PASS |
| `git diff --check` | PASS |
| `git diff --cached --check` | PASS |

The two M4 live integration tests are standalone scripts and are not included in the 230 pytest tests.

Evidence:
- `tests/evidence/2026-11-21/m4-live-pause-resume.log`
- `tests/evidence/2026-11-21/m4-api-crash-recovery.log`