"""Security tests for the trusted tool broker."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from secure_agentic_sandbox.tool_broker import (
    GrantCallLimitError,
    GrantExpiredError,
    GrantRevokedError,
    GrantScopeError,
    InvalidGrantError,
    ToolBroker,
    ToolCallContext,
    ToolInputError,
    ToolNotFoundError,
    ToolRegistrationError,
    create_mock_tool_broker,
    inventory_lookup,
    validate_inventory_lookup,
)


@pytest.fixture
def broker():
    return create_mock_tool_broker()


@pytest.fixture
def context():
    return ToolCallContext(
        tenant_id="tenant-a",
        execution_id="execution-a",
    )


@pytest.fixture
def grant(broker, context):
    return broker.create_grant(
        tenant_id=context.tenant_id,
        execution_id=context.execution_id,
        allowed_tools=["inventory.lookup"],
        ttl_seconds=300,
        max_calls=10,
    )


def invoke(broker, context, grant, arguments=None):
    return broker.invoke(
        context=context,
        grant_token=grant.token,
        tool_name="inventory.lookup",
        arguments=arguments or {"sku": "SKU-100"},
    )


def test_inventory_tool_is_registered(broker):
    assert broker.registered_tools == ("inventory.lookup",)


def test_inventory_lookup_success(broker, context, grant):
    result = invoke(broker, context, grant)

    assert result == {
        "sku": "SKU-100",
        "found": True,
        "available": True,
        "quantity": 12,
    }


def test_unknown_tool_is_rejected(broker, context, grant):
    with pytest.raises(ToolNotFoundError):
        broker.invoke(
            context=context,
            grant_token=grant.token,
            tool_name="payments.transfer",
            arguments={},
        )


def test_unregistered_tool_cannot_receive_grant(broker):
    with pytest.raises(ToolNotFoundError):
        broker.create_grant(
            tenant_id="tenant-a",
            execution_id="execution-a",
            allowed_tools=["payments.transfer"],
        )


def test_duplicate_tool_registration_is_rejected(broker):
    with pytest.raises(ToolRegistrationError):
        broker.register_tool(
            "inventory.lookup",
            inventory_lookup,
            validate_inventory_lookup,
        )


def test_cross_execution_grant_reuse_is_rejected(
    broker,
    grant,
):
    other_context = ToolCallContext(
        tenant_id="tenant-a",
        execution_id="execution-b",
    )

    with pytest.raises(GrantScopeError):
        invoke(broker, other_context, grant)


def test_cross_tenant_grant_reuse_is_rejected(
    broker,
    grant,
):
    other_context = ToolCallContext(
        tenant_id="tenant-b",
        execution_id="execution-a",
    )

    with pytest.raises(GrantScopeError):
        invoke(broker, other_context, grant)


def test_tool_outside_grant_scope_is_rejected(
    broker,
    context,
    grant,
):
    broker.register_tool(
        "inventory.reserve",
        lambda arguments: {"reserved": True},
        lambda arguments: None,
    )

    with pytest.raises(GrantScopeError):
        broker.invoke(
            context=context,
            grant_token=grant.token,
            tool_name="inventory.reserve",
            arguments={},
        )


def test_invalid_grant_token_is_rejected(
    broker,
    context,
):
    with pytest.raises(InvalidGrantError):
        broker.invoke(
            context=context,
            grant_token="invalid-token",
            tool_name="inventory.lookup",
            arguments={"sku": "SKU-100"},
        )


def test_expired_grant_is_rejected():
    current_time = [100.0]

    broker = ToolBroker(clock=lambda: current_time[0])
    broker.register_tool(
        "inventory.lookup",
        inventory_lookup,
        validate_inventory_lookup,
    )

    context = ToolCallContext(
        tenant_id="tenant-a",
        execution_id="execution-a",
    )

    grant = broker.create_grant(
        tenant_id=context.tenant_id,
        execution_id=context.execution_id,
        allowed_tools=["inventory.lookup"],
        ttl_seconds=5,
    )

    current_time[0] = 105.0

    with pytest.raises(GrantExpiredError):
        invoke(broker, context, grant)


def test_revoked_grant_is_rejected(
    broker,
    context,
    grant,
):
    broker.revoke_grant(grant.grant_id, context=context)

    with pytest.raises(GrantRevokedError):
        invoke(broker, context, grant)


def test_another_execution_cannot_revoke_grant(
    broker,
    context,
    grant,
):
    other_context = ToolCallContext(
        tenant_id="tenant-a",
        execution_id="execution-b",
    )

    with pytest.raises(GrantScopeError):
        broker.revoke_grant(
            grant.grant_id,
            context=other_context,
        )

    # The original grant must remain usable.
    result = invoke(broker, context, grant)
    assert result["found"] is True


def test_grant_call_limit_is_enforced(
    broker,
    context,
):
    grant = broker.create_grant(
        tenant_id=context.tenant_id,
        execution_id=context.execution_id,
        allowed_tools=["inventory.lookup"],
        max_calls=2,
    )

    invoke(broker, context, grant)
    invoke(broker, context, grant)

    with pytest.raises(GrantCallLimitError):
        invoke(broker, context, grant)


def test_call_limit_is_atomic_under_concurrency(
    broker,
    context,
):
    call_limit = 5

    grant = broker.create_grant(
        tenant_id=context.tenant_id,
        execution_id=context.execution_id,
        allowed_tools=["inventory.lookup"],
        max_calls=call_limit,
    )

    def make_call():
        try:
            invoke(broker, context, grant)
            return "success"
        except GrantCallLimitError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = list(executor.map(
            lambda _: make_call(),
            range(20),
        ))

    assert outcomes.count("success") == call_limit
    assert outcomes.count("rejected") == 20 - call_limit


def test_invalid_tool_arguments_are_rejected(
    broker,
    context,
    grant,
):
    with pytest.raises(ToolInputError):
        invoke(
            broker,
            context,
            grant,
            arguments={"sku": "SKU-100", "unexpected": True},
        )


def test_grant_token_is_not_exposed_in_audit_log(
    broker,
    context,
    grant,
):
    invoke(broker, context, grant)

    audit_text = repr(broker.audit_log)

    assert grant.token not in audit_text


def test_raw_arguments_are_not_written_to_audit_log(
    broker,
    context,
    grant,
):
    dummy_secret = "dummy-secret-value-123"

    with pytest.raises(ToolInputError):
        invoke(
            broker,
            context,
            grant,
            arguments={
                "sku": "SKU-100",
                "api_key": dummy_secret,
            },
        )

    audit_text = repr(broker.audit_log)

    assert dummy_secret not in audit_text


def test_audit_log_records_rejected_authorization(
    broker,
    grant,
):
    other_context = ToolCallContext(
        tenant_id="tenant-b",
        execution_id="execution-b",
    )

    with pytest.raises(GrantScopeError):
        invoke(broker, other_context, grant)

    assert broker.audit_log[-1].event == "tool.invoke"
    assert broker.audit_log[-1].outcome == "rejected"
    assert broker.audit_log[-1].reason == "GrantScopeError"