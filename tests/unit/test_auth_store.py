
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from secure_agentic_sandbox.auth_store import AuthStore


@pytest.fixture
def auth_store(tmp_path):
    database_path = tmp_path / "test_auth.sqlite3"
    return AuthStore(database_path)


def test_demo_users_are_created(auth_store):
    users = auth_store.list_demo_users()

    emails = {user["email"] for user in users}

    assert emails == {
        "alice@tenant-a.demo",
        "bob@tenant-b.demo",
        "charlie@tenant-a.demo",
    }


def test_create_and_authenticate_session(auth_store):
    token, user = auth_store.create_session(
        "alice@tenant-a.demo"
    )

    assert token
    assert user.email == "alice@tenant-a.demo"
    assert user.tenant_id == "tenant-a"

    authenticated = auth_store.authenticate_session(token)

    assert authenticated is not None
    assert authenticated.user_id == user.user_id
    assert authenticated.tenant_id == "tenant-a"


def test_session_token_is_not_stored_in_database(auth_store, tmp_path):
    database_path = tmp_path / "test_auth.sqlite3"

    token, _ = auth_store.create_session(
        "alice@tenant-a.demo"
    )

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT token_hash FROM sessions"
        ).fetchone()

    assert row is not None
    assert row[0] != token
    assert len(row[0]) == 64


def test_unknown_demo_user_is_rejected(auth_store):
    with pytest.raises(ValueError, match="Unknown or inactive"):
        auth_store.create_session("unknown@tenant-a.demo")


def test_logout_revokes_session(auth_store):
    token, _ = auth_store.create_session(
        "alice@tenant-a.demo"
    )

    assert auth_store.authenticate_session(token) is not None

    assert auth_store.revoke_session(token) is True
    assert auth_store.authenticate_session(token) is None


def test_session_cannot_be_revoked_twice(auth_store):
    token, _ = auth_store.create_session(
        "alice@tenant-a.demo"
    )

    assert auth_store.revoke_session(token) is True
    assert auth_store.revoke_session(token) is False


def test_expired_session_is_rejected(auth_store, tmp_path):
    database_path = tmp_path / "test_auth.sqlite3"

    token, _ = auth_store.create_session(
        "alice@tenant-a.demo"
    )

    expired_at = (
        datetime.now(UTC) - timedelta(hours=1)
    ).isoformat(timespec="seconds")

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE sessions SET expires_at = ?",
            (expired_at,),
        )

    assert auth_store.authenticate_session(token) is None


def test_users_have_correct_tenant_assignments(auth_store):
    _, alice = auth_store.create_session(
        "alice@tenant-a.demo"
    )
    _, bob = auth_store.create_session(
        "bob@tenant-b.demo"
    )
    _, charlie = auth_store.create_session(
        "charlie@tenant-a.demo"
    )

    assert alice.tenant_id == "tenant-a"
    assert charlie.tenant_id == "tenant-a"
    assert bob.tenant_id == "tenant-b"

    assert alice.user_id != charlie.user_id
    assert alice.user_id != bob.user_id
