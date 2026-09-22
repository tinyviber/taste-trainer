import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import jobs
from app.config import ws_dirs


class FormatSubmissionTests(unittest.TestCase):
    def test_formats_without_overwriting_the_saved_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            dirs = ws_dirs(workspace)
            exercise = dirs["exercises"] / "exercise-1"
            exercise.mkdir(parents=True)
            (dirs["template"]).mkdir(parents=True)
            (dirs["template"] / "submission.md").write_text(
                "## 分镜表\n\n| 时间 | 画面 | 镜头职能 | 注意力意图 |\n",
                encoding="utf-8",
            )
            (exercise / "meta.json").write_text(
                '{"id":"exercise-1","title":"雨夜便利店","status":"prompted"}',
                encoding="utf-8",
            )
            saved = "原来的草稿"
            (exercise / "submission.md").write_text(saved, encoding="utf-8")
            settings = SimpleNamespace(
                workspace=workspace,
                user_id="",
                default_provider_id="",
                default_model_id="",
                llm_base_url="",
                llm_api_key="",
                llm_model="test-model",
                llm_vision_model="test-model",
            )

            with patch.object(jobs, "_client", return_value=object()), \
                    patch.object(jobs, "_model", return_value="test-model"), \
                    patch.object(jobs, "chat_json", return_value={
                        "submission": "## 分镜表\n\n整理后的内容"
                    }) as chat:
                result = jobs.format_submission(settings, "exercise-1", "雨很大，先拍招牌")

            self.assertEqual(result, "## 分镜表\n\n整理后的内容")
            self.assertEqual((exercise / "submission.md").read_text(encoding="utf-8"), saved)
            self.assertIn("雨很大，先拍招牌", chat.call_args.kwargs["user"])
            self.assertIn("雨夜便利店", chat.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
