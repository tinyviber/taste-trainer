"""Contract tests for the future ``app.auth`` module.

These tests intentionally stay at the small public boundary needed by the
application: a SQLite database path, user credentials, and opaque sessions.
Until the module exists, the contract is skipped so the existing test suite
remains runnable during the staged implementation.
"""

import importlib
import sqlite3
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path


try:
    auth = importlib.import_module("app.auth")
except ModuleNotFoundError as exc:
    if exc.name == "app.auth":
        auth = None
    else:
        raise


REQUIRED_FUNCTIONS = (
    "create_user",
    "verify_password",
    "create_session",
    "revoke_session",
)
SESSION_LOOKUP = (
    getattr(auth, "get_session", None)
    if auth is not None
    else None
) or (
    getattr(auth, "resolve_session", None)
    if auth is not None
    else None
)
AUTH_CONTRACT_AVAILABLE = auth is not None and all(
    hasattr(auth, name) for name in REQUIRED_FUNCTIONS
) and SESSION_LOOKUP is not None


def _field(value, *names):
    """Read an id/hash field without requiring a particular record type."""
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    if isinstance(value, (tuple, list)):
        for item in value:
            found = _field(item, *names)
            if found is not None:
                return found
    return None


def _session_token(value):
    if isinstance(value, str):
        return value
    token = _field(value, "token", "session_token")
    if not isinstance(token, str):
        raise AssertionError("create_session must return an opaque session token")
    return token


@unittest.skipUnless(
    AUTH_CONTRACT_AVAILABLE,
    "pending app.auth contract implementation",
)
class AuthContractTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "auth.sqlite3"

    def tearDown(self):
        self.tempdir.cleanup()

    def _create_user(self, password="correct horse battery staple"):
        created = auth.create_user(self.db_path, "wj", password)
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT id FROM users WHERE username = ?", ("wj",)
            ).fetchone()
        self.assertIsNotNone(row, "create_user must persist the user in SQLite")
        user_id = row[0]
        return created, user_id

    def _stored_hash(self, created):
        del created
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT password_hash FROM users WHERE username = ?",
                ("wj",),
            ).fetchone()
        self.assertIsNotNone(row, "users must contain a password_hash column")
        return row[0]

    def _database_bytes(self):
        """Include SQLite sidecar files if the implementation uses WAL mode."""
        files = list(self.db_path.parent.glob(self.db_path.name + "*"))
        return b"".join(path.read_bytes() for path in files if path.is_file())

    def test_create_user_stores_a_hash_and_not_the_plaintext(self):
        password = "wj-only test password"
        created, _ = self._create_user(password)
        password_hash = self._stored_hash(created)

        self.assertIsInstance(password_hash, str)
        self.assertNotEqual(password_hash, password)
        self.assertNotIn(password.encode("utf-8"), self._database_bytes())
        self.assertTrue(auth.verify_password(password, password_hash))
        self.assertFalse(auth.verify_password("wrong password", password_hash))

    def test_create_user_can_generate_an_initial_password(self):
        created = auth.create_user(self.db_path, "wj")
        generated_password = _field(created, "password")
        if generated_password is None and isinstance(created, (tuple, list)):
            generated_password = next(
                (item for item in created[1:] if isinstance(item, str)), None
            )
        self.assertIsInstance(generated_password, str)
        self.assertTrue(generated_password)
        self.assertNotEqual(generated_password, "wj")
        self.assertTrue(
            auth.verify_password(generated_password, self._stored_hash(created))
        )

    def test_verify_password_rejects_a_different_password(self):
        created, _ = self._create_user()
        password_hash = self._stored_hash(created)

        self.assertTrue(auth.verify_password("correct horse battery staple", password_hash))
        self.assertFalse(auth.verify_password("not the password", password_hash))

    def test_create_get_and_revoke_session(self):
        _, user_id = self._create_user()
        token = _session_token(auth.create_session(self.db_path, user_id))

        self.assertIsInstance(token, str)
        self.assertTrue(token)
        session = SESSION_LOOKUP(self.db_path, token)
        self.assertIsNotNone(session)
        session_user = _field(session, "user", "id", "user_id", "username")
        session_user_id = _field(session_user, "id", "user_id") or session_user
        self.assertEqual(session_user_id, user_id)

        auth.revoke_session(self.db_path, token)
        self.assertIsNone(SESSION_LOOKUP(self.db_path, token))

    def test_password_change_invalidates_existing_session(self):
        """Password changes must revoke old sessions, not only replace a hash."""
        change_password = getattr(auth, "change_password", None)
        if change_password is None:
            self.skipTest(
                "password-change function name/signature is not part of the "
                "named auth contract yet"
            )

        _, user_id = self._create_user("old password")
        token = _session_token(auth.create_session(self.db_path, user_id))
        change_password(
            self.db_path, user_id, "old password", "new secure password"
        )

        self.assertIsNone(SESSION_LOOKUP(self.db_path, token))

    def test_registration_is_not_a_public_auth_operation(self):
        self.assertFalse(hasattr(auth, "register"))
        self.assertFalse(hasattr(auth, "register_user"))


if __name__ == "__main__":
    unittest.main()
