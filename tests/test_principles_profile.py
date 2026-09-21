"""Contracts for the durable principles ledger and compact active profile."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import jobs, renders
from app.config import Settings, ws_dirs
from app.principles import save_candidates, state, update_candidate


def _settings(root: Path) -> Settings:
    return Settings(
        workspace=root / "workspace",
        training_assets_dir=root / "training-assets",
        auth_db_path=root / "auth.sqlite3",
        llm_base_url="https://example.invalid/v1",
        llm_api_key="test-key",
        llm_model="test-model",
        llm_vision_model="test-model",
    )


def _add_candidate(dirs: dict, name: str, title: str, detail: str) -> str:
    path = dirs["analyses"] / name / "principles_candidates.json"
    save_candidates(
        path,
        f"{name}.mp4",
        f"analyses/{name}/分析.md",
        [{"title": title, "detail": detail, "evidence": []}],
    )
    candidate_id = f"{name}-p1"
    update_candidate(dirs, candidate_id, "accept")
    return candidate_id


class PrinciplesProfileTests(unittest.TestCase):
    def test_schema_one_migrates_to_schema_two_without_losing_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            dirs = ws_dirs(workspace)
            dirs["principles_meta"].parent.mkdir(parents=True)
            legacy = [
                {
                    "id": "old-1",
                    "title": "先给承诺",
                    "detail": "先让观众知道要等什么。",
                    "source_video": "old.mp4",
                    "accepted_at": "2026-01-01T00:00:00",
                    "status": "accepted",
                },
                {
                    "id": "old-2",
                    "title": "释放信息",
                    "detail": "兑现前面给出的承诺。",
                    "source_video": "old.mp4",
                    "accepted_at": "2026-01-02T00:00:00",
                },
            ]
            dirs["principles_meta"].write_text(
                json.dumps({"schema_version": 1, "principles": legacy}, ensure_ascii=False),
                encoding="utf-8",
            )

            snapshot = state(dirs)
            migrated = json.loads(dirs["principles_meta"].read_text(encoding="utf-8"))

            self.assertEqual(migrated["schema_version"], 2)
            self.assertEqual(
                {item["id"] for item in migrated["principles"]},
                {"old-1", "old-2"},
            )
            self.assertEqual(
                set(migrated["active_ids"]),
                {"old-1", "old-2"},
            )
            self.assertEqual(
                {item["id"] for item in snapshot["ledger"]},
                {"old-1", "old-2"},
            )
            self.assertIn("先给承诺", dirs["active_profile"].read_text(encoding="utf-8"))
            self.assertIn("释放信息", dirs["active_profile"].read_text(encoding="utf-8"))

    def test_active_profile_deduplicates_but_keeps_the_full_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            dirs = ws_dirs(Path(temp))
            _add_candidate(dirs, "video-a", "同一原则", "先给承诺，再释放信息。")
            _add_candidate(dirs, "video-b", "同一原则", "先给承诺，再释放信息。")

            ledger = json.loads(dirs["principles_meta"].read_text(encoding="utf-8"))
            profile = dirs["active_profile"].read_text(encoding="utf-8")

            self.assertGreaterEqual(len(ledger["principles"]), 2)
            self.assertEqual(profile.count("同一原则"), 1)
            self.assertLessEqual(len(ledger["active_ids"]), 40)

    def test_active_profile_is_bounded_without_truncating_the_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            dirs = ws_dirs(Path(temp))
            for index in range(45):
                _add_candidate(
                    dirs,
                    f"video-{index:02d}",
                    f"原则 {index:02d}",
                    f"这是第 {index:02d} 条不同的训练原则。",
                )

            ledger = json.loads(dirs["principles_meta"].read_text(encoding="utf-8"))
            profile = dirs["active_profile"].read_text(encoding="utf-8")
            active_headings = [line for line in profile.splitlines() if line.startswith("### ")]

            self.assertEqual(len(ledger["principles"]), 45)
            self.assertLessEqual(len(ledger["active_ids"]), 40)
            self.assertLessEqual(len(active_headings), 40)
            self.assertIn("原则 44", profile)

    def test_grading_reads_active_profile_not_historical_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = _settings(root)
            settings.training_assets_dir.mkdir(parents=True)
            (settings.training_assets_dir / "RUBRIC.md").write_text(
                "rubric", encoding="utf-8"
            )
            workspace = settings.workspace
            exercise = workspace / "exercises" / "exercise-1"
            exercise.mkdir(parents=True)
            (exercise / "meta.json").write_text(
                json.dumps(
                    {
                        "id": "exercise-1",
                        "title": "测试题",
                        "status": "prompted",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (exercise / "prompt.md").write_text("题目", encoding="utf-8")
            dirs = ws_dirs(workspace, settings.training_assets_dir)
            dirs["principles"].parent.mkdir(parents=True, exist_ok=True)
            dirs["principles"].write_text("HISTORICAL_LEDGER_MARKER", encoding="utf-8")
            dirs["principles_meta"].write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "principles": [
                            {
                                "id": "active-1",
                                "title": "当前原则",
                                "detail": "ACTIVE_PROFILE_MARKER",
                                "status": "active",
                                "source_video": "active.mp4",
                            }
                        ],
                        "active_ids": ["active-1"],
                        "events": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            captured = {}

            def fake_chat_json(_client, _model, prompt, **_kwargs):
                captured["prompt"] = prompt
                return {
                    "total": 0,
                    "scores": [
                        {"dim": dim, "score": 0, "evidence": ""}
                        for dim in renders.DIMS
                    ],
                    "weakest": [
                        {"dim": renders.DIMS[0]},
                        {"dim": renders.DIMS[1]},
                    ],
                }

            with patch.object(jobs, "_client", return_value=object()), patch.object(
                jobs, "_model", return_value="test-model"
            ), patch.object(jobs, "chat_json", side_effect=fake_chat_json), patch.object(
                jobs.manifest, "regenerate"
            ):
                jobs.grade(settings, "exercise-1", "我的方案")

            self.assertIn("ACTIVE_PROFILE_MARKER", captured["prompt"])
            self.assertNotIn("HISTORICAL_LEDGER_MARKER", captured["prompt"])


if __name__ == "__main__":
    unittest.main()
