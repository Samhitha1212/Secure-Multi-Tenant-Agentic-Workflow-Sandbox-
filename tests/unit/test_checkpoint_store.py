"""Tests for tenant-scoped durable workflow checkpoints."""

import pytest

from secure_agentic_sandbox.checkpoint_store import (
    CheckpointConflictError,
    CheckpointNotFoundError,
    CheckpointStore,
    CheckpointStoreError,
)


@pytest.fixture
def specification():
    return {
        "version": 1,
        "steps": [
            {
                "id": "lookup",
                "type": "tool",
                "tool": "inventory.lookup",
                "arguments": {"sku": "SKU-100"},
            },
            {
                "id": "process",
                "type": "python",
                "source": "print('processed')",
            },
        ],
    }


@pytest.fixture
def store(tmp_path):
    database = tmp_path / "checkpoints.db"
    with CheckpointStore(database) as checkpoint_store:
        yield checkpoint_store


def create_checkpoint(store, specification):
    return store.create_checkpoint(
        tenant_id="tenant-a",
        execution_id="execution-001",
        workflow_version=1,
        specification=specification,
    )


def test_create_and_load_checkpoint(store, specification):
    checkpoint = create_checkpoint(store, specification)

    assert checkpoint.tenant_id == "tenant-a"
    assert checkpoint.execution_id == "execution-001"
    assert checkpoint.schema_version == 1
    assert checkpoint.workflow_version == 1
    assert checkpoint.next_step_index == 0
    assert checkpoint.step_results == {}
    assert checkpoint.status == "running"


def test_checkpoint_survives_store_restart(
    tmp_path,
    specification,
):
    database = tmp_path / "checkpoints.db"

    with CheckpointStore(database) as first_store:
        create_checkpoint(first_store, specification)

    with CheckpointStore(database) as second_store:
        checkpoint = second_store.get_checkpoint(
            tenant_id="tenant-a",
            execution_id="execution-001",
        )

    assert checkpoint is not None
    assert checkpoint.next_step_index == 0
    assert checkpoint.specification == specification


def test_checkpoint_is_scoped_to_tenant(store, specification):
    create_checkpoint(store, specification)

    checkpoint = store.get_checkpoint(
        tenant_id="tenant-b",
        execution_id="execution-001",
    )

    assert checkpoint is None


def test_duplicate_checkpoint_is_rejected(store, specification):
    create_checkpoint(store, specification)

    with pytest.raises(CheckpointConflictError):
        create_checkpoint(store, specification)


def test_update_progress(store, specification):
    create_checkpoint(store, specification)

    checkpoint = store.update_progress(
        tenant_id="tenant-a",
        execution_id="execution-001",
        next_step_index=1,
        step_results={
            "lookup": {
                "sku": "SKU-100",
                "found": True,
                "quantity": 12,
            }
        },
        status="running",
        expected_next_step_index=0,
    )

    assert checkpoint.next_step_index == 1
    assert checkpoint.step_results["lookup"]["quantity"] == 12
    assert checkpoint.status == "running"


def test_stale_progress_update_is_rejected(
    store,
    specification,
):
    create_checkpoint(store, specification)

    store.update_progress(
        tenant_id="tenant-a",
        execution_id="execution-001",
        next_step_index=1,
        step_results={"lookup": {"sku": "SKU-100"}},
        status="running",
        expected_next_step_index=0,
    )

    with pytest.raises(CheckpointConflictError):
        store.update_progress(
            tenant_id="tenant-a",
            execution_id="execution-001",
            next_step_index=2,
            step_results={},
            status="running",
            expected_next_step_index=0,
        )


def test_invalid_workflow_version_is_rejected(store, specification):
    with pytest.raises(ValueError):
        store.create_checkpoint(
            tenant_id="tenant-a",
            execution_id="execution-001",
            workflow_version=2,
            specification=specification,
        )


def test_non_json_values_are_rejected(store, specification):
    specification["invalid"] = float("nan")

    with pytest.raises(ValueError):
        create_checkpoint(store, specification)


def test_invalid_step_results_are_rejected(store, specification):
    with pytest.raises(TypeError):
        store.create_checkpoint(
            tenant_id="tenant-a",
            execution_id="execution-001",
            workflow_version=1,
            specification=specification,
            step_results={"lookup": ["not", "an", "object"]},
        )


def test_specification_hash_is_stored(store, specification):
    checkpoint = create_checkpoint(store, specification)

    assert len(checkpoint.specification_hash) == 64
    assert checkpoint.specification_hash == (
        store.get_checkpoint(
            tenant_id="tenant-a",
            execution_id="execution-001",
        ).specification_hash
    )


def test_pause_request_is_persisted_across_store_restart(
    tmp_path,
    specification,
):
    database = tmp_path / "checkpoints.db"

    with CheckpointStore(database) as first_store:
        create_checkpoint(first_store, specification)

        requested = first_store.request_pause(
            tenant_id="tenant-a",
            execution_id="execution-001",
        )

        assert requested.status == "running"
        assert requested.pause_requested is True

    with CheckpointStore(database) as second_store:
        checkpoint = second_store.get_checkpoint(
            tenant_id="tenant-a",
            execution_id="execution-001",
        )

    assert checkpoint is not None
    assert checkpoint.status == "running"
    assert checkpoint.pause_requested is True


def test_pause_if_requested_marks_checkpoint_paused(
    store,
    specification,
):
    create_checkpoint(store, specification)
    store.request_pause(
        tenant_id="tenant-a",
        execution_id="execution-001",
    )

    acknowledged = store.pause_if_requested(
        tenant_id="tenant-a",
        execution_id="execution-001",
    )

    checkpoint = store.get_checkpoint(
        tenant_id="tenant-a",
        execution_id="execution-001",
    )

    assert acknowledged is True
    assert checkpoint is not None
    assert checkpoint.status == "paused"
    assert checkpoint.pause_requested is False
    assert checkpoint.next_step_index == 0


def test_progress_update_saves_step_before_acknowledging_pause(
    store,
    specification,
):
    create_checkpoint(store, specification)
    store.request_pause(
        tenant_id="tenant-a",
        execution_id="execution-001",
    )

    checkpoint = store.update_progress(
        tenant_id="tenant-a",
        execution_id="execution-001",
        next_step_index=1,
        step_results={
            "lookup": {
                "sku": "SKU-100",
                "found": True,
                "quantity": 12,
            }
        },
        status="running",
        expected_next_step_index=0,
    )

    assert checkpoint.status == "paused"
    assert checkpoint.pause_requested is False
    assert checkpoint.next_step_index == 1
    assert checkpoint.step_results["lookup"]["found"] is True


def test_pause_request_is_tenant_scoped(store, specification):
    create_checkpoint(store, specification)

    with pytest.raises(CheckpointNotFoundError):
        store.request_pause(
            tenant_id="tenant-b",
            execution_id="execution-001",
        )

    checkpoint = store.get_checkpoint(
        tenant_id="tenant-a",
        execution_id="execution-001",
    )

    assert checkpoint is not None
    assert checkpoint.pause_requested is False


def test_old_database_is_migrated_with_pause_flag(
    tmp_path,
    specification,
):
    import sqlite3

    database = tmp_path / "legacy-checkpoints.db"

    with CheckpointStore(database) as first_store:
        create_checkpoint(first_store, specification)

    # Simulate a database created before pause_requested existed.
    with sqlite3.connect(database) as connection:
        connection.execute(
            "ALTER TABLE workflow_checkpoints DROP COLUMN pause_requested"
        )
        connection.commit()

    with CheckpointStore(database) as migrated_store:
        checkpoint = migrated_store.get_checkpoint(
            tenant_id="tenant-a",
            execution_id="execution-001",
        )

    assert checkpoint is not None
    assert checkpoint.status == "running"
    assert checkpoint.pause_requested is False


def test_claim_for_resume_preserves_checkpoint_progress(
    store,
    specification,
):
    create_checkpoint(store, specification)

    store.update_progress(
        tenant_id="tenant-a",
        execution_id="execution-001",
        next_step_index=1,
        step_results={"lookup": {"found": True, "quantity": 12}},
        status="running",
        expected_next_step_index=0,
    )
    store.request_pause(
        tenant_id="tenant-a",
        execution_id="execution-001",
    )
    store.update_progress(
        tenant_id="tenant-a",
        execution_id="execution-001",
        next_step_index=1,
        step_results={"lookup": {"found": True, "quantity": 12}},
        status="running",
        expected_next_step_index=1,
    )

    checkpoint = store.claim_for_resume(
        tenant_id="tenant-a",
        execution_id="execution-001",
    )

    assert checkpoint.status == "running"
    assert checkpoint.pause_requested is False
    assert checkpoint.next_step_index == 1
    assert checkpoint.step_results["lookup"]["found"] is True


def test_running_checkpoint_cannot_be_claimed_twice(
    store,
    specification,
):
    create_checkpoint(store, specification)

    store.update_progress(
        tenant_id="tenant-a",
        execution_id="execution-001",
        next_step_index=1,
        step_results={"lookup": {"found": True}},
        status="running",
        expected_next_step_index=0,
    )
    store.request_pause(
        tenant_id="tenant-a",
        execution_id="execution-001",
    )
    store.update_progress(
        tenant_id="tenant-a",
        execution_id="execution-001",
        next_step_index=1,
        step_results={"lookup": {"found": True}},
        status="running",
        expected_next_step_index=1,
    )

    store.claim_for_resume(
        tenant_id="tenant-a",
        execution_id="execution-001",
    )

    with pytest.raises(CheckpointConflictError):
        store.claim_for_resume(
            tenant_id="tenant-a",
            execution_id="execution-001",
        )


def test_claim_for_resume_rejects_another_tenant(
    store,
    specification,
):
    create_checkpoint(store, specification)

    store.update_progress(
        tenant_id="tenant-a",
        execution_id="execution-001",
        next_step_index=1,
        step_results={"lookup": {"found": True}},
        status="running",
        expected_next_step_index=0,
    )
    store.request_pause(
        tenant_id="tenant-a",
        execution_id="execution-001",
    )
    store.update_progress(
        tenant_id="tenant-a",
        execution_id="execution-001",
        next_step_index=1,
        step_results={"lookup": {"found": True}},
        status="running",
        expected_next_step_index=1,
    )

    with pytest.raises(CheckpointNotFoundError):
        store.claim_for_resume(
            tenant_id="tenant-b",
            execution_id="execution-001",
        )


def test_recovery_pauses_interrupted_execution_and_preserves_progress(
    store,
    specification,
):
    create_checkpoint(store, specification)

    saved_results = {
        "lookup": {
            "found": True,
            "quantity": 12,
        }
    }
    store.update_progress(
        tenant_id="tenant-a",
        execution_id="execution-001",
        next_step_index=1,
        step_results=saved_results,
        status="running",
        expected_next_step_index=0,
    )
    store.request_pause(
        tenant_id="tenant-a",
        execution_id="execution-001",
    )

    recovered_count = store.recover_interrupted_executions()

    checkpoint = store.get_checkpoint(
        tenant_id="tenant-a",
        execution_id="execution-001",
    )

    assert recovered_count == 1
    assert checkpoint is not None
    assert checkpoint.status == "paused"
    assert checkpoint.pause_requested is False
    assert checkpoint.next_step_index == 1
    assert checkpoint.step_results == saved_results


def test_recovery_does_not_change_completed_or_paused_checkpoints(
    store,
    specification,
):
    create_checkpoint(store, specification)

    store.update_progress(
        tenant_id="tenant-a",
        execution_id="execution-001",
        next_step_index=2,
        step_results={
            "lookup": {"found": True},
            "process": {"exit_code": 0},
        },
        status="completed",
        expected_next_step_index=0,
    )

    store.create_checkpoint(
        tenant_id="tenant-a",
        execution_id="execution-002",
        workflow_version=1,
        specification=specification,
        status="paused",
    )

    recovered_count = store.recover_interrupted_executions()

    completed = store.get_checkpoint(
        tenant_id="tenant-a",
        execution_id="execution-001",
    )
    paused = store.get_checkpoint(
        tenant_id="tenant-a",
        execution_id="execution-002",
    )

    assert recovered_count == 0
    assert completed is not None
    assert completed.status == "completed"
    assert paused is not None
    assert paused.status == "paused"


def test_runtime_lock_allows_only_one_store_owner(tmp_path):
    database = tmp_path / "runtime-lock.sqlite3"
    first_store = CheckpointStore(database)
    second_store = CheckpointStore(database)

    try:
        first_store.acquire_runtime_lock()

        with pytest.raises(CheckpointStoreError):
            second_store.acquire_runtime_lock()

        first_store.release_runtime_lock()
        second_store.acquire_runtime_lock()
    finally:
        first_store.close()
        second_store.close()

def test_checkpoint_persists_allowed_tools(store, specification):
    checkpoint = store.create_checkpoint(
        tenant_id="tenant-a",
        execution_id="execution-001",
        workflow_version=1,
        specification=specification,
        allowed_tools={
            "inventory.lookup",
            "inventory.reserve",
        },
    )

    assert checkpoint.allowed_tools == frozenset({
        "inventory.lookup",
        "inventory.reserve",
    })

    loaded = store.get_checkpoint(
        tenant_id="tenant-a",
        execution_id="execution-001",
    )

    assert loaded is not None
    assert loaded.allowed_tools == checkpoint.allowed_tools


def test_checkpoint_distinguishes_empty_permissions_from_legacy(
    store,
    specification,
):
    new_checkpoint = store.create_checkpoint(
        tenant_id="tenant-a",
        execution_id="execution-empty",
        workflow_version=1,
        specification=specification,
        allowed_tools=set(),
    )

    legacy_checkpoint = store.create_checkpoint(
        tenant_id="tenant-a",
        execution_id="execution-legacy",
        workflow_version=1,
        specification=specification,
    )

    assert new_checkpoint.allowed_tools == frozenset()
    assert legacy_checkpoint.allowed_tools is None
