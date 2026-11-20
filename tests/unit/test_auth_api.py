
"""Tests for demo-user authentication API endpoints."""

import pytest
from fastapi.testclient import TestClient

from secure_agentic_sandbox.api import ApiPrincipal, create_app

API_KEY = "auth-test-api-key"


@pytest.fixture
def client(tmp_path):
    """Create an API client with demo authentication enabled."""

    app = create_app(
        api_keys={
            API_KEY: ApiPrincipal(
                tenant_id="tenant-api",
                allowed_tools=frozenset({"inventory.lookup"}),
            )
        },
        checkpoint_db_path=tmp_path / "auth-api.sqlite3",
        demo_auth_enabled=True,
    )

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_without_demo_auth(tmp_path):
    """Create an API client with demo authentication disabled."""

    app = create_app(
        checkpoint_db_path=tmp_path / "no-demo-auth.sqlite3",
        demo_auth_enabled=False,
    )

    with TestClient(app) as test_client:
        yield test_client


def login(client, email="alice@tenant-a.demo"):
    """Log in a demo user and return the response."""

    return client.post(
        "/auth/login",
        json={"email": email},
    )


def auth_headers(token):
    """Build bearer authentication headers."""

    return {"Authorization": f"Bearer {token}"}


def test_list_demo_users(client):
    response = client.get("/auth/demo-users")

    assert response.status_code == 200

    users = response.json()["users"]

    assert {user["email"] for user in users} == {
        "alice@tenant-a.demo",
        "charlie@tenant-a.demo",
        "bob@tenant-b.demo",
    }

    assert all("allowed_tools" not in user for user in users)


def test_demo_auth_endpoints_are_disabled(client_without_demo_auth):
    users_response = client_without_demo_auth.get("/auth/demo-users")
    login_response = login(client_without_demo_auth)

    assert users_response.status_code == 404
    assert login_response.status_code == 404


def test_demo_user_can_login(client):
    response = login(client)

    assert response.status_code == 200

    result = response.json()

    assert result["access_token"]
    assert result["token_type"] == "bearer"
    assert result["user"] == {
        "user_id": "user-alice",
        "email": "alice@tenant-a.demo",
        "tenant_id": "tenant-a",
        "role": "member",
    }


def test_unknown_demo_user_cannot_login(client):
    response = login(client, "unknown@tenant-a.demo")

    assert response.status_code == 401


def test_login_rejects_extra_request_fields(client):
    response = client.post(
        "/auth/login",
        json={
            "email": "alice@tenant-a.demo",
            "tenant_id": "tenant-b",
        },
    )

    assert response.status_code == 422


def test_current_user_returns_authenticated_identity(client):
    login_response = login(client)
    token = login_response.json()["access_token"]

    response = client.get(
        "/auth/me",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "user-alice",
        "email": "alice@tenant-a.demo",
        "tenant_id": "tenant-a",
        "role": "member",
    }


def test_different_users_receive_their_own_tenant_identity(client):
    alice_login = login(client, "alice@tenant-a.demo")
    bob_login = login(client, "bob@tenant-b.demo")

    alice_token = alice_login.json()["access_token"]
    bob_token = bob_login.json()["access_token"]

    alice_response = client.get(
        "/auth/me",
        headers=auth_headers(alice_token),
    )
    bob_response = client.get(
        "/auth/me",
        headers=auth_headers(bob_token),
    )

    assert alice_response.json()["tenant_id"] == "tenant-a"
    assert bob_response.json()["tenant_id"] == "tenant-b"


def test_logout_revokes_demo_session(client):
    login_response = login(client)
    token = login_response.json()["access_token"]

    logout_response = client.post(
        "/auth/logout",
        headers=auth_headers(token),
    )

    assert logout_response.status_code == 200
    assert logout_response.json() == {"status": "logged_out"}

    me_response = client.get(
        "/auth/me",
        headers=auth_headers(token),
    )

    assert me_response.status_code == 401


def test_logout_requires_valid_demo_session(client):
    response = client.post(
        "/auth/logout",
        headers=auth_headers("invalid-session-token"),
    )

    assert response.status_code == 401


def test_api_key_authentication_still_works(client):
    response = client.get(
        "/tools",
        headers=auth_headers(API_KEY),
    )

    assert response.status_code == 200
    assert response.json() == {
        "tools": ["inventory.lookup"],
    }


def test_api_key_cannot_use_demo_logout(client):
    response = client.post(
        "/auth/logout",
        headers=auth_headers(API_KEY),
    )

    assert response.status_code == 401


def test_api_key_cannot_use_demo_current_user_endpoint(client):
    response = client.get(
        "/auth/me",
        headers=auth_headers(API_KEY),
    )

    assert response.status_code == 403
