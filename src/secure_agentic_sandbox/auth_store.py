
"""Persistent demo-user and session storage."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AuthUser:
    """Trusted user identity and permissions."""

    user_id: str
    email: str
    tenant_id: str
    role: str
    allowed_tools: frozenset[str]


class AuthStore:
    """SQLite-backed user and session storage."""

    DEMO_USERS = (
        {
            "user_id": "user-alice",
            "email": "alice@tenant-a.demo",
            "tenant_id": "tenant-a",
            "role": "member",
            "allowed_tools": ["inventory.lookup", "inventory.reserve"],
        },
        {
            "user_id": "user-charlie",
            "email": "charlie@tenant-a.demo",
            "tenant_id": "tenant-a",
            "role": "member",
            "allowed_tools": ["inventory.lookup"],
        },
        {
            "user_id": "user-bob",
            "email": "bob@tenant-b.demo",
            "tenant_id": "tenant-b",
            "role": "member",
            "allowed_tools": ["inventory.lookup"],
        },
    )

    def __init__(
        self,
        database_path: str | Path,
        *,
        session_lifetime_hours: int = 8,
    ) -> None:
        if session_lifetime_hours <= 0:
            raise ValueError("Session lifetime must be positive.")

        self._database_path = str(database_path)
        self._session_lifetime = timedelta(hours=session_lifetime_hours)

        if self._database_path != ":memory:":
            Path(self._database_path).parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        self._initialize_schema()
        self._seed_demo_users()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._database_path,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    tenant_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    allowed_tools_json TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                        CHECK (is_active IN (0, 1)),
                    created_at TEXT NOT NULL
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_user_id
                ON sessions(user_id)
                """
            )

    def _seed_demo_users(self) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")

        import json

        with self._connect() as connection:
            for user in self.DEMO_USERS:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO users (
                        user_id,
                        email,
                        tenant_id,
                        role,
                        allowed_tools_json,
                        is_active,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        user["user_id"],
                        user["email"],
                        user["tenant_id"],
                        user["role"],
                        json.dumps(user["allowed_tools"]),
                        now,
                    ),
                )

    def list_demo_users(self) -> list[dict[str, str]]:
        """Return active demo identities without exposing permissions."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT user_id, email, tenant_id, role
                FROM users
                WHERE is_active = 1
                ORDER BY email
                """
            ).fetchall()

        return [dict(row) for row in rows]

    def create_session(self, email: str) -> tuple[str, AuthUser]:
        """Create a session for an active predefined demo user."""

        import json

        normalized_email = email.strip().lower()

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM users
                WHERE email = ? AND is_active = 1
                """,
                (normalized_email,),
            ).fetchone()

            if row is None:
                raise ValueError("Unknown or inactive demo user.")

            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(
                token.encode("utf-8")
            ).hexdigest()

            now = datetime.now(UTC)
            session_id = secrets.token_urlsafe(16)

            connection.execute(
                """
                INSERT INTO sessions (
                    session_id,
                    user_id,
                    token_hash,
                    expires_at,
                    revoked_at,
                    created_at
                )
                VALUES (?, ?, ?, ?, NULL, ?)
                """,
                (
                    session_id,
                    row["user_id"],
                    token_hash,
                    (now + self._session_lifetime).isoformat(
                        timespec="seconds"
                    ),
                    now.isoformat(timespec="seconds"),
                ),
            )

            user = AuthUser(
                user_id=row["user_id"],
                email=row["email"],
                tenant_id=row["tenant_id"],
                role=row["role"],
                allowed_tools=frozenset(
                    json.loads(row["allowed_tools_json"])
                ),
            )

        return token, user

    def authenticate_session(self, token: str) -> AuthUser | None:
        """Resolve a valid, unexpired session token."""

        import json

        if not token:
            return None

        token_hash = hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

        now = datetime.now(UTC).isoformat(timespec="seconds")

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    users.user_id,
                    users.email,
                    users.tenant_id,
                    users.role,
                    users.allowed_tools_json
                FROM sessions
                JOIN users ON users.user_id = sessions.user_id
                WHERE sessions.token_hash = ?
                  AND sessions.revoked_at IS NULL
                  AND sessions.expires_at > ?
                  AND users.is_active = 1
                """,
                (token_hash, now),
            ).fetchone()

        if row is None:
            return None

        return AuthUser(
            user_id=row["user_id"],
            email=row["email"],
            tenant_id=row["tenant_id"],
            role=row["role"],
            allowed_tools=frozenset(
                json.loads(row["allowed_tools_json"])
            ),
        )

    def revoke_session(self, token: str) -> bool:
        """Revoke a session. Return whether an active session was found."""

        if not token:
            return False

        token_hash = hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

        now = datetime.now(UTC).isoformat(timespec="seconds")

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE sessions
                SET revoked_at = ?
                WHERE token_hash = ?
                  AND revoked_at IS NULL
                """,
                (now, token_hash),
            )

        return cursor.rowcount == 1
