"""Deterministic media pipeline: ffmpeg/ffprobe wrappers.

Produces (under runs/<slug>/):
  frames/f_%03d.jpg   — native-res frames for keyframe evidence
  small/f_%03d.jpg    — 640w frames sent to the vision model
  sheets/sheet_%02d.jpg — tiled contact sheets (quick read-through)
  audio.wav           — optional, when the model reports dialogue
"""
import json
import re
import subprocess
from pathlib import Path


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def probe_duration(ffmpeg: str, ffprobe: str, video: Path) -> float:
    """Duration in seconds. ffprobe preferred, ffmpeg -i stderr as fallback."""
    r = _run([ffprobe, "-v", "error", "-show_entries", "format=duration",
              "-of", "json", str(video)])
    if r.returncode == 0:
        try:
            return float(json.loads(r.stdout)["format"]["duration"])
        except (KeyError, ValueError, json.JSONDecodeError):
            pass
    r = _run([ffmpeg, "-i", str(video)])
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        raise RuntimeError(f"cannot read duration of {video}")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def extract_frames(ffmpeg: str, video: Path, out_dir: Path,
                   small_dir: Path, fps: float = 1.0, small_w: int = 640) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    small_dir.mkdir(parents=True, exist_ok=True)
    r = _run([ffmpeg, "-y", "-i", str(video), "-vf", f"fps={fps}",
              "-q:v", "3", str(out_dir / "f_%03d.jpg")])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg frames failed: {r.stderr[-400:]}")
    r = _run([ffmpeg, "-y", "-i", str(video), "-vf",
              f"fps={fps},scale={small_w}:-1", "-q:v", "5",
              str(small_dir / "f_%03d.jpg")])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg small frames failed: {r.stderr[-400:]}")
    return sorted(out_dir.glob("f_*.jpg"))


def make_sheets(ffmpeg: str, video: Path, out_dir: Path, fps: float = 1.0,
                tile: str = "2x2", w: int = 480) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    r = _run([ffmpeg, "-y", "-i", str(video), "-vf",
              f"fps={fps},scale={w}:-1,tile={tile}", "-q:v", "4",
              str(out_dir / "sheet_%02d.jpg")])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg sheets failed: {r.stderr[-400:]}")
    return sorted(out_dir.glob("sheet_*.jpg"))


def extract_audio(ffmpeg: str, video: Path, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    r = _run([ffmpeg, "-y", "-i", str(video), "-vn", "-ac", "1", str(out_path)])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg audio failed: {r.stderr[-400:]}")
    return out_path
