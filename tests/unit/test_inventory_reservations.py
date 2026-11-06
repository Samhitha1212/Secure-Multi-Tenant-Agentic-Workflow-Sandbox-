"""Tests for stateful mock inventory reservations."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from secure_agentic_sandbox.tool_broker import (
    IdempotencyConflictError,
    ToolCallContext,
    create_mock_tool_broker,
)


def create_test_broker(quantity: int = 10):
    """Create a broker with reservation and lookup tools."""

    broker = create_mock_tool_broker(
        inventory={
            "SKU-TEST": {
                "quantity": quantity,
            }
        },
        include_reservation=True,
    )

    context = ToolCallContext(
        tenant_id="tenant-a",
        execution_id="execution-a",
    )

    grant = broker.create_grant(
        tenant_id=context.tenant_id,
        execution_id=context.execution_id,
        allowed_tools={
            "inventory.lookup",
            "inventory.reserve",
        },
        max_calls=100,
    )

    return broker, context, grant


def reserve(
    broker,
    context,
    grant,
    *,
    quantity: int = 2,
    idempotency_key: str = "reservation-1",
):
    """Invoke the inventory reservation tool."""

    return broker.invoke(
        context=context,
        grant_token=grant.token,
        tool_name="inventory.reserve",
        arguments={
            "sku": "SKU-TEST",
            "quantity": quantity,
            "idempotency_key": idempotency_key,
        },
    )


def lookup(broker, context, grant):
    """Read the current inventory quantity."""

    return broker.invoke(
        context=context,
        grant_token=grant.token,
        tool_name="inventory.lookup",
        arguments={"sku": "SKU-TEST"},
    )


def test_successful_reservation_decreases_inventory():
    broker, context, grant = create_test_broker(quantity=10)

    result = reserve(
        broker,
        context,
        grant,
        quantity=3,
    )

    assert result["status"] == "reserved"
    assert result["sku"] == "SKU-TEST"
    assert result["quantity"] == 3
    assert result["remaining_quantity"] == 7
    assert result["reservation_id"]

    inventory = lookup(broker, context, grant)

    assert inventory["found"] is True
    assert inventory["quantity"] == 7
    assert inventory["available"] is True


def test_reservation_rejected_when_stock_is_insufficient():
    broker, context, grant = create_test_broker(quantity=2)

    result = reserve(
        broker,
        context,
        grant,
        quantity=3,
    )

    assert result["status"] == "insufficient_stock"
    assert result["requested_quantity"] == 3
    assert result["available_quantity"] == 2

    inventory = lookup(broker, context, grant)

    # Failed reservation must not change inventory.
    assert inventory["quantity"] == 2


def test_same_idempotency_key_returns_original_result():
    broker, context, grant = create_test_broker(quantity=10)

    first_result = reserve(
        broker,
        context,
        grant,
        quantity=2,
        idempotency_key="retry-001",
    )

    second_result = reserve(
        broker,
        context,
        grant,
        quantity=2,
        idempotency_key="retry-001",
    )

    assert first_result == second_result
    assert first_result["status"] == "reserved"

    # Inventory must be decremented only once.
    inventory = lookup(broker, context, grant)

    assert inventory["quantity"] == 8


def test_reusing_idempotency_key_with_different_data_is_rejected():
    broker, context, grant = create_test_broker(quantity=10)

    first_result = reserve(
        broker,
        context,
        grant,
        quantity=2,
        idempotency_key="retry-002",
    )

    assert first_result["status"] == "reserved"

    with pytest.raises(IdempotencyConflictError):
        reserve(
            broker,
            context,
            grant,
            quantity=4,
            idempotency_key="retry-002",
        )

    # Conflicting request must not change the original reservation.
    inventory = lookup(broker, context, grant)

    assert inventory["quantity"] == 8


def test_idempotency_keys_are_scoped_to_execution():
    broker = create_mock_tool_broker(
        inventory={
            "SKU-TEST": {
                "quantity": 10,
            }
        },
        include_reservation=True,
    )

    context_a = ToolCallContext(
        tenant_id="tenant-a",
        execution_id="execution-a",
    )

    context_b = ToolCallContext(
        tenant_id="tenant-a",
        execution_id="execution-b",
    )

    grant_a = broker.create_grant(
        tenant_id=context_a.tenant_id,
        execution_id=context_a.execution_id,
        allowed_tools={"inventory.reserve"},
        max_calls=10,
    )

    grant_b = broker.create_grant(
        tenant_id=context_b.tenant_id,
        execution_id=context_b.execution_id,
        allowed_tools={"inventory.reserve"},
        max_calls=10,
    )

    # Both executions use the same idempotency key and request.
    result_a = reserve(
        broker,
        context_a,
        grant_a,
        quantity=2,
        idempotency_key="shared-key",
    )

    result_b = reserve(
        broker,
        context_b,
        grant_b,
        quantity=2,
        idempotency_key="shared-key",
    )

    assert result_a["status"] == "reserved"
    assert result_b["status"] == "reserved"

    # The second execution must create its own reservation.
    assert result_a["reservation_id"] != result_b["reservation_id"]

    # Both reservations must affect the shared inventory.
    assert result_a["remaining_quantity"] == 8
    assert result_b["remaining_quantity"] == 6


def test_concurrent_duplicate_reservations_decrement_stock_only_once():
    broker, context, grant = create_test_broker(quantity=10)

    def make_reservation(_):
        return reserve(
            broker,
            context,
            grant,
            quantity=3,
            idempotency_key="concurrent-request",
        )

    number_of_requests = 20

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                make_reservation,
                range(number_of_requests),
            )
        )

    # All duplicate requests must receive the same result.
    assert all(result == results[0] for result in results)

    assert results[0]["status"] == "reserved"
    assert results[0]["quantity"] == 3
    assert results[0]["remaining_quantity"] == 7

    # Only one of the concurrent requests may decrement inventory.
    inventory = lookup(broker, context, grant)

    assert inventory["quantity"] == 7