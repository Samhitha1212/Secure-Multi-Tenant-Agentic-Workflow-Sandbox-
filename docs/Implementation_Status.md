# Implementation Status

**Project:** Secure Multi-Tenant Agentic Workflow Sandbox with Stateful Execution and Hybrid Automated Grading  
**Status date:** 2026-11-21  
**Project group:** 18 (individual project)

## 1. Overall Status

The project currently provides a working prototype for submitting Python programs and declarative workflows, executing them in isolated OpenSandbox environments using gVisor, controlling access to mock tools, and persisting workflow progress for pause, resume, and recovery.

The implementation is strongest in sandbox execution, resource controls, tool authorization, and workflow checkpointing. A frontend and demo account flow are also available.

The original project scope included hybrid automated grading. However, the grading subsystem has not been implemented in the current prototype. Queue-based admission and scheduling are also deferred. These are documented as future work rather than completed functionality.

## 2. Implemented Functionality

### 2.1 Isolated Python Execution

**Status: Implemented and tested**

- Executes submitted Python code through OpenSandbox.
- Uses Docker with gVisor (`runsc`) as the secure runtime.
- Applies execution time limits.
- Captures standard output and standard error.
- Reports execution results, including non-zero exits and timeouts.
- Cleans up sandbox containers after execution.
- Supports separate execution environments for concurrent submissions.

**Validation evidence:**
- M1 test record: `tests/M1_TEST_RECORD.md`
- M1 evidence: `tests/evidence/2026-10-29/` and `tests/evidence/2026-11-02/`
- Relevant tests: `tests/unit/test_runner.py`, `tests/unit/test_runner_execution.py`, `tests/unit/test_runner_output.py`

### 2.2 Resource and Network Restrictions

**Status: Implemented and tested for documented cases**

- Configurable CPU and memory limits.
- Process-count limits.
- Bounded stdout and stderr collection.
- CPU quota enforcement.
- Memory and swap limits.
- OOM-kill detection and reporting.
- Internal Docker network configuration for sandbox workloads.
- Restrictions tested against selected public-network, host-network, DNS, and inter-container access paths.

**Validation evidence:**
- M2 test record: `tests/M2_TEST_RECORD.md`
- M2 evidence: `tests/evidence/2026-11-03/` and `tests/evidence/2026-11-04/`

**Limitations:**
- Network tests cover the specific paths documented in the M2 test record; they do not prove that every possible network-bypass technique is blocked.
- DNS-over-TCP, DNS-over-HTTPS, and DNS-over-TLS were not comprehensively tested.
- The host firewall rule installed by the development startup script is not persistent across host reboot and is not automatically removed when the script is stopped.
- The configured OpenSandbox network policy option was not supported by the current custom Docker-network and gVisor setup; isolation relies on the documented Docker network and host firewall configuration.

### 2.3 Trusted Tool Broker

**Status: Implemented and unit-tested**

- Registers trusted tools and rejects duplicate registrations.
- Validates tool inputs.
- Authorizes tool calls using tenant-scoped and execution-scoped grants.
- Uses generated grant tokens and stores token hashes rather than raw tokens.
- Enforces grant expiry, revocation, allowed-tool scopes, and call quotas.
- Enforces quotas under concurrent requests.
- Records successful and rejected operations in audit logs.
- Avoids storing grant tokens and raw tool arguments in audit records.
- Provides mock inventory lookup and reservation operations.
- Supports reservation idempotency and rejects conflicting reuse of an idempotency key.
- Supports SQLite-backed inventory state and durable idempotency receipts in the default API configuration.

**Validation evidence:**
- M3 test record: `tests/M3_TEST_RECORD.md`
- Broker tests: `tests/unit/test_tool_broker.py`
- Reservation tests: `tests/unit/test_inventory_reservations.py`

The M3 record also documents a manual end-to-end inventory workflow test dated 2026-11-09. A frontend screenshot for that test has not yet been captured.

### 2.4 Stateful Workflow Execution

**Status: Implemented and tested**

- Supports declarative workflows containing isolated Python steps and trusted tool steps.
- Passes structured outputs between workflow steps using references.
- Persists workflow progress in SQLite.
- Stores tenant and execution ownership, workflow version information, a specification hash, completed step results, and the next-step cursor.
- Validates checkpoint data and rejects stale progress updates.
- Advances the checkpoint only after successful step completion.
- Preserves successful earlier steps if a later step fails.
- Supports tenant-scoped pause requests.
- Pauses at a safe step boundary, allowing an in-progress step to finish before saving its result.
- Resumes from the first unfinished step without replaying completed checkpointed steps.
- Creates a fresh sandbox for resumed Python execution.
- Recovers checkpoints left in the running state to a paused state when the API starts again.
- Uses durable idempotency for the default SQLite-backed mock inventory service.

**Validation evidence:**
- M4 test record: `tests/M4_TEST_RECORD.md`
- Checkpoint tests: `tests/unit/test_checkpoint_store.py`
- Workflow tests: `tests/unit/test_workflow_service.py`
- Live OpenSandbox test: `tests/integration/m4_live_pause_resume.py`
- API crash-recovery test: `tests/integration/m4_api_crash_recovery.py`
- Live integration logs: `tests/evidence/2026-11-15/`

**Limitations:**
- The live pause/resume integration test exercises the workflow service and checkpoint store directly; it is not a complete browser-to-HTTP pause/resume demonstration.
- The API crash-recovery integration test uses a real API subprocess and HTTP resume, but manually removes the orphaned sandbox container after killing the API process. Automatic cleanup of orphaned containers after an abrupt API crash is therefore not demonstrated.
- Exactly-once side effects cannot be guaranteed for arbitrary external tools. Durable idempotency is provided by the default mock inventory service; custom or external tools must provide their own persistence and idempotency guarantees.
- The runtime lock is designed for the Linux environment and a single API process/worker per checkpoint database.

### 2.5 Frontend and API

**Status: Implemented; manually exercised**

- Provides a web interface for selecting demo accounts and viewing available exercises.
- Supports Python and workflow submissions.
- Displays execution output, errors, structured results, workflow step results, tools used, and execution identifiers.
- Provides pause and resume controls for workflows.
- Exposes API endpoints for health checks, questions, submissions, workflow execution, pause/resume, and demo authentication.
- Uses a Vite development proxy to communicate with the local FastAPI backend.

**Authentication limitation:**

The current demo login is an account-selection mechanism using predefined demo users. It is not production identity verification: there are no real passwords or external identity-provider checks. Session tokens are generated and stored as hashes, and logout revokes the session. The demo authentication mode must be explicitly enabled for the local demonstration.

**Manual verification:**
- Demo account selection and frontend loading.
- Python and workflow execution through the website.
- Inventory reservation permission behavior for different demo users.
- Workflow pause/resume interaction.

The frontend screenshot set is pending and will be captured together from the final working website.

## 3. Features Not Implemented or Deferred

The following items belong to the broader original project scope but are not part of the current working prototype:

| Feature | Status | Notes |
|---|---|---|
| Queue-based admission and scheduling | Deferred | No complete queue/admission subsystem is implemented. |
| Hybrid automated grading | Not implemented | No deterministic rubric grader, semantic grader, or human-escalation pipeline is currently delivered. |
| Production authentication | Not implemented | Current login is for local demonstration only. |
| Automatic orphan-container cleanup after abrupt API failure | Not demonstrated | Crash-recovery test performs manual cleanup. |
| General-purpose external tool integrations | Deferred | Current tool implementation uses a mock inventory service. |
| Production multi-host deployment | Not implemented | Prototype is designed and tested on one machine. |

These items are treated as limitations and future work. They must not be presented as completed features in the report or presentation.

## 4. Current Validation Snapshot

The latest recorded full-project validation completed successfully:

| Validation | Result |
|---|---|
| Python lint (`ruff check .`) | PASS |
| Python test suite (`python -m pytest -q`) | 230 passed |
| Frontend lint (`npm run lint`) | PASS |
| Frontend production build (`npm run build`) | PASS |
| Git whitespace check (`git diff --check`) | PASS for the current documentation edits |

The M1–M4 test records contain milestone-specific validation results and evidence. The 230-test result is the latest consolidated Python test snapshot; earlier milestone test counts in M1–M4 records are historical results from those milestones.

## 5. Reproducibility and Demonstration

The prototype is intended to run locally on a Linux machine with Docker, gVisor, OpenSandbox, Python, and Node.js installed.

The repository includes:
- Python backend and sandbox execution code.
- Frontend source code.
- Development startup script.
- Unit and integration tests.
- M1–M4 test records.
- Execution evidence logs.

The final report and README should provide the exact setup steps, environment configuration, startup commands, test commands, and cleanup instructions needed to reproduce the demonstration.

## 6. Submission Evidence Still to Prepare

- Capture a consistent set of screenshots from the final working frontend.
- Record the final end-to-end demonstration.
- Prepare the consolidated Architecture Decision Records document.
- Complete the project report and final presentation.
- Review the README and handover instructions against the final repository state.
- Create a final release tag after the final revision is validated.

