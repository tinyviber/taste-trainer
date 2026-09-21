"""Contract tests for user-scoped workspace paths."""

import importlib
import tempfile
import unittest
from pathlib import Path


try:
    auth = importlib.import_module("app.auth")
except ModuleNotFoundError as exc:
    if exc.name == "app.auth":
        auth = None
    else:
        raise
config = importlib.import_module("app.config")
workspace_owner = auth if auth is not None and hasattr(auth, "user_workspace") else config
USER_WORKSPACE_AVAILABLE = hasattr(workspace_owner, "user_workspace")


@unittest.skipUnless(
    USER_WORKSPACE_AVAILABLE,
    "pending app.config.user_workspace contract implementation",
)
class UserWorkspaceIsolationTests(unittest.TestCase):
    def test_different_user_ids_get_different_scoped_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = workspace_owner.user_workspace(
                root, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            )
            second = workspace_owner.user_workspace(
                root, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
            )

            self.assertIsInstance(first, Path)
            self.assertIsInstance(second, Path)
            self.assertNotEqual(first, second)
            self.assertTrue(root.resolve() in first.resolve().parents)
            self.assertTrue(root.resolve() in second.resolve().parents)

    def test_user_id_cannot_escape_the_workspace_or_be_used_as_a_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for unsafe_id in (
                "../outside",
                "..",
                "nested/user",
                "/tmp/outside",
                r"..\outside",
                "wj",
            ):
                with self.subTest(unsafe_id=unsafe_id):
                    with self.assertRaises((TypeError, ValueError)):
                        workspace_owner.user_workspace(root, unsafe_id)


if __name__ == "__main__":
    unittest.main()
