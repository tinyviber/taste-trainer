import json
import tempfile
import unittest
from pathlib import Path

from scripts.migrate_legacy_workspace import migrate


class LegacyMigrationTests(unittest.TestCase):
    user_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    def test_migration_preserves_runs_but_excludes_shared_assets_and_template(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "videos").mkdir()
            (root / "videos" / "clip.mp4").write_bytes(b"video")
            (root / "exercises" / "2026-01-01-example").mkdir(parents=True)
            (root / "exercises" / "2026-01-01-example" / "meta.json").write_text(
                "{}", encoding="utf-8"
            )
            (root / "exercises" / "_template").mkdir(parents=True)
            (root / "exercises" / "_template" / "submission.md").write_text(
                "shared template", encoding="utf-8"
            )
            (root / ".a2h").mkdir()
            (root / ".a2h" / "runs").mkdir()
            (root / ".a2h" / "runs" / "old-job.json").write_text(
                '{"runs": []}', encoding="utf-8"
            )
            (root / ".a2h" / "providers.json").write_text(
                json.dumps([{"apiKey": "must-not-copy"}]), encoding="utf-8"
            )
            (root / "RUBRIC.md").write_text("rubric", encoding="utf-8")
            (root / "PROMPT_POOL.md").write_text("pool", encoding="utf-8")

            report = migrate(root, self.user_id, dry_run=True)
            self.assertTrue(report["dry_run"])
            self.assertFalse((root / "users").exists())

            # The Node provider migration must have encrypted and deleted this
            # file before the workspace copier is allowed to finish.
            (root / ".a2h" / "providers.json").unlink()
            migrate(root, self.user_id)
            target = root / "users" / self.user_id
            self.assertEqual((target / "videos" / "clip.mp4").read_bytes(), b"video")
            self.assertTrue((target / "exercises" / "2026-01-01-example" / "meta.json").exists())
            self.assertEqual(
                (target / ".a2h" / "runs" / "old-job.json").read_text(),
                '{"runs": []}',
            )
            self.assertFalse((target / "exercises" / "_template").exists())
            self.assertFalse((target / "RUBRIC.md").exists())
            self.assertFalse((target / "PROMPT_POOL.md").exists())
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
