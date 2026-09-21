"""Contracts for targeted micro-v2 feedback and failure-safe retries."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import jobs, renders
from app.config import Settings


class MicroFeedbackTests(unittest.TestCase):
    def test_normalize_micro_feedback_uses_server_focus_and_safe_scores(self):
        from app.schemas import normalize_micro_feedback

        result = normalize_micro_feedback(
            {
                "target_dim": "模型偷偷改的维度",
                "v1_score": 99,
                "estimated_score": 8,
                "fixed": "已经把注意力预告提前了。",
                "remaining_gap": "释放仍然偏晚。",
                "next_step": "只调整释放时机。",
            },
            target_dim="注意力预告与释放",
            v1_score=4,
        )

        self.assertEqual(result["target_dim"], "注意力预告与释放")
        self.assertEqual(result["v1_score"], 4)
        self.assertGreaterEqual(result["estimated_score"], 0)
        self.assertLessEqual(result["estimated_score"], 10)
        for key in ("fixed", "remaining_gap", "next_step"):
            self.assertIsInstance(result[key], str)
            self.assertTrue(result[key].strip())

    def test_feedback_failure_keeps_micro_stage_and_hides_reference_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            (root / "training-assets").mkdir()
            (root / "training-assets" / "RUBRIC.md").write_text(
                "rubric", encoding="utf-8"
            )
            exercise = workspace / "exercises" / "exercise-1"
            exercise.mkdir(parents=True)
            (exercise / "meta.json").write_text(
                json.dumps(
                    {
                        "id": "exercise-1",
                        "title": "测试题",
                        "status": "needs_micro_revision",
                        "score": 40,
                        "weakest": "注意力预告与释放",
                        "micro_revision_focus": {
                            "dim": "注意力预告与释放",
                            "score": 4,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (exercise / "grade.json").write_text(
                json.dumps(
                    {
                        "total": 40,
                        "revision": {"rows": [], "changes": []},
                    }
                ),
                encoding="utf-8",
            )
            settings = Settings(
                workspace=workspace,
                training_assets_dir=root / "training-assets",
                auth_db_path=root / "auth.sqlite3",
                llm_base_url="https://example.invalid/v1",
                llm_api_key="test-key",
                llm_model="test-model",
                llm_vision_model="test-model",
            )

            with patch.object(jobs, "_client", return_value=object()), patch.object(
                jobs,
                "chat_json",
                side_effect=RuntimeError("provider unavailable"),
            ):
                with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
                    jobs.complete_micro_revision(settings, "exercise-1", "局部改写")

            metadata = renders.load_meta(exercise)
            self.assertEqual(metadata["status"], "needs_micro_revision")
            self.assertFalse((exercise / "revision.md").exists())
            self.assertFalse((exercise / "micro_feedback.json").exists())
            self.assertFalse((exercise / "micro_feedback.md").exists())

    def test_feedback_success_persists_targeted_feedback_before_reviewed_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            assets = root / "training-assets"
            assets.mkdir()
            for filename in ("RUBRIC.md", "PROMPT_POOL.md", "submission-template.md"):
                (assets / filename).write_text("asset", encoding="utf-8")
            exercise = workspace / "exercises" / "exercise-1"
            exercise.mkdir(parents=True)
            (exercise / "meta.json").write_text(
                json.dumps({
                    "id": "exercise-1",
                    "title": "测试题",
                    "status": "needs_micro_revision",
                    "score": 40,
                    "micro_revision_focus": {
                        "dim": "注意力预告与释放",
                        "score": 4,
                        "original": "没有提前预告",
                        "gap": "释放太突然",
                    },
                }, ensure_ascii=False), encoding="utf-8"
            )
            (exercise / "grade.json").write_text(
                json.dumps({"revision": {"rows": [], "changes": []}}),
                encoding="utf-8",
            )
            settings = Settings(
                workspace=workspace,
                training_assets_dir=assets,
                auth_db_path=root / "auth.sqlite3",
                llm_base_url="https://example.invalid/v1",
                llm_api_key="test-key",
                llm_model="test-model",
                llm_vision_model="test-model",
            )
            with patch.object(jobs, "_client", return_value=object()), patch.object(
                jobs, "_model", return_value="test-model"
            ), patch.object(jobs, "chat_json", return_value={
                "estimated_score": 7,
                "fixed": "加入了声音预告。",
                "remaining_gap": "释放仍可更精确。",
                "next_step": "只调整释放时机。",
            }), patch.object(jobs.manifest, "regenerate"):
                result = jobs.complete_micro_revision(settings, "exercise-1", "先听见声音，再看到物体")

            self.assertEqual(result["status"], "reviewed")
            feedback = json.loads((exercise / "micro_feedback.json").read_text(encoding="utf-8"))
            self.assertEqual(feedback["target_dim"], "注意力预告与释放")
            self.assertEqual(feedback["v1_score"], 4)
            self.assertIn("加入了声音预告", (exercise / "micro_feedback.md").read_text(encoding="utf-8"))
            self.assertTrue((exercise / "revision.md").exists())


if __name__ == "__main__":
    unittest.main()
