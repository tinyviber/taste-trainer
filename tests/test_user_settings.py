"""Contract tests for per-user default model settings."""

import tempfile
import unittest
from pathlib import Path

from app import user_settings


class UserSettingsTests(unittest.TestCase):
    def test_defaults_are_empty_and_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "user"
            self.assertEqual(
                user_settings.load(workspace),
                {"default_provider_id": "", "default_model_id": ""},
            )
            saved = user_settings.save(workspace, "provider-1", "model-1")
            self.assertEqual(user_settings.load(workspace), saved)
            self.assertEqual((workspace / user_settings.SETTINGS_FILENAME).stat().st_mode & 0o777, 0o600)

    def test_provider_and_model_must_be_set_together(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                user_settings.save(Path(temp), "provider-1", "")


if __name__ == "__main__":
    unittest.main()
