"""Deterministic media pipeline: ffmpeg/ffprobe wrappers.

Produces (under runs/<slug>/):
  frames/f_%03d.jpg   — native-res frames for keyframe evidence
  small/f_%03d.jpg    — 640w frames sent to the vision model
  sheets/sheet_%02d.jpg — tiled contact sheets (quick read-through)
"""
import json
import re
import subprocess
from pathlib import Path


def _run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            cmd, 124, stdout=exc.stdout or "", stderr=f"command timed out after {timeout}s")
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, stdout="", stderr=str(exc))


def _label_filter(fontsize: int = 22) -> str:
    """Burn a stable frame id and the actual presentation timestamp into images."""
    # drawtext's colons must be escaped inside %{...} expressions.  ``n`` is
    # the output-frame index after the fps filter, so it matches f_%03d.jpg.
    return (
        "drawtext=fontcolor=white:"
        f"fontsize={fontsize}:box=1:boxcolor=black@0.70:x=10:y=10:"
        r"text='f_%{eif\:n+1\:d\:3} %{pts\:hms}'"
    )


def format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS, retaining milliseconds when useful."""
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    if millis:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def frame_manifest(frames: list[Path], fps: float) -> list[dict]:
    """Return the exact id → timestamp mapping used by the extracted frames."""
    out = []
    for frame in frames:
        match = re.fullmatch(r"f_(\d+)", frame.stem)
        if not match:
            continue
        number = int(match.group(1))
        seconds = (number - 1) / fps
        out.append({
            "frame": frame.stem,
            "seconds": seconds,
            "time": format_timestamp(seconds),
        })
    return out


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
    label = _label_filter()
    r = _run([ffmpeg, "-y", "-i", str(video), "-vf", f"fps={fps},{label}",
              "-q:v", "3", str(out_dir / "f_%03d.jpg")])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg frames failed: {r.stderr[-400:]}")
    small_label = _label_filter(fontsize=18)
    r = _run([ffmpeg, "-y", "-i", str(video), "-vf",
              f"fps={fps},scale={small_w}:-1,{small_label}", "-q:v", "5",
              str(small_dir / "f_%03d.jpg")])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg small frames failed: {r.stderr[-400:]}")
    return sorted(out_dir.glob("f_*.jpg"))


def make_sheets(ffmpeg: str, video: Path, out_dir: Path, fps: float = 1.0,
                tile: str = "2x2", w: int = 480) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    label = _label_filter(fontsize=18)
    r = _run([ffmpeg, "-y", "-i", str(video), "-vf",
              f"fps={fps},scale={w}:-1,{label},tile={tile}", "-q:v", "4",
              str(out_dir / "sheet_%02d.jpg")])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg sheets failed: {r.stderr[-400:]}")
    return sorted(out_dir.glob("sheet_*.jpg"))
