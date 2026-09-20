import tempfile
import unittest
from pathlib import Path

from app import manifest
from app.config import safe_child, ws_dirs
from app.pipeline import frame_manifest
from app.principles import save_candidates, state, update_candidate
from app.renders import DIMS
from app.schemas import normalize_grade


class CoreInvariantTests(unittest.TestCase):
    def test_frame_manifest_uses_real_timestamp(self):
        frames = [Path("f_001.jpg"), Path("f_012.jpg")]
        result = frame_manifest(frames, fps=0.5)
        self.assertEqual(result[1]["frame"], "f_012")
        self.assertEqual(result[1]["seconds"], 22.0)
        self.assertEqual(result[1]["time"], "00:00:22")

    def test_grade_total_is_computed_and_dimensions_are_canonical(self):
        data = {
            "total": 0,
            "scores": [
                {"dim": dim, "score": index, "evidence": "e"}
                for index, dim in enumerate(reversed(DIMS))
            ],
            "weakest": [{"dim": DIMS[0]}, {"dim": DIMS[1]}],
        }
        result = normalize_grade(data)
        self.assertEqual([row["dim"] for row in result["scores"]], DIMS)
        self.assertEqual(result["total"], sum(range(10)))

    def test_principles_need_human_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            ws = Path(temp)
            dirs = ws_dirs(ws)
            candidate_path = dirs["analyses"] / "video-1" / "principles_candidates.json"
            save_candidates(candidate_path, "clip.mp4", "analyses/video-1/分析.md", [
                {"title": "先给承诺", "detail": "先让观众知道要等什么。",
                 "evidence": [{"frame": "f_001", "time": "00:00:00"}]},
            ])
            self.assertEqual(len(state(dirs)["pending"]), 1)
            update_candidate(dirs, "video-1-p1", "accept")
            current = state(dirs)
            self.assertEqual(len(current["pending"]), 0)
            self.assertEqual(len(current["accepted"]), 1)
            self.assertIn("先给承诺", dirs["principles"].read_text(encoding="utf-8"))
            manifest_path = manifest.regenerate(ws, dirs)
            self.assertIn("analyses/PRINCIPLES.md", manifest_path.read_text(encoding="utf-8"))
            update_candidate(dirs, "video-1-p1", "reject")
            self.assertEqual(len(state(dirs)["accepted"]), 0)
            self.assertNotIn("先给承诺", dirs["principles"].read_text(encoding="utf-8"))

    def test_safe_child_rejects_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            root.mkdir()
            with self.assertRaises(ValueError):
                safe_child(root, "../outside")


if __name__ == "__main__":
    unittest.main()
