"""The three LLM jobs: 出题 / 打分 / 分析视频.

Each job writes a run record to .a2h/runs/<job>.json so progress is visible
in the A2H viewer, produces markdown artifacts via renders.py, and leaves
structured data in meta.json for INDEX.md and the manifest to regenerate.
"""
import json
import re
import shutil
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from . import manifest, pipeline, renders
from .config import Settings, safe_child, ws_dirs
from .llm import chat_json
from .principles import save_candidates
from .schemas import normalize_grade


def _write_run(dirs: dict, job_id: str, title: str, status: str,
               summary: str = "", artifacts: list | None = None) -> None:
    dirs["runs_meta"].mkdir(parents=True, exist_ok=True)
    spec = {"runs": [{
        "id": job_id, "title": title, "status": status,
        "summary": summary,
        "artifacts": artifacts or [],
        "endedAt": datetime.now().isoformat(timespec="seconds"),
    }]}
    (dirs["runs_meta"] / f"{job_id}.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "untitled"


def _exercise_metas(ex_dir: Path) -> list[tuple[Path, dict]]:
    out = []
    if not ex_dir.exists():
        return out
    for d in sorted(ex_dir.iterdir()):
        if d.is_dir() and not d.name.startswith("_"):
            out.append((d, renders.load_meta(d)))
    return sorted(
        out,
        key=lambda item: item[1].get("created_at", item[1].get("id", item[0].name)),
    )


def _refresh_index(dirs: dict) -> None:
    metas = [m for _, m in _exercise_metas(dirs["exercises"]) if m.get("id")]
    dirs["index"].write_text(renders.render_index_md(metas), encoding="utf-8")


def _unique_id(slug: str) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    return f"{stamp}-{_slugify(slug)}-{uuid4().hex[:8]}"


def _frame_id(value: str) -> str:
    match = re.fullmatch(r"f_(\d+)(?:\.jpg)?", str(value).strip())
    return f"f_{int(match.group(1)):03d}" if match else ""


def _ground_principles(principles: list[dict], frame_map: dict[str, str]) -> list[dict]:
    """Replace model-guessed evidence times with the extracted-frame mapping."""
    grounded = []
    for principle in principles:
        if not isinstance(principle, dict):
            continue
        item = dict(principle)
        evidence = item.get("evidence", [])
        if isinstance(evidence, dict):
            evidence = [evidence]
        if isinstance(evidence, list):
            fixed = []
            for entry in evidence:
                if not isinstance(entry, dict):
                    fixed.append(entry)
                    continue
                entry = dict(entry)
                frame = _frame_id(entry.get("frame", ""))
                if frame:
                    entry["frame"] = frame
                    entry["time"] = frame_map.get(frame, entry.get("time", ""))
                fixed.append(entry)
            item["evidence"] = fixed
        grounded.append(item)
    return grounded


def _ground_pass1(result: dict, frame_map: dict[str, str]) -> dict:
    result = dict(result)
    moments = []
    for moment in result.get("key_moments", []):
        if not isinstance(moment, dict):
            continue
        item = dict(moment)
        frame = _frame_id(item.get("frame", ""))
        if frame:
            item["frame"] = frame
            item["time"] = frame_map.get(frame, item.get("time", ""))
        moments.append(item)
    result["key_moments"] = moments
    result["keyframes"] = [
        frame for frame in (_frame_id(value) for value in result.get("keyframes", []))
        if frame in frame_map
    ]
    return result


# ---------------------------------------------------------------- 出题

def new_exercise(settings: Settings, force: bool = False) -> dict:
    dirs = ws_dirs(settings.workspace)
    existing = _exercise_metas(dirs["exercises"])
    latest_dir, latest_meta = existing[-1] if existing else (None, {})
    if latest_meta.get("status") in ("prompted", "submitted", "needs_micro_revision"):
        if not force:
            raise RuntimeError(
                f"上一题「{latest_meta.get('title')}」尚未完成，先提交 micro-v2 或加 force=1 跳过")
        latest_meta["status"] = "skipped"
        renders.save_meta(latest_dir, latest_meta)

    history = "\n".join(f"- {m.get('title', d.name)}" for d, m in existing) or "（无）"
    data = chat_json(
        _client(settings), settings.llm_model,
        renders.load_prompt(
            "gen_prompt",
            pool=renders.read(dirs["pool"], "（题库为空，请原创）"),
            history=history,
            next_focus="、".join(latest_meta.get("next_focus", [])) or "（首次出题）",
        ),
        user="请出一道题。")

    slug = _unique_id(data["slug"])
    ex_dir = dirs["exercises"] / slug
    ex_dir.mkdir(parents=True, exist_ok=True)
    (ex_dir / "prompt.md").write_text(
        renders.render_prompt_md(data, str(date.today())), encoding="utf-8")
    sub_tpl = renders.read(dirs["template"] / "submission.md")
    (ex_dir / "submission.md").write_text(
        sub_tpl.replace("<复制题目名>", data["title"]), encoding="utf-8")

    meta = {
        "id": slug, "title": data["title"], "date": str(date.today()),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "difficulty": data.get("difficulty", "★★☆"), "status": "prompted",
        "summary": f"题目已出：{data['scenario'][:60]}…",
    }
    renders.save_meta(ex_dir, meta)
    _refresh_index(dirs)
    manifest.regenerate(settings.workspace, dirs)
    _write_run(dirs, f"new-{slug}", f"出题：{data['title']}", "succeeded",
               meta["summary"], [f"exercises/{slug}/prompt.md"])
    return meta


# ---------------------------------------------------------------- 打分

def grade(settings: Settings, exercise_id: str, submission_text: str) -> dict:
    dirs = ws_dirs(settings.workspace)
    try:
        ex_dir = safe_child(dirs["exercises"], exercise_id)
    except ValueError as exc:
        raise RuntimeError(f"invalid exercise id: {exercise_id}") from exc
    if not ex_dir.exists():
        raise RuntimeError(f"exercise not found: {exercise_id}")
    meta = renders.load_meta(ex_dir)
    if meta.get("status") in ("reviewed", "needs_micro_revision"):
        raise RuntimeError("该题已评审过")
    if meta.get("status") not in ("prompted", "submitted"):
        raise RuntimeError("该题当前不可提交评分")
    if not submission_text.strip():
        raise RuntimeError("submission 为空")

    (ex_dir / "submission.md").write_text(
        f"# 我的分镜方案\n\n> 题目：{meta.get('title', '')} ｜ 提交日期：{date.today()}\n\n"
        + submission_text.strip() + "\n", encoding="utf-8")
    meta["status"] = "submitted"
    renders.save_meta(ex_dir, meta)

    raw_data = chat_json(
        _client(settings), settings.llm_model,
        renders.load_prompt(
            "grade",
            rubric=renders.read(dirs["rubric"]),
            principles=renders.read(dirs["principles"], "（暂无累积原则）"),
            prompt=renders.read(ex_dir / "prompt.md"),
            submission=submission_text),
        user="请评审并给出 JSON。",
        temperature=0.4)
    data = normalize_grade(raw_data)

    (ex_dir / "grade.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    (ex_dir / "review.md").write_text(
        renders.render_review_md(data, meta.get("title", ""), str(date.today())),
        encoding="utf-8")
    weakest = data.get("weakest") or [{}]
    meta.update({
        "status": "needs_micro_revision", "score": data.get("total", 0),
        "strongest": (data.get("strengths") or ["—"])[0][:40],
        "weakest": weakest[0].get("dim", "—"),
        "next_focus": data.get("next_focus", []),
        "changes": (data.get("revision") or {}).get("changes", []),
        "micro_revision_focus": weakest[0],
        "summary": f"已评审：{data.get('total', 0)}/100。{data.get('one_liner', '')[:60]}",
    })
    renders.save_meta(ex_dir, meta)
    _refresh_index(dirs)
    manifest.regenerate(settings.workspace, dirs)
    _write_run(dirs, f"grade-{exercise_id}", f"评审：{meta.get('title')}",
               "succeeded", meta["summary"],
               [f"exercises/{exercise_id}/review.md"])
    return meta


def complete_micro_revision(settings: Settings, exercise_id: str,
                            micro_text: str) -> dict:
    """Save the learner's focused rewrite, then reveal the full model revision."""
    dirs = ws_dirs(settings.workspace)
    try:
        ex_dir = safe_child(dirs["exercises"], exercise_id)
    except ValueError as exc:
        raise RuntimeError(f"invalid exercise id: {exercise_id}") from exc
    if not ex_dir.exists():
        raise RuntimeError(f"exercise not found: {exercise_id}")
    if not micro_text.strip():
        raise RuntimeError("micro-v2 不能为空")
    meta = renders.load_meta(ex_dir)
    if meta.get("status") != "needs_micro_revision":
        raise RuntimeError("该题当前不在 micro-v2 提交阶段")
    grade_path = ex_dir / "grade.json"
    if not grade_path.exists():
        raise RuntimeError("grade.json 不存在，无法展开 revision")
    grade_data = json.loads(grade_path.read_text(encoding="utf-8"))
    (ex_dir / "micro_revision.md").write_text(
        f"# 我的局部改写（micro-v2）\n\n"
        f"> 题目：{meta.get('title', '')} ｜ 聚焦：{meta.get('weakest', '—')}\n\n"
        + micro_text.strip() + "\n", encoding="utf-8")
    (ex_dir / "revision.md").write_text(
        renders.render_revision_md(grade_data, meta.get("title", "")),
        encoding="utf-8")
    meta.update({
        "status": "reviewed",
        "micro_revision_submitted": True,
        "summary": f"已评审：{meta.get('score', 0)}/100，已完成 micro-v2。",
    })
    renders.save_meta(ex_dir, meta)
    _refresh_index(dirs)
    manifest.regenerate(settings.workspace, dirs)
    _write_run(dirs, f"micro-{exercise_id}", f"micro-v2：{meta.get('title')}",
               "succeeded", "已提交局部改写并展开完整 revision",
               [f"exercises/{exercise_id}/micro_revision.md",
                f"exercises/{exercise_id}/revision.md"])
    return meta


# ---------------------------------------------------------------- 分析视频

def analyze(settings: Settings, video_name: str) -> dict:
    dirs = ws_dirs(settings.workspace)
    try:
        video = safe_child(dirs["videos"], video_name)
        inbox = safe_child(dirs["inbox"], video_name)
    except ValueError as exc:
        raise RuntimeError(f"invalid video name: {video_name}") from exc
    if not video.is_file():
        if inbox.is_file():
            video.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(inbox), video)
        else:
            raise RuntimeError(f"video not found: {video_name} (videos/ or videos/inbox/)")

    slug = _unique_id(video.stem)
    run_dir = dirs["runs"] / slug
    frames_dir, small_dir, sheets_dir = (run_dir / "frames",
                                       run_dir / "small", run_dir / "sheets")

    duration = pipeline.probe_duration(settings.ffmpeg, settings.ffprobe, video)
    if duration > settings.max_video_seconds:
        raise RuntimeError(
            f"video is too long ({duration:.0f}s > {settings.max_video_seconds}s)")
    fps = 1.0 if duration <= 120 else 0.5
    frames = pipeline.extract_frames(settings.ffmpeg, video, frames_dir, small_dir, fps)
    sheets = pipeline.make_sheets(settings.ffmpeg, video, sheets_dir, fps)
    frame_map = {item["frame"]: item["time"]
                 for item in pipeline.frame_manifest(frames, fps)}

    # 第一遍：拼图通读（超过 24 张时按间隔抽样）
    step = max(1, len(sheets) // 24 + (1 if len(sheets) % 24 else 0))
    sheets_send = sheets[::step]
    p1 = chat_json(
        _client(settings), settings.llm_vision_model,
        renders.load_prompt("analyze_pass1"),
        user=[{"type": "text", "text":
               f"共 {len(sheets)} 张拼图（本次发送每第 {step} 张）。视频约 {duration:.0f} 秒。"
               f"抽帧频率为 {fps} fps；图中烧录的 frame id 与 timestamp 是唯一可信映射。"}],
        images=sheets_send,
        temperature=0.4)
    p1 = _ground_pass1(p1, frame_map)

    # 第二遍：关键帧单图 → 正式分析
    kf_ids = [_frame_id(k) for k in p1.get("keyframes", [])]
    kf_ids = [k for k in kf_ids if k and (small_dir / f"{k}.jpg").exists()]
    kf_imgs = [small_dir / f"{k}.jpg" for k in kf_ids][:16]
    p2 = chat_json(
        _client(settings), settings.llm_vision_model,
        renders.load_prompt("analyze_pass2", pass1=json.dumps(p1, ensure_ascii=False)),
        user=[{"type": "text", "text":
               "附关键帧：" + ", ".join(f"{k}={frame_map.get(k, '未知')}" for k in kf_ids[:16])
               + "。每张图片左上角也烧录了相同标识。"}],
        images=kf_imgs, temperature=0.4)
    p2["principles"] = _ground_principles(p2.get("principles", []), frame_map)

    an_dir = dirs["analyses"] / slug
    (an_dir / "keyframes").mkdir(parents=True, exist_ok=True)
    final_kfs = [_frame_id(k) for k in p2.get("keyframes_final", kf_ids)]
    final_kfs = [k for k in final_kfs if k]
    final_kfs = [k for k in final_kfs
                 if (frames_dir / f"{k}.jpg").exists()]
    for k in final_kfs:
        shutil.copy2(frames_dir / f"{k}.jpg", an_dir / "keyframes" / f"{k}.jpg")

    (an_dir / "分析.md").write_text(
        renders.render_analysis_md(p2, video.name, duration), encoding="utf-8")
    (an_dir / "frame_manifest.json").write_text(
        json.dumps({"fps": fps, "frames": pipeline.frame_manifest(frames, fps)},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    candidates = save_candidates(
        an_dir / "principles_candidates.json",
        video.name,
        f"analyses/{slug}/分析.md",
        p2.get("principles", []),
    )

    stats = p1.get("stats", {})
    stats["时长"] = f"{duration:.0f}秒"
    stats["帧数"] = len(frames)
    meta = {
        "id": slug, "title": p2.get("title_cn", video.name),
        "date": str(date.today()),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "summary": p2.get("story", "")[:80],
        "source": f"videos/{video.name}", "stats": stats,
        "principle_candidates": [c["id"] for c in candidates],
        "keyframe_notes": {
            _frame_id(km.get("frame", "")): (
                f"{frame_map.get(_frame_id(km.get('frame', '')), km.get('time', ''))} "
                f"{km.get('why', '')}"
            )
            for km in p1.get("key_moments", []) if _frame_id(km.get("frame", ""))},
    }
    renders.save_meta(an_dir, meta)
    manifest.regenerate(settings.workspace, dirs)
    _write_run(dirs, f"analyze-{slug}", f"分析：{meta['title']}", "succeeded",
               meta["summary"], [f"analyses/{slug}/分析.md"])
    return meta


_client_cache = {}


def _client(settings: Settings):
    from .llm import make_client
    key = (settings.llm_base_url, settings.llm_api_key)
    if key not in _client_cache:
        _client_cache[key] = make_client(*key)
    return _client_cache[key]
