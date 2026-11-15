"""Trusted SQLite storage for durable workflow checkpoints."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import sqlite3
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self, TextIO

from secure_agentic_sandbox.tool_broker import validate_identifier

CHECKPOINT_SCHEMA_VERSION = 1

CheckpointStatus = Literal[
    "running",
    "paused",
    "completed",
    "failed",
]

VALID_STATUSES = frozenset(
    {"running", "paused", "completed", "failed"}
)


class CheckpointStoreError(Exception):
    """Base exception for checkpoint storage errors."""


class CheckpointConflictError(CheckpointStoreError):
    """Raised when a checkpoint already exists or is stale."""


class CheckpointNotFoundError(CheckpointStoreError):
    """Raised when an owned checkpoint cannot be found."""


class CheckpointSchemaVersionError(CheckpointStoreError):
    """Raised when a checkpoint uses an unsupported schema version."""


class CheckpointCorruptError(CheckpointStoreError):
    """Raised when stored checkpoint data fails integrity validation."""


@dataclass(frozen=True, slots=True)
class WorkflowCheckpoint:
    """Validated checkpoint data returned by the trusted store."""

    tenant_id: str
    execution_id: str
    schema_version: int
    workflow_version: int
    specification: dict[str, object]
    specification_hash: str
    allowed_tools: frozenset[str] | None
    next_step_index: int
    step_results: dict[str, dict[str, object]]
    status: CheckpointStatus
    pause_requested: bool
    created_at: str
    updated_at: str


def _validate_json_value(value: object) -> None:
    """Reject values that are not strict JSON data."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings.")
            _validate_json_value(item)
        return

    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return

    if value is None or isinstance(value, (str, bool, int)):
        return

    if isinstance(value, float) and math.isfinite(value):
        return

    raise ValueError("Value is not valid finite JSON data.")


def _normalize_json_object(
    value: object,
    description: str,
) -> dict[str, object]:
    """Validate and copy a JSON object."""

    if not isinstance(value, Mapping):
        raise TypeError(f"{description} must be an object.")

    _validate_json_value(value)

    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(
            f"{description} must contain valid JSON data."
        ) from exc

    if not isinstance(decoded, dict):
        raise TypeError(f"{description} must be an object.")

    return decoded


def _normalize_step_results(
    value: object,
) -> dict[str, dict[str, object]]:
    """Validate the completed workflow step results."""

    normalized = _normalize_json_object(
        value,
        "Step results",
    )

    for step_id, result in normalized.items():
        if not step_id:
            raise ValueError("Step result IDs cannot be empty.")
        if not isinstance(result, dict):
            raise TypeError(
                "Each step result must be a JSON object."
            )

    return normalized


def _normalize_allowed_tools(
    value: Iterable[str] | None,
) -> frozenset[str] | None:
    """Validate and normalize checkpoint tool permissions."""

    if value is None:
        return None

    if isinstance(value, (str, bytes)):
        raise TypeError("Allowed tools must be an iterable of names.")

    try:
        tools = frozenset(value)
    except TypeError as exc:
        raise TypeError(
            "Allowed tools must be an iterable of names."
        ) from exc

    if any(not isinstance(name, str) or not name for name in tools):
        raise ValueError("Allowed tool names must be non-empty strings.")

    return tools


def _validate_checkpoint_fields(
    *,
    workflow_version: int,
    specification: Mapping[str, object],
    next_step_index: int,
    status: str,
) -> tuple[dict[str, object], str]:
    """Validate immutable workflow metadata and progress."""

    if (
        isinstance(workflow_version, bool)
        or not isinstance(workflow_version, int)
        or workflow_version < 1
    ):
        raise ValueError("Workflow version must be a positive integer.")

    normalized_spec = _normalize_json_object(
        specification,
        "Workflow specification",
    )

    if normalized_spec.get("version") != workflow_version:
        raise ValueError(
            "Workflow version does not match the specification."
        )

    steps = normalized_spec.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("Workflow specification requires steps.")

    if (
        isinstance(next_step_index, bool)
        or not isinstance(next_step_index, int)
        or not 0 <= next_step_index <= len(steps)
    ):
        raise ValueError("Invalid next step index.")

    if status not in VALID_STATUSES:
        raise ValueError("Invalid checkpoint status.")

    canonical = json.dumps(
        normalized_spec,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    specification_hash = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()

    return normalized_spec, specification_hash


class CheckpointStore:
    """Tenant-scoped persistent checkpoint storage."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        if self._database_path != ":memory:":
            Path(self._database_path).parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        self._lock = threading.RLock()
        self._runtime_lock_file: TextIO | None = None
        self._connection = sqlite3.connect(
            self._database_path,
            timeout=10,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 10000")

        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_checkpoints (
                tenant_id TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                workflow_version INTEGER NOT NULL,
                specification_json TEXT NOT NULL,
                specification_hash TEXT NOT NULL,
                allowed_tools_json TEXT,
                next_step_index INTEGER NOT NULL CHECK (
                    next_step_index >= 0
                ),
                step_results_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN (
                        'running',
                        'paused',
                        'completed',
                        'failed'
                    )
                ),
                pause_requested INTEGER NOT NULL DEFAULT 0 CHECK (pause_requested IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, execution_id)
            )
            """
        )
        # Migrate databases created before pause requests were supported.
        columns = {
            row["name"]
            for row in self._connection.execute(
                "PRAGMA table_info(workflow_checkpoints)"
            )
        }
        if "pause_requested" not in columns:
            self._connection.execute(
                """
                ALTER TABLE workflow_checkpoints
                ADD COLUMN pause_requested INTEGER NOT NULL DEFAULT 0
                CHECK (pause_requested IN (0, 1))
                """
            )

        if "allowed_tools_json" not in columns:
            self._connection.execute(
                """
                ALTER TABLE workflow_checkpoints
                ADD COLUMN allowed_tools_json TEXT
                """
            )

        self._connection.commit()

    def close(self) -> None:
        """Release the runtime lock and close the SQLite connection."""

        self.release_runtime_lock()
        with self._lock:
            self._connection.close()

    def acquire_runtime_lock(self) -> None:
        """Ensure only one API process owns this checkpoint database.

        The lock is held for the API lifespan. The operating system releases
        it automatically if the process exits unexpectedly.
        """

        if self._database_path == ":memory:":
            return

        with self._lock:
            if self._runtime_lock_file is not None:
                return

            lock_path = Path(
                f"{Path(self._database_path).resolve()}.runtime.lock"
            )
            lock_file = lock_path.open("a+", encoding="utf-8")

            try:
                fcntl.flock(
                    lock_file.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            except BlockingIOError as exc:
                lock_file.close()
                raise CheckpointStoreError(
                    "Checkpoint database is already owned by another "
                    "API process. Run only one API worker per database."
                ) from exc

            self._runtime_lock_file = lock_file

    def release_runtime_lock(self) -> None:
        """Release the API process lock, if it is held."""

        with self._lock:
            lock_file = self._runtime_lock_file
            self._runtime_lock_file = None

            if lock_file is None:
                return

            try:
                fcntl.flock(
                    lock_file.fileno(),
                    fcntl.LOCK_UN,
                )
            finally:
                lock_file.close()

    def recover_interrupted_executions(self) -> int:
        """Mark executions left running by a previous process as paused.

        Saved step progress is retained, and pending pause requests are
        cleared. Call this only after acquiring the runtime lock.
        """

        updated_at = datetime.now(UTC).isoformat()

        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                cursor = self._connection.execute(
                    """
                    UPDATE workflow_checkpoints
                    SET status = 'paused',
                        pause_requested = 0,
                        updated_at = ?
                    WHERE status = 'running'
                    """,
                    (updated_at,),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

        return cursor.rowcount

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def create_checkpoint(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        workflow_version: int,
        specification: Mapping[str, object],
        allowed_tools: Iterable[str] | None = None,
        next_step_index: int = 0,
        step_results: Mapping[str, object] | None = None,
        status: CheckpointStatus = "running",
    ) -> WorkflowCheckpoint:
        """Create the initial checkpoint for one execution."""

        validate_identifier(tenant_id, "tenant ID")
        validate_identifier(execution_id, "execution ID")

        normalized_spec, specification_hash = (
            _validate_checkpoint_fields(
                workflow_version=workflow_version,
                specification=specification,
                next_step_index=next_step_index,
                status=status,
            )
        )
        normalized_results = _normalize_step_results(
            step_results if step_results is not None else {}
        )

        normalized_allowed_tools = _normalize_allowed_tools(
            allowed_tools
        )

        allowed_tools_json = (
            json.dumps(sorted(normalized_allowed_tools))
            if normalized_allowed_tools is not None
            else None
        )

        now = datetime.now(UTC).isoformat()
        spec_json = json.dumps(
            normalized_spec,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        results_json = json.dumps(
            normalized_results,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        with self._lock:
            try:
                self._connection.execute(
                    """
                    INSERT INTO workflow_checkpoints (
                        tenant_id,
                        execution_id,
                        schema_version,
                        workflow_version,
                        specification_json,
                        specification_hash,
                        allowed_tools_json,
                        next_step_index,
                        step_results_json,
                        status,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        execution_id,
                        CHECKPOINT_SCHEMA_VERSION,
                        workflow_version,
                        spec_json,
                        specification_hash,
                        allowed_tools_json,
                        next_step_index,
                        results_json,
                        status,
                        now,
                        now,
                    ),
                )
                self._connection.commit()
            except sqlite3.IntegrityError as exc:
                self._connection.rollback()
                raise CheckpointConflictError(
                    "A checkpoint already exists for this execution."
                ) from exc

        checkpoint = self.get_checkpoint(
            tenant_id=tenant_id,
            execution_id=execution_id,
        )
        assert checkpoint is not None
        return checkpoint

    def get_checkpoint(
        self,
        *,
        tenant_id: str,
        execution_id: str,
    ) -> WorkflowCheckpoint | None:
        """Load a checkpoint only within the supplied tenant scope."""

        validate_identifier(tenant_id, "tenant ID")
        validate_identifier(execution_id, "execution ID")

        with self._lock:
            row = self._connection.execute(
                """
                SELECT *
                FROM workflow_checkpoints
                WHERE tenant_id = ? AND execution_id = ?
                """,
                (tenant_id, execution_id),
            ).fetchone()

        if row is None:
            return None

        return self._row_to_checkpoint(row)

    def request_pause(
        self,
        *,
        tenant_id: str,
        execution_id: str,
    ) -> WorkflowCheckpoint:
        """Request a pause without interrupting the active workflow step."""

        validate_identifier(tenant_id, "tenant ID")
        validate_identifier(execution_id, "execution ID")
        now = datetime.now(UTC).isoformat()

        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    """
                    SELECT status
                    FROM workflow_checkpoints
                    WHERE tenant_id = ? AND execution_id = ?
                    """,
                    (tenant_id, execution_id),
                ).fetchone()

                if row is None:
                    raise CheckpointNotFoundError(
                        "Checkpoint not found for this tenant."
                    )

                if row["status"] != "running":
                    raise CheckpointConflictError(
                        "Only a running workflow can receive a pause request."
                    )

                self._connection.execute(
                    """
                    UPDATE workflow_checkpoints
                    SET pause_requested = 1, updated_at = ?
                    WHERE tenant_id = ? AND execution_id = ?
                    """,
                    (now, tenant_id, execution_id),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

        checkpoint = self.get_checkpoint(
            tenant_id=tenant_id,
            execution_id=execution_id,
        )
        if checkpoint is None:
            raise CheckpointNotFoundError(
                "Checkpoint disappeared after pause request."
            )
        return checkpoint

    def pause_if_requested(
        self,
        *,
        tenant_id: str,
        execution_id: str,
    ) -> bool:
        """Atomically acknowledge a pending pause request."""

        validate_identifier(tenant_id, "tenant ID")
        validate_identifier(execution_id, "execution ID")
        now = datetime.now(UTC).isoformat()

        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    """
                    SELECT status, pause_requested
                    FROM workflow_checkpoints
                    WHERE tenant_id = ? AND execution_id = ?
                    """,
                    (tenant_id, execution_id),
                ).fetchone()

                if row is None:
                    raise CheckpointNotFoundError(
                        "Checkpoint not found for this tenant."
                    )

                if row["status"] == "paused":
                    self._connection.commit()
                    return True

                if (
                    row["status"] != "running"
                    or not bool(row["pause_requested"])
                ):
                    self._connection.commit()
                    return False

                self._connection.execute(
                    """
                    UPDATE workflow_checkpoints
                    SET status = 'paused',
                        pause_requested = 0,
                        updated_at = ?
                    WHERE tenant_id = ? AND execution_id = ?
                    """,
                    (now, tenant_id, execution_id),
                )
                self._connection.commit()
                return True
            except Exception:
                self._connection.rollback()
                raise

    def claim_for_resume(
        self,
        *,
        tenant_id: str,
        execution_id: str,
    ) -> WorkflowCheckpoint:
        """Atomically claim a paused checkpoint for one resume worker."""

        validate_identifier(tenant_id, "tenant ID")
        validate_identifier(execution_id, "execution ID")
        now = datetime.now(UTC).isoformat()

        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    """
                    SELECT status, next_step_index, specification_json
                    FROM workflow_checkpoints
                    WHERE tenant_id = ? AND execution_id = ?
                    """,
                    (tenant_id, execution_id),
                ).fetchone()

                if row is None:
                    raise CheckpointNotFoundError(
                        "Checkpoint not found for this tenant."
                    )

                if row["status"] != "paused":
                    raise CheckpointConflictError(
                        "Only a paused workflow can be resumed."
                    )

                specification = json.loads(row["specification_json"])
                steps = specification.get("steps")
                next_step_index = row["next_step_index"]

                if (
                    not isinstance(steps, list)
                    or next_step_index >= len(steps)
                ):
                    raise CheckpointConflictError(
                        "Checkpoint has no remaining workflow steps."
                    )

                cursor = self._connection.execute(
                    """
                    UPDATE workflow_checkpoints
                    SET status = 'running',
                        pause_requested = 0,
                        updated_at = ?
                    WHERE tenant_id = ?
                      AND execution_id = ?
                      AND status = 'paused'
                    """,
                    (now, tenant_id, execution_id),
                )

                if cursor.rowcount != 1:
                    raise CheckpointConflictError(
                        "Checkpoint was claimed by another worker."
                    )

                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

        checkpoint = self.get_checkpoint(
            tenant_id=tenant_id,
            execution_id=execution_id,
        )
        if checkpoint is None:
            raise CheckpointNotFoundError(
                "Checkpoint disappeared after resume claim."
            )
        return checkpoint

    def update_progress(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        next_step_index: int,
        step_results: Mapping[str, object],
        status: CheckpointStatus,
        expected_next_step_index: int | None = None,
    ) -> WorkflowCheckpoint:
        """Persist progress without changing the workflow definition."""

        validate_identifier(tenant_id, "tenant ID")
        validate_identifier(execution_id, "execution ID")

        if (
            isinstance(next_step_index, bool)
            or not isinstance(next_step_index, int)
            or next_step_index < 0
        ):
            raise ValueError("Invalid next step index.")

        if status not in VALID_STATUSES:
            raise ValueError("Invalid checkpoint status.")

        if expected_next_step_index is not None and (
            isinstance(expected_next_step_index, bool)
            or not isinstance(expected_next_step_index, int)
            or expected_next_step_index < 0
        ):
            raise ValueError("Invalid expected step index.")

        normalized_results = _normalize_step_results(step_results)
        results_json = json.dumps(
            normalized_results,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        now = datetime.now(UTC).isoformat()

        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    """
                    SELECT next_step_index, specification_json, pause_requested
                    FROM workflow_checkpoints
                    WHERE tenant_id = ? AND execution_id = ?
                    """,
                    (tenant_id, execution_id),
                ).fetchone()

                if row is None:
                    raise CheckpointNotFoundError(
                        "Checkpoint not found for this tenant."
                    )

                specification = json.loads(row["specification_json"])
                steps = specification.get("steps")

                if (
                    not isinstance(steps, list)
                    or next_step_index > len(steps)
                ):
                    raise ValueError("Next step index exceeds workflow.")

                current_index = row["next_step_index"]
                if (
                    expected_next_step_index is not None
                    and current_index != expected_next_step_index
                ):
                    raise CheckpointConflictError(
                        "Checkpoint progress was changed by another worker."
                    )

                # Persist the finished step before acknowledging a pause.
                if (
                    bool(row["pause_requested"])
                    and status == "running"
                    and next_step_index < len(steps)
                ):
                    status = "paused"

                cursor = self._connection.execute(
                    """
                    UPDATE workflow_checkpoints
                    SET next_step_index = ?,
                        step_results_json = ?,
                        status = ?,
                        pause_requested = CASE
                            WHEN ? = 'running' THEN pause_requested
                            ELSE 0
                        END,
                        updated_at = ?
                    WHERE tenant_id = ? AND execution_id = ?
                    """,
                    (
                        next_step_index,
                        results_json,
                        status,
                        status,
                        now,
                        tenant_id,
                        execution_id,
                    ),
                )

                if cursor.rowcount != 1:
                    raise CheckpointConflictError(
                        "Checkpoint progress could not be updated."
                    )

                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

        checkpoint = self.get_checkpoint(
            tenant_id=tenant_id,
            execution_id=execution_id,
        )
        if checkpoint is None:
            raise CheckpointNotFoundError(
                "Checkpoint disappeared after update."
            )
        return checkpoint

    @staticmethod
    def _row_to_checkpoint(
        row: sqlite3.Row,
    ) -> WorkflowCheckpoint:
        """Decode a row and verify its schema and specification hash."""

        schema_version = row["schema_version"]
        if schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointSchemaVersionError(
                f"Unsupported checkpoint schema version: "
                f"{schema_version}"
            )

        try:
            specification = json.loads(row["specification_json"])
            step_results = json.loads(row["step_results_json"])
        except (TypeError, ValueError) as exc:
            raise CheckpointCorruptError(
                "Checkpoint contains invalid JSON."
            ) from exc

        if not isinstance(specification, dict):
            raise CheckpointCorruptError(
                "Stored workflow specification is not an object."
            )

        if not isinstance(step_results, dict):
            raise CheckpointCorruptError(
                "Stored step results are not an object."
            )

        raw_allowed_tools = row["allowed_tools_json"]

        if raw_allowed_tools is None:
            normalized_allowed_tools = None
        else:
            try:
                decoded_allowed_tools = json.loads(raw_allowed_tools)

                if (
                    not isinstance(decoded_allowed_tools, list)
                    or any(
                        not isinstance(name, str) or not name
                        for name in decoded_allowed_tools
                    )
                    or len(decoded_allowed_tools)
                    != len(set(decoded_allowed_tools))
                ):
                    raise ValueError("Invalid allowed tools.")

                normalized_allowed_tools = frozenset(
                    decoded_allowed_tools
                )
            except (TypeError, ValueError) as exc:
                raise CheckpointCorruptError(
                    "Stored checkpoint tool permissions are invalid."
                ) from exc

        canonical = json.dumps(
            specification,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        actual_hash = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()

        if actual_hash != row["specification_hash"]:
            raise CheckpointCorruptError(
                "Workflow specification hash does not match."
            )

        try:
            normalized_results = _normalize_step_results(step_results)
        except ValueError as exc:
            raise CheckpointCorruptError(
                "Stored step results are invalid."
            ) from exc

        return WorkflowCheckpoint(
            tenant_id=row["tenant_id"],
            execution_id=row["execution_id"],
            schema_version=schema_version,
            workflow_version=row["workflow_version"],
            specification=specification,
            specification_hash=actual_hash,
            allowed_tools=normalized_allowed_tools,
            next_step_index=row["next_step_index"],
            step_results=normalized_results,
            status=row["status"],
            pause_requested=bool(row["pause_requested"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
