"""Local, closed-account authentication for the public dashboard.

The application deliberately has no self-service registration endpoint.  User
records and sessions live in a small SQLite database; session cookies contain
opaque random values and the database only stores their hashes.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4


PASSWORD_MIN_LENGTH = 12
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", str(30 * 24 * 60 * 60)))
SESSION_COOKIE = "trainer_session"
CSRF_HEADER = "X-CSRF-Token"


@dataclass(frozen=True)
class User:
    id: str
    username: str


@dataclass(frozen=True)
class Session:
    token: str
    csrf_token: str
    user: User
    expires_at: int


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


@contextmanager
def _db(db_path: Path):
    connection = _connect(Path(db_path))
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db(db_path: Path) -> None:
    """Create the auth schema idempotently and restrict the database file."""
    db_path = Path(db_path)
    with _db(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                csrf_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                revoked_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);
            """
        )
    try:
        os.chmod(db_path, 0o600)
    except FileNotFoundError:
        pass


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"密码至少需要 {PASSWORD_MIN_LENGTH} 个字符")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt$16384$8$1${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=_unb64(salt),
            n=int(n), r=int(r), p=int(p),
        )
        return hmac.compare_digest(digest, _unb64(expected))
    except (TypeError, ValueError, UnicodeError):
        return False


def _row_user(row: sqlite3.Row | None) -> User | None:
    return User(str(row["id"]), str(row["username"])) if row else None


def get_user(db_path: Path, *, username: str | None = None,
             user_id: str | None = None) -> User | None:
    init_db(Path(db_path))
    if username is None and user_id is None:
        raise ValueError("username or user_id is required")
    with _db(Path(db_path)) as connection:
        if username is not None:
            row = connection.execute(
                "SELECT id, username FROM users WHERE username = ? COLLATE NOCASE",
                (username.strip(),),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT id, username FROM users WHERE id = ?", (user_id,)
            ).fetchone()
    return _row_user(row)


def get_user_with_hash(db_path: Path, username: str) -> tuple[User, str] | None:
    init_db(Path(db_path))
    with _db(Path(db_path)) as connection:
        row = connection.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ? COLLATE NOCASE",
            (username.strip(),),
        ).fetchone()
    if not row:
        return None
    return User(str(row["id"]), str(row["username"])), str(row["password_hash"])


def create_user(db_path: Path, username: str, password: str | None = None,
                *, user_id: str | None = None) -> tuple[User, str | None]:
    """Create one closed-account user; never overwrite an existing account."""
    username = username.strip()
    if not username or len(username) > 64:
        raise ValueError("用户名无效")
    generated = password is None
    password = password or secrets.token_urlsafe(24)
    encoded = hash_password(password)
    now = int(time.time())
    user = User(user_id or str(uuid4()), username)
    init_db(Path(db_path))
    try:
        with _db(Path(db_path)) as connection:
            connection.execute(
                "INSERT INTO users (id, username, password_hash, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user.id, user.username, encoded, now, now),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"用户已存在：{username}") from exc
    return user, password if generated else None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db_path: Path, user_id: str) -> Session:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(24)
    now = int(time.time())
    expires_at = now + SESSION_TTL_SECONDS
    init_db(Path(db_path))
    with _db(Path(db_path)) as connection:
        row = connection.execute(
            "SELECT id, username FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            raise ValueError("user not found")
        connection.execute(
            "INSERT INTO sessions (token_hash, user_id, csrf_hash, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (_token_hash(token), user_id, _token_hash(csrf_token), now, expires_at),
        )
    return Session(token, csrf_token, User(str(row["id"]), str(row["username"])), expires_at)


def resolve_session(db_path: Path, token: str | None) -> Session | None:
    if not token:
        return None
    init_db(Path(db_path))
    now = int(time.time())
    with _db(Path(db_path)) as connection:
        row = connection.execute(
            "SELECT s.token_hash, s.csrf_hash, s.expires_at, u.id, u.username "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ?",
            (_token_hash(token), now),
        ).fetchone()
    if not row:
        return None
    # The raw CSRF token is intentionally never persisted.  Callers validate
    # it through verify_csrf; this placeholder keeps Session useful for auth
    # identity while preventing accidental token disclosure.
    return Session(token, "", User(str(row["id"]), str(row["username"])), int(row["expires_at"]))


def verify_csrf(db_path: Path, token: str, csrf_token: str | None) -> bool:
    if not token or not csrf_token:
        return False
    init_db(Path(db_path))
    with _db(Path(db_path)) as connection:
        row = connection.execute(
            "SELECT csrf_hash FROM sessions WHERE token_hash = ? AND revoked_at IS NULL "
            "AND expires_at > ?",
            (_token_hash(token), int(time.time())),
        ).fetchone()
    return bool(row and hmac.compare_digest(str(row["csrf_hash"]), _token_hash(csrf_token)))


def revoke_session(db_path: Path, token: str | None) -> None:
    if not token:
        return
    init_db(Path(db_path))
    with _db(Path(db_path)) as connection:
        connection.execute(
            "UPDATE sessions SET revoked_at = ? WHERE token_hash = ?",
            (int(time.time()), _token_hash(token)),
        )


def revoke_user_sessions(db_path: Path, user_id: str) -> None:
    init_db(Path(db_path))
    with _db(Path(db_path)) as connection:
        connection.execute(
            "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (int(time.time()), user_id),
        )


def change_password(db_path: Path, user_id: str, current_password: str,
                    new_password: str) -> Session:
    record = None
    init_db(Path(db_path))
    with _db(Path(db_path)) as connection:
        record = connection.execute(
            "SELECT password_hash FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if not record or not verify_password(current_password, str(record["password_hash"])):
        raise ValueError("当前密码不正确")
    password_hash = hash_password(new_password)
    now = int(time.time())
    with _db(Path(db_path)) as connection:
        connection.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (password_hash, now, user_id),
        )
    revoke_user_sessions(db_path, user_id)
    return create_session(db_path, user_id)


def user_workspace(root: Path, user_id: str) -> Path:
    """Return a workspace for an authenticated immutable UUID only."""
    try:
        UUID(str(user_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("invalid user id") from exc
    return Path(root).resolve() / "users" / str(user_id)


_RATE_LOCK = threading.Lock()
_RATE_ATTEMPTS: dict[str, list[float]] = {}


def allow_rate(key: str, *, limit: int, window_seconds: int) -> bool:
    now = time.monotonic()
    with _RATE_LOCK:
        attempts = [stamp for stamp in _RATE_ATTEMPTS.get(key, [])
                    if now - stamp < window_seconds]
        allowed = len(attempts) < limit
        attempts.append(now)
        _RATE_ATTEMPTS[key] = attempts
        if len(_RATE_ATTEMPTS) > 2048:
            for candidate, values in list(_RATE_ATTEMPTS.items()):
                if not values or now - values[-1] >= window_seconds:
                    _RATE_ATTEMPTS.pop(candidate, None)
        return allowed


def clear_rate(key: str) -> None:
    with _RATE_LOCK:
        _RATE_ATTEMPTS.pop(key, None)


def _default_db_path() -> Path:
    workspace = Path(os.environ.get("WORKSPACE_DIR", "workspace")).expanduser()
    return Path(os.environ.get("AUTH_DB_PATH", workspace / "auth.sqlite3")).expanduser()


def main() -> None:
    parser = argparse.ArgumentParser(description="Taste Trainer closed-account administration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-wj", help="create the initial wj account")
    create.add_argument("--db", type=Path, default=_default_db_path())
    args = parser.parse_args()
    if args.command == "create-wj":
        user, password = create_user(args.db, "wj")
        print(f"username: {user.username}")
        print(f"password: {password}")
        print("请立即保存密码；服务端只保存不可逆 hash。")


if __name__ == "__main__":
    main()
