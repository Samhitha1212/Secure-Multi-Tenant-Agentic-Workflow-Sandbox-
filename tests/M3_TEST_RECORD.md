# M3 - Secure Tool Broker Test Record

**Date:** 2026-11-07
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





## Hybrid Inventory Workflow — End-to-End Integration Test

**Date:** 2026-11-09  
**Test type:** Manual integration test using the frontend, FastAPI and OpenSandbox.

**Submission endpoint:**
`POST /questions/inventory-python-fulfillment/submit`

### Workflow execution

| Step | Type | Observed result |
|---|---|---|
| lookup | Trusted tool | SKU-100 found, available quantity = 12 |
| validate_request | Isolated Python | Validated requested quantity = 3 |
| reserve | Trusted tool | Reservation succeeded, remaining quantity = 9 |
| finalize | Isolated Python | Finalization step executed successfully |

### What this demonstrates

- Python workflow steps execute through the OpenSandbox runner.
- Tool operations execute through the trusted Tool Broker.
- Structured outputs are passed between steps using `$ref`.
- Python execution results include stdout, stderr, exit code and structured output.
- The workflow completes successfully across tool and Python steps.

**Result:** PASS

**Evidence:** Capture and retain the frontend execution screenshot alongside this record.
