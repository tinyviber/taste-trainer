import json
import tempfile
import unittest
from pathlib import Path

from scripts.migrate_legacy_workspace import migrate


class LegacyMigrationTests(unittest.TestCase):
    user_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    def test_dry_run_and_copy_exclude_plaintext_provider_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "videos").mkdir()
            (root / "videos" / "clip.mp4").write_bytes(b"video")
            (root / ".a2h").mkdir()
            (root / ".a2h" / "providers.json").write_text(
                json.dumps([{"apiKey": "must-not-copy"}]), encoding="utf-8"
            )
            (root / "RUBRIC.md").write_text("rubric", encoding="utf-8")

            report = migrate(root, self.user_id, dry_run=True)
            self.assertTrue(report["dry_run"])
            self.assertFalse((root / "users").exists())

            # The Node provider migration must have encrypted and deleted this
            # file before the workspace copier is allowed to finish.
            (root / ".a2h" / "providers.json").unlink()
            migrate(root, self.user_id)
            target = root / "users" / self.user_id
            self.assertEqual((target / "videos" / "clip.mp4").read_bytes(), b"video")
            self.assertEqual((target / "RUBRIC.md").read_text(), "rubric")
            self.assertFalse((target / ".a2h" / "providers.json").exists())
            self.assertFalse((root / ".a2h" / "providers.json").exists())

    def test_existing_target_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "users" / self.user_id
            target.mkdir(parents=True)
            (target / "sentinel").write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                migrate(root, self.user_id)
            self.assertEqual((target / "sentinel").read_text(), "keep")

    def test_remove_source_requires_provider_migration_first(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".a2h").mkdir()
            (root / ".a2h" / "providers.json").write_text("[]", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                migrate(root, self.user_id, remove_source=True)


if __name__ == "__main__":
    unittest.main()
