"""Trusted tool broker with execution-scoped authorization."""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

MAX_GRANT_TTL_SECONDS = 3600
MAX_GRANT_CALLS = 1000

IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
TOOL_NAME_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
SKU_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")

ToolHandler = Callable[
    [Mapping[str, object], "ToolCallContext"],
    Mapping[str, object],
]
ToolValidator = Callable[[Mapping[str, object]], None]


class ToolBrokerError(Exception):
    """Base exception for tool broker errors."""


class ToolRegistrationError(ToolBrokerError):
    """Raised when a tool cannot be registered."""


class ToolNotFoundError(ToolBrokerError):
    """Raised when a tool is not registered."""


class ToolInputError(ToolBrokerError):
    """Raised when tool arguments are invalid."""


class ToolAuthorizationError(ToolBrokerError):
    """Raised when a tool invocation is unauthorized."""


class InvalidGrantError(ToolAuthorizationError):
    """Raised when a grant token is unknown or invalid."""


class GrantExpiredError(ToolAuthorizationError):
    """Raised when a grant has expired."""


class GrantRevokedError(ToolAuthorizationError):
    """Raised when a grant has been revoked."""


class GrantScopeError(ToolAuthorizationError):
    """Raised when a grant belongs to another tenant or execution."""


class GrantCallLimitError(ToolAuthorizationError):
    """Raised when a grant has exhausted its call quota."""


class ToolHandlerError(ToolBrokerError):
    """Raised when a trusted tool returns an invalid result."""


class IdempotencyConflictError(ToolBrokerError):
    """Raised when an idempotency key is reused with different input."""


def validate_identifier(value: str, field_name: str) -> None:
    """Validate a tenant, execution, or idempotency identifier."""

    if (
        not isinstance(value, str)
        or IDENTIFIER_PATTERN.fullmatch(value) is None
    ):
        raise ValueError(f"Invalid {field_name}.")


@dataclass(frozen=True, slots=True)
class ToolCallContext:
    """Identity supplied by the trusted platform controller.

    Never construct this context from untrusted sandbox request fields.
    """

    tenant_id: str
    execution_id: str

    def __post_init__(self) -> None:
        validate_identifier(self.tenant_id, "tenant ID")
        validate_identifier(self.execution_id, "execution ID")


@dataclass(frozen=True, slots=True)
class GrantCredential:
    """Credential returned once when a grant is created."""

    grant_id: str
    token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """Audit metadata without credentials or raw tool payloads."""

    timestamp: datetime
    event: str
    tenant_id: str | None
    execution_id: str | None
    grant_id: str | None
    tool_name: str | None
    outcome: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """A registered trusted tool and its argument validation."""

    name: str
    handler: ToolHandler
    validator: ToolValidator


@dataclass(slots=True)
class _GrantRecord:
    """Internal grant state. The original token is never stored."""

    grant_id: str
    token_hash: bytes
    tenant_id: str
    execution_id: str
    allowed_tools: frozenset[str]
    expires_at_monotonic: float
    expires_at_utc: datetime
    max_calls: int
    calls_used: int = 0
    revoked: bool = False


class ToolBroker:
    """In-process trusted broker for approved tool invocations."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._clock = clock or time.monotonic
        self._tools: dict[str, ToolDefinition] = {}
        self._grants: dict[str, _GrantRecord] = {}
        self._token_index: dict[bytes, str] = {}
        self._audit_records: list[AuditRecord] = []
        self._lock = threading.Lock()

    @property
    def registered_tools(self) -> tuple[str, ...]:
        """Return registered tool names."""

        with self._lock:
            return tuple(sorted(self._tools))

    @property
    def audit_log(self) -> tuple[AuditRecord, ...]:
        """Return an immutable snapshot of audit records."""

        with self._lock:
            return tuple(self._audit_records)

    def register_tool(
        self,
        name: str,
        handler: ToolHandler,
        validator: ToolValidator,
    ) -> None:
        """Register a trusted tool implementation."""

        if (
            not isinstance(name, str)
            or TOOL_NAME_PATTERN.fullmatch(name) is None
        ):
            raise ToolRegistrationError("Invalid tool name.")

        if not callable(handler) or not callable(validator):
            raise ToolRegistrationError(
                "A tool requires a handler and validator."
            )

        definition = ToolDefinition(
            name=name,
            handler=handler,
            validator=validator,
        )

        with self._lock:
            if name in self._tools:
                raise ToolRegistrationError(
                    f"Tool is already registered: {name}"
                )
            self._tools[name] = definition

    def create_grant(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        allowed_tools: Iterable[str],
        ttl_seconds: float = 300,
        max_calls: int = 10,
    ) -> GrantCredential:
        """Create a short-lived grant for one tenant and execution.

        This method is for the trusted platform controller only. The token
        is returned once; only its SHA-256 digest is retained by the broker.
        """

        validate_identifier(tenant_id, "tenant ID")
        validate_identifier(execution_id, "execution ID")

        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not math.isfinite(ttl_seconds)
            or not 0 < ttl_seconds <= MAX_GRANT_TTL_SECONDS
        ):
            raise ValueError(
                "Grant TTL must be greater than 0 and at most 3600 seconds."
            )

        if (
            isinstance(max_calls, bool)
            or not isinstance(max_calls, int)
            or not 1 <= max_calls <= MAX_GRANT_CALLS
        ):
            raise ValueError(
                f"Grant call limit must be between 1 and {MAX_GRANT_CALLS}."
            )

        if isinstance(allowed_tools, (str, bytes)):
            raise TypeError("Allowed tools must be an iterable of names.")

        try:
            tools = frozenset(allowed_tools)
        except TypeError as exc:
            raise TypeError("Allowed tools must be an iterable of names.") from exc

        if not tools:
            raise ValueError("At least one tool must be allowed.")

        for tool_name in tools:
            if (
                not isinstance(tool_name, str)
                or TOOL_NAME_PATTERN.fullmatch(tool_name) is None
            ):
                raise ValueError("Invalid allowed tool name.")

        with self._lock:
            unknown_tools = tools.difference(self._tools)

        if unknown_tools:
            raise ToolNotFoundError("Cannot grant an unregistered tool.")

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).digest()
        grant_id = str(uuid4())
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)

        record = _GrantRecord(
            grant_id=grant_id,
            token_hash=token_hash,
            tenant_id=tenant_id,
            execution_id=execution_id,
            allowed_tools=tools,
            expires_at_monotonic=self._clock() + ttl_seconds,
            expires_at_utc=expires_at,
            max_calls=max_calls,
        )

        with self._lock:
            self._grants[grant_id] = record
            self._token_index[token_hash] = grant_id

        self._append_audit(
            event="grant.create",
            tenant_id=tenant_id,
            execution_id=execution_id,
            grant_id=grant_id,
            tool_name=None,
            outcome="succeeded",
        )

        return GrantCredential(
            grant_id=grant_id,
            token=token,
            expires_at=expires_at,
        )

    def revoke_grant(
        self,
        grant_id: str,
        *,
        context: ToolCallContext,
    ) -> None:
        """Revoke a grant owned by the supplied trusted execution context."""

        scope_mismatch = False

        with self._lock:
            record = self._grants.get(grant_id)
            if record is None:
                raise InvalidGrantError("Grant does not exist.")

            if (
                record.tenant_id != context.tenant_id
                or record.execution_id != context.execution_id
            ):
                scope_mismatch = True
            elif record.revoked:
                return
            else:
                record.revoked = True

        # Audit outside the lock to avoid acquiring it recursively.
        if scope_mismatch:
            self._append_audit(
                event="grant.revoke",
                tenant_id=context.tenant_id,
                execution_id=context.execution_id,
                grant_id=None,
                tool_name=None,
                outcome="rejected",
                reason="scope_mismatch",
            )
            raise GrantScopeError("Grant belongs to another execution.")

        self._append_audit(
            event="grant.revoke",
            tenant_id=context.tenant_id,
            execution_id=context.execution_id,
            grant_id=grant_id,
            tool_name=None,
            outcome="succeeded",
        )

    def invoke(
        self,
        *,
        context: ToolCallContext,
        grant_token: str,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> dict[str, object]:
        """Authorize and invoke one registered tool.

        The context must come from the trusted controller, not from
        untrusted sandbox-supplied identity fields.
        """

        if not isinstance(context, ToolCallContext):
            raise TypeError("Tool context must be created by the trusted controller.")
        if not isinstance(arguments, Mapping):
            raise ToolInputError("Tool arguments must be an object.")
        if not isinstance(tool_name, str):
            raise ToolNotFoundError("Invalid tool name.")

        payload = dict(arguments)

        with self._lock:
            definition = self._tools.get(tool_name)

        if definition is None:
            self._append_audit(
                event="tool.invoke",
                tenant_id=context.tenant_id,
                execution_id=context.execution_id,
                grant_id=None,
                tool_name=tool_name,
                outcome="rejected",
                reason="unknown_tool",
            )
            raise ToolNotFoundError(f"Unknown tool: {tool_name}")

        try:
            grant = self._authorize(
                context=context,
                grant_token=grant_token,
                tool_name=tool_name,
            )
        except ToolAuthorizationError as exc:
            self._append_audit(
                event="tool.invoke",
                tenant_id=context.tenant_id,
                execution_id=context.execution_id,
                grant_id=None,
                tool_name=tool_name,
                outcome="rejected",
                reason=type(exc).__name__,
            )
            raise

        try:
            definition.validator(payload)
        except ToolInputError as exc:
            self._append_audit(
                event="tool.invoke",
                tenant_id=context.tenant_id,
                execution_id=context.execution_id,
                grant_id=grant.grant_id,
                tool_name=tool_name,
                outcome="rejected",
                reason=type(exc).__name__,
            )
            raise

        outcome = "failed"
        reason: str | None = "handler_error"

        try:
            result = definition.handler(payload, context)

            if not isinstance(result, Mapping):
                raise ToolHandlerError("Tool handler must return an object.")

            safe_result = dict(result)
            try:
                # Validate JSON compatibility and reject NaN/Infinity.
                encoded_result = json.dumps(safe_result, allow_nan=False)
                safe_result = json.loads(encoded_result)
            except (TypeError, ValueError) as exc:
                raise ToolHandlerError(
                    "Tool returned a non-JSON-compatible result."
                ) from exc

            outcome = "succeeded"
            reason = None
            return safe_result
        except ToolBrokerError as exc:
            reason = type(exc).__name__
            raise
        finally:
            self._append_audit(
                event="tool.invoke",
                tenant_id=context.tenant_id,
                execution_id=context.execution_id,
                grant_id=grant.grant_id,
                tool_name=tool_name,
                outcome=outcome,
                reason=reason,
            )

    def _authorize(
        self,
        *,
        context: ToolCallContext,
        grant_token: str,
        tool_name: str,
    ) -> _GrantRecord:
        """Validate a grant and atomically consume one call."""

        if not isinstance(grant_token, str) or not grant_token:
            raise InvalidGrantError("Invalid grant token.")

        token_hash = hashlib.sha256(grant_token.encode("utf-8")).digest()

        with self._lock:
            grant_id = self._token_index.get(token_hash)
            if grant_id is None:
                raise InvalidGrantError("Invalid grant token.")

            record = self._grants[grant_id]

            if record.revoked:
                raise GrantRevokedError("Grant has been revoked.")
            if self._clock() >= record.expires_at_monotonic:
                raise GrantExpiredError("Grant has expired.")
            if (
                record.tenant_id != context.tenant_id
                or record.execution_id != context.execution_id
            ):
                raise GrantScopeError("Grant belongs to another execution.")
            if tool_name not in record.allowed_tools:
                raise GrantScopeError("Tool is not included in this grant.")
            if record.calls_used >= record.max_calls:
                raise GrantCallLimitError("Grant call limit has been reached.")

            # Consume quota atomically before validation and handler execution.
            record.calls_used += 1
            return record

    def _append_audit(
        self,
        *,
        event: str,
        tenant_id: str | None,
        execution_id: str | None,
        grant_id: str | None,
        tool_name: str | None,
        outcome: str,
        reason: str | None = None,
    ) -> None:
        """Append an audit record without tokens or raw payloads."""

        record = AuditRecord(
            timestamp=datetime.now(UTC),
            event=event,
            tenant_id=tenant_id,
            execution_id=execution_id,
            grant_id=grant_id,
            tool_name=tool_name,
            outcome=outcome,
            reason=reason,
        )
        with self._lock:
            self._audit_records.append(record)


# -------------------------------------------------------------------
# Mock inventory service
# -------------------------------------------------------------------

MOCK_INVENTORY: dict[str, dict[str, object]] = {
    "SKU-100": {"available": True, "quantity": 12},
    "SKU-200": {"available": False, "quantity": 0},
}


@dataclass(frozen=True, slots=True)
class _IdempotencyRecord:
    """Result associated with one execution-scoped idempotency key."""

    request_fingerprint: str
    result: dict[str, object]


class MockInventoryService:
    """Thread-safe synthetic inventory and reservation service."""

    def __init__(
        self,
        inventory: Mapping[str, Mapping[str, object]] | None = None,
    ) -> None:
        source = inventory if inventory is not None else MOCK_INVENTORY
        self._inventory: dict[str, int] = {}

        for sku, item in source.items():
            quantity = item.get("quantity")
            if (
                not isinstance(sku, str)
                or SKU_PATTERN.fullmatch(sku) is None
                or isinstance(quantity, bool)
                or not isinstance(quantity, int)
                or quantity < 0
            ):
                raise ValueError("Invalid mock inventory configuration.")
            self._inventory[sku] = quantity

        self._idempotency_records: dict[
            tuple[str, str, str], _IdempotencyRecord
        ] = {}
        self._lock = threading.Lock()

    def lookup(
        self,
        arguments: Mapping[str, object],
        _context: ToolCallContext,
    ) -> Mapping[str, object]:
        """Look up available inventory."""

        sku = arguments["sku"]
        assert isinstance(sku, str)

        with self._lock:
            quantity = self._inventory.get(sku)
            if quantity is None:
                return {
                    "sku": sku,
                    "found": False,
                    "available": False,
                    "quantity": 0,
                }
            return {
                "sku": sku,
                "found": True,
                "available": quantity > 0,
                "quantity": quantity,
            }

    def reserve(
        self,
        arguments: Mapping[str, object],
        context: ToolCallContext,
    ) -> Mapping[str, object]:
        """Reserve inventory once per execution and idempotency key.

        Repeated requests with the same key and same input return the
        original result without decrementing inventory again. Reusing a
        key with different input raises IdempotencyConflictError.
        """

        sku = arguments["sku"]
        quantity = arguments["quantity"]
        idempotency_key = arguments["idempotency_key"]

        assert isinstance(sku, str)
        assert isinstance(quantity, int) and not isinstance(quantity, bool)
        assert isinstance(idempotency_key, str)

        scoped_key = (
            context.tenant_id,
            context.execution_id,
            idempotency_key,
        )
        fingerprint = json.dumps(
            {"sku": sku, "quantity": quantity},
            sort_keys=True,
            separators=(",", ":"),
        )

        with self._lock:
            previous = self._idempotency_records.get(scoped_key)
            if previous is not None:
                if previous.request_fingerprint != fingerprint:
                    raise IdempotencyConflictError(
                        "Idempotency key was already used with different request data."
                    )
                # Return a detached JSON-compatible copy.
                return json.loads(json.dumps(previous.result))

            available_quantity = self._inventory.get(sku)
            if available_quantity is None:
                result: dict[str, object] = {
                    "status": "not_found",
                    "sku": sku,
                    "quantity": quantity,
                }
            elif available_quantity < quantity:
                result = {
                    "status": "insufficient_stock",
                    "sku": sku,
                    "requested_quantity": quantity,
                    "available_quantity": available_quantity,
                }
            else:
                self._inventory[sku] = available_quantity - quantity
                result = {
                    "status": "reserved",
                    "reservation_id": str(uuid4()),
                    "sku": sku,
                    "quantity": quantity,
                    "remaining_quantity": available_quantity - quantity,
                }

            # Cache the result, including negative outcomes, so retries
            # cannot change the outcome for an already-used request key.
            self._idempotency_records[scoped_key] = _IdempotencyRecord(
                request_fingerprint=fingerprint,
                result=dict(result),
            )
            return dict(result)


# -------------------------------------------------------------------
# Tool argument validation
# -------------------------------------------------------------------

def validate_inventory_lookup(arguments: Mapping[str, object]) -> None:
    """Validate inventory lookup arguments."""

    if set(arguments) != {"sku"}:
        raise ToolInputError(
            "inventory.lookup requires exactly one argument: sku."
        )

    sku = arguments["sku"]
    if not isinstance(sku, str) or SKU_PATTERN.fullmatch(sku) is None:
        raise ToolInputError("Invalid SKU.")


def validate_inventory_reservation(arguments: Mapping[str, object]) -> None:
    """Validate inventory reservation arguments."""

    expected_fields = {"sku", "quantity", "idempotency_key"}
    if set(arguments) != expected_fields:
        raise ToolInputError(
            "inventory.reserve requires sku, quantity and idempotency_key."
        )

    sku = arguments["sku"]
    quantity = arguments["quantity"]
    idempotency_key = arguments["idempotency_key"]

    if not isinstance(sku, str) or SKU_PATTERN.fullmatch(sku) is None:
        raise ToolInputError("Invalid SKU.")
    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, int)
        or not 1 <= quantity <= 1000
    ):
        raise ToolInputError("Reservation quantity must be between 1 and 1000.")
    if (
        not isinstance(idempotency_key, str)
        or IDENTIFIER_PATTERN.fullmatch(idempotency_key) is None
    ):
        raise ToolInputError("Invalid idempotency key.")


def inventory_lookup(
    arguments: Mapping[str, object],
    _context: ToolCallContext | None = None,
) -> Mapping[str, object]:
    """Return the default synthetic inventory record for a SKU.

    The optional context preserves compatibility with callers that used the
    original one-argument helper, while also allowing it to be registered as
    a broker handler. The broker's factory uses its stateful service instance
    so custom inventory and reservations share the same inventory state.
    """

    sku = arguments["sku"]
    if not isinstance(sku, str):
        raise ToolInputError("Invalid SKU.")

    inventory_item = MOCK_INVENTORY.get(sku)
    if inventory_item is None:
        return {
            "sku": sku,
            "found": False,
            "available": False,
            "quantity": 0,
        }

    return {
        "sku": sku,
        "found": True,
        **inventory_item,
    }

def create_mock_tool_broker(
    *,
    inventory: Mapping[str, Mapping[str, object]] | None = None,
    include_reservation: bool = False,
) -> ToolBroker:
    """Create a broker with mock inventory tools.

    By default, register only inventory.lookup to preserve
    the original factory contract.

    Pass include_reservation=True to also register
    inventory.reserve.
    """

    broker = ToolBroker()
    service = MockInventoryService(inventory)

    broker.register_tool(
        name="inventory.lookup",
        handler=service.lookup,
        validator=validate_inventory_lookup,
    )

    if include_reservation:
        broker.register_tool(
            name="inventory.reserve",
            handler=service.reserve,
            validator=validate_inventory_reservation,
        )

    return broker