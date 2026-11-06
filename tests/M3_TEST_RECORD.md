# M3 - Secure Tool Broker Test Record

**Date:** 2026-11-14
**Status:** Core broker implementation and tests passed.

## 1. Objective

Implement a trusted tool broker that allows approved tools to be
invoked by authorized executions while enforcing tenant isolation,
execution scoping, expiry, revocation, quotas, input validation,
and audit logging.

The initial implementation uses a mock inventory service.

## 2. Implemented Features

- Trusted tool registration and duplicate registration prevention.
- Tool input validation.
- Tenant-scoped and execution-scoped grant authorization.
- Cryptographically generated grant tokens.
- Grant token hashes stored instead of raw tokens.
- Grant expiration and revocation.
- Per-grant allowed-tool restrictions.
- Maximum tool-call quotas.
- Atomic quota enforcement under concurrent calls.
- Audit records for successful and rejected operations.
- Audit records exclude grant tokens and raw tool arguments.
- Mock inventory lookup.
- Mock inventory reservation with stock updates.
- Idempotency support for reservation requests.
- Detection of conflicting idempotency-key reuse.
- Thread-safe inventory reservation handling.

## 3. Existing Broker Tests

**File:** `tests/unit/test_tool_broker.py`

The tests cover:

- Tool registration and duplicate registration.
- Unknown tool and invalid grant rejection.
- Cross-tenant and cross-execution access prevention.
- Tool scope enforcement.
- Grant expiration and revocation.
- Unauthorized grant revocation.
- Call quota enforcement.
- Concurrent quota enforcement.
- Invalid tool arguments.
- Audit logging and prevention of sensitive data leakage.

## 4. Inventory Reservation Tests

**File:** `tests/unit/test_inventory_reservations.py`

| Test case | What it verifies |
|---|---|
| Successful reservation | Stock decreases by the requested quantity. |
| Insufficient stock | Reservation is rejected without changing stock. |
| Idempotent retry | Repeating a request with the same key returns the original result without another stock update. |
| Conflicting key reuse | Reusing a key with different request data is rejected. |
| Execution-scoped idempotency | Different executions can use the same key independently. |
| Concurrent duplicate requests | Concurrent retries produce only one inventory update. |

## 5. Validation Commands

```bash
uv run ruff check .
uv run pytest -q
git diff --check


6. Final Results
- Ruff: All checks passed.
- Full test suite: 85 passed.
- Runtime: 2.44 seconds.
- Git whitespace and patch validation: Passed.
7. Current Limitations and Deferred Work
- Inventory and idempotency state are currently stored in memory.
  They do not survive broker process restarts.
- The inventory service is a synthetic mock service, not a real
  external business API.
- Persistent storage and durable idempotency records are future work.
- OpenSandbox Credential Vault integration is deferred because the
  current gVisor and egress networking configuration is not compatible
  with the required credential proxy setup.
- Integration of the broker with the trusted LangGraph workflow
  controller is a subsequent implementation step.
8. Conclusion
The core M3 Secure Tool Broker implementation has passed its
unit tests and the complete project regression suite.

The implementation demonstrates execution-scoped authorization,
controlled tool invocation, inventory reservation, idempotency,
concurrency safety, and audit logging.