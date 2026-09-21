"""Regenerate .a2h/manifest.json from workspace meta.json files.

Curation policy (same as AGENTS.md): the manifest is a curated view, not a
file listing. Only the latest exercise, INDEX, all analyses' reports, the
latest analysis' keyframes, and the rule files go in. runs/ and raw videos
never do.
"""
import json
from datetime import date
from pathlib import Path

from .renders import load_meta


def _scan_dirs(parent: Path) -> list[tuple[Path, dict]]:
    out = []
    if not parent.exists():
        return out
    for d in sorted(parent.iterdir()):
        if d.is_dir() and not d.name.startswith("_"):
            out.append((d, load_meta(d)))
    return sorted(
        out,
        key=lambda item: item[1].get("created_at", item[1].get("id", item[0].name)),
    )


def _exercise_items(d: Path, meta: dict) -> tuple[list[dict], list[str]]:
    items, artifacts = [], []
    specs = [
        ("review.md", "review", "评分与修改意见", 100),
        ("revision.md", "candidate", "修改版分镜 v2", 95),
        ("micro_feedback.md", "feedback", "micro-v2 定向反馈", 96),
        ("micro_revision.md", "submission", "我的局部改写 micro-v2", 94),
        ("grade.json", "data", "完整评分数据", 92),
        ("submission.md", "submission", "分镜方案", 90),
        ("prompt.md", "prompt", "题目", 80),
    ]
    for fname, role, label, pri in specs:
        # Keep the full model revision machine-readable on disk, but do not
        # surface it in the review view before the learner submits micro-v2.
        if fname == "grade.json" and meta.get("status") != "reviewed":
            continue
        if (d / fname).exists():
            rel = f"{d.parent.name}/{d.name}/{fname}"
            items.append({
                "path": rel, "role": role,
                "title": f"{label}：{meta.get('title', d.name)}",
                "summary": meta.get(f"{fname.split('.')[0]}_summary",
                                   meta.get("summary", "")),
                "group": "exercise", "taskId": meta.get("id", d.name),
                "priority": pri,
            })
            artifacts.append(rel)
    return items, artifacts


def _analysis_items(d: Path, meta: dict, latest: bool) -> tuple[list[dict], list[str]]:
    items, artifacts = [], []
    md = next((f for f in d.glob("*.md") if f.name != "PRINCIPLES.md"), None)
    if md:
        rel = f"analyses/{d.name}/{md.name}"
        items.append({
            "path": rel, "role": "report",
            "title": meta.get("title", d.name),
            "summary": meta.get("summary", ""),
            "group": "analysis", "taskId": f"analysis-{d.name}",
            "priority": 70 if latest else 40,
            "tags": ["final"],
        })
        artifacts.append(rel)
    candidates = d / "principles_candidates.json"
    if candidates.exists():
        rel = f"analyses/{d.name}/principles_candidates.json"
        items.append({
            "path": rel, "role": "data",
            "title": f"原则候选：{meta.get('title', d.name)}",
            "summary": "等待人工接受、修改后接受或拒绝",
            "group": "reference", "taskId": f"analysis-{d.name}",
            "priority": 55,
        })
        artifacts.append(rel)
    frame_map = d / "frame_manifest.json"
    if frame_map.exists():
        rel = f"analyses/{d.name}/frame_manifest.json"
        items.append({
            "path": rel, "role": "data",
            "title": f"帧时间映射：{meta.get('title', d.name)}",
            "summary": "关键帧 id 与真实 timestamp 的确定性映射",
            "group": "evidence", "taskId": f"analysis-{d.name}",
            "priority": 64,
        })
        artifacts.append(rel)
    if latest:
        for i, kf in enumerate(sorted((d / "keyframes").glob("*.jpg"))
                               if (d / "keyframes").exists() else []):
            rel = f"analyses/{d.name}/keyframes/{kf.name}"
            info = (meta.get("keyframe_notes") or {}).get(kf.stem, "")
            items.append({
                "path": rel, "role": "evidence",
                "title": info or kf.stem,
                "summary": info,
                "group": "evidence", "taskId": f"analysis-{d.name}",
                "priority": 66 - i,
            })
            artifacts.append(rel)
    return items, artifacts


def regenerate(ws: Path, dirs: dict) -> Path:
    exercises = _scan_dirs(dirs["exercises"])
    analyses = _scan_dirs(dirs["analyses"])

    ex_metas = [m for _, m in exercises if m.get("id")]
    an_metas = [m for _, m in analyses if m.get("id")]

    items, tasks, artifacts_map = [], [], {}
    for i, (d, m) in enumerate(reversed(exercises)):
        if i == 0:  # latest exercise only
            ex_items, ex_art = _exercise_items(d, m)
            items.extend(ex_items)
            artifacts_map[m.get("id", d.name)] = ex_art
            status = m.get("status", "prompted")
            tasks.append({
                "id": m.get("id", d.name),
                "title": f"练习：{m.get('title', d.name)}",
                "status": {"prompted": "running", "submitted": "running",
                           "needs_micro_revision": "running",
                           "reviewed": "succeeded"}.get(status, status),
                "summary": m.get("summary", ""),
                "artifacts": ex_art,
            })
    for i, (d, m) in enumerate(reversed(analyses)):
        an_items, an_art = _analysis_items(d, m, latest=(i == 0))
        items.extend(an_items)
        tasks.append({
            "id": f"analysis-{d.name}",
            "title": f"视频分析：{m.get('title', d.name)}",
            "status": "succeeded",
            "summary": m.get("summary", ""),
            "artifacts": an_art,
        })

    if dirs["index"].exists():
        items.append({"path": "exercises/INDEX.md", "role": "report",
                      "title": "训练进度总表",
                      "summary": "每次练习的得分曲线与强弱项",
                      "group": "progress", "priority": 85})
    if dirs["principles"].exists():
        items.append({"path": "analyses/PRINCIPLES.md", "role": "spec",
                      "title": "口味基准（累积原则）",
                      "summary": "完整的原则 ledger；评审使用其 compact active profile",
                      "group": "reference", "priority": 48})
    if dirs.get("active_profile", Path()).exists():
        items.append({"path": "analyses/ACTIVE_PROFILE.md", "role": "spec",
                      "title": "当前口味 profile",
                      "summary": "评分实际注入的 compact 原则集合",
                      "group": "reference", "priority": 52})
    # Shared application assets live outside the user workspace and are not
    # valid paths in a per-user manifest.  They are still read by the jobs.

    latest_ex = ex_metas[-1] if ex_metas else {}
    latest_an = an_metas[-1] if an_metas else {}
    status_map = {"prompted": ("running", "题目已出，等待提交分镜后点「打分」"),
                  "submitted": ("running", "分镜已提交，可点评审"),
                  "needs_micro_revision": ("running", "评审完成，先提交一个 micro-v2 再查看完整 revision"),
                  "reviewed": ("succeeded",
                               f"已评审：{latest_ex.get('score', '?')}/100"
                               f"｜最强={latest_ex.get('strongest', '—')}"
                               f"｜最弱={latest_ex.get('weakest', '—')}")}
    st, detail = status_map.get(latest_ex.get("status", "prompted"),
                                ("info", "暂无练习"))
    panels = [{
        "type": "status", "title": "最新练习状态", "status": st,
        "detail": f"{latest_ex.get('title', '—')}（{latest_ex.get('difficulty', '—')}）→ {detail}",
    }]
    changes = latest_ex.get("changes") or []
    if changes:
        panels.append({
            "type": "table",
            "title": "v1 → v2 优化对照（来自 review.md 的修改建议）",
            "columns": [
                {"key": "part", "label": "环节"},
                {"key": "v1", "label": "原方案"},
                {"key": "v2", "label": "优化后"},
                {"key": "why", "label": "为什么"},
            ],
            "rows": changes,
            "note": "完整改写示范见 review.md，整合后的完整分镜见 revision.md",
        })
    stats = latest_an.get("stats") or {}
    if stats:
        panels.append({"type": "metrics",
                       "title": f"最新视频分析：{latest_an.get('title', '—')}",
                       "items": [{"label": k, "value": str(v)}
                                 for k, v in stats.items()]})

    manifest = {
        "a2h": 1,
        "name": "视频脚本分析",
        "summary": (f"最新练习「{latest_ex.get('title', '—')}」"
                    f"{latest_ex.get('status', '')}｜"
                    f"已分析视频 {len(an_metas)} 部｜生成于 {date.today()}"),
        "groups": [
            {"id": "exercise", "title": "最新练习", "order": 0},
            {"id": "progress", "title": "训练进度", "order": 1},
            {"id": "analysis", "title": "分析结论", "order": 2},
            {"id": "evidence", "title": "关键帧证据", "order": 3},
            {"id": "reference", "title": "规则与题库", "order": 4},
        ],
        "tasks": tasks,
        "actions": [],
        "panels": panels,
        "items": items,
    }
    out = dirs["manifest"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out
