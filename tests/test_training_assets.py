"""Contracts for application-owned training assets and user workspaces."""

import tempfile
import unittest
from pathlib import Path

from app.config import TRAINING_ASSET_FILES, validate_training_assets, ws_dirs


class TrainingAssetsTests(unittest.TestCase):
    def test_missing_training_asset_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as temp:
            assets = Path(temp)
            for filename in TRAINING_ASSET_FILES.values():
                if filename != "submission-template.md":
                    (assets / filename).write_text("asset", encoding="utf-8")

            with self.assertRaises(SystemExit) as raised:
                validate_training_assets(assets)

            self.assertIn("submission-template.md", str(raised.exception))
            self.assertIn(str(assets), str(raised.exception))

    def test_ws_dirs_keeps_training_assets_outside_user_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace" / "users" / "user-a"
            assets = root / "training-assets"
            dirs = ws_dirs(workspace, assets)

            self.assertEqual(dirs["training_assets"], assets.resolve())
            self.assertEqual(dirs["rubric"], (assets / "RUBRIC.md").resolve())
            self.assertEqual(dirs["pool"], (assets / "PROMPT_POOL.md").resolve())
            self.assertEqual(
                dirs["submission_template"],
                (assets / "submission-template.md").resolve(),
            )

            for name in (
                "videos",
                "analyses",
                "exercises",
                "a2h",
                "runs_meta",
                "index",
                "principles",
                "active_profile",
                "principles_meta",
            ):
                with self.subTest(name=name):
                    self.assertTrue(
                        dirs[name].resolve() == workspace.resolve()
                        or workspace.resolve() in dirs[name].resolve().parents,
                        f"{name} must remain user-scoped",
                    )


if __name__ == "__main__":
    unittest.main()
