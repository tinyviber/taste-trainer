"""Concurrency contracts for the training job lock."""

import importlib
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


def _load_api_for_lock_tests(tempdir):
    """Import app.api with isolated settings without starting its server."""
    assets = Path(tempdir) / "training-assets"
    assets.mkdir()
    for filename in ("RUBRIC.md", "PROMPT_POOL.md", "submission-template.md"):
        (assets / filename).write_text("asset", encoding="utf-8")
    workspace = Path(tempdir) / "workspace"
    env = {
        "WORKSPACE_DIR": str(workspace),
        "TRAINING_ASSETS_DIR": str(assets),
        "AUTH_DB_PATH": str(Path(tempdir) / "auth.sqlite3"),
        "LLM_API_KEY": "test-key",
    }
    with patch.dict(os.environ, env, clear=False):
        return importlib.import_module("app.api")


class PerUserLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.api = _load_api_for_lock_tests(cls.tempdir.name)

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()
        # The module is deliberately removed so this contract test does not
        # leak its temporary settings into a test process that reuses imports.
        sys.modules.pop("app.api", None)

    def _run_serial(self, user_id, function):
        """The implementation contract is (user_id, fn, args)."""
        return self.api._run_serial(user_id, function, ())

    def test_same_user_jobs_are_serial(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []
        calls_lock = threading.Lock()

        def job():
            with calls_lock:
                calls.append("entered")
                first = len(calls) == 1
            if first:
                entered.set()
                self.assertTrue(release.wait(timeout=2))
            return len(calls)

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self._run_serial, "user-a", job)
                self.assertTrue(entered.wait(timeout=2))
                second = pool.submit(self._run_serial, "user-a", job)
                time.sleep(0.1)
                with calls_lock:
                    self.assertEqual(calls, ["entered"])
                release.set()
                first.result(timeout=2)
                second.result(timeout=2)
        finally:
            release.set()

    def test_different_users_do_not_share_a_lock(self):
        first_entered = threading.Event()
        second_entered = threading.Event()
        release = threading.Event()

        def job(user_id):
            if user_id == "user-a":
                first_entered.set()
                self.assertTrue(release.wait(timeout=2))
            else:
                second_entered.set()
            return user_id

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self._run_serial, "user-a", lambda: job("user-a"))
                self.assertTrue(first_entered.wait(timeout=2))
                second = pool.submit(self._run_serial, "user-b", lambda: job("user-b"))
                self.assertTrue(
                    second_entered.wait(timeout=0.5),
                    "a job for another user must not wait for user-a",
                )
                self.assertEqual(second.result(timeout=2), "user-b")
                release.set()
                self.assertEqual(first.result(timeout=2), "user-a")
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()
