"""Human-curated principle candidates and the accepted taste profile."""
import json
import re
from datetime import datetime
from pathlib import Path


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def save_candidates(path: Path, source_video: str, source_analysis: str,
                    principles: list[dict]) -> list[dict]:
    """Persist model suggestions separately from the accepted taste profile."""
    candidates = []
    for index, principle in enumerate(principles, start=1):
        title = str(principle.get("title", "")).strip()
        detail = str(principle.get("detail", "")).strip()
        if not title or not detail:
            continue
        candidates.append({
            "id": f"{path.parent.name}-p{index}",
            "title": title,
            "detail": detail,
            "evidence": principle.get("evidence", []),
            "source_video": source_video,
            "source_analysis": source_analysis,
            "status": "pending",
        })
    _write_json(path, {
        "schema_version": 1,
        "source_video": source_video,
        "source_analysis": source_analysis,
        "candidates": candidates,
    })
    return candidates


def _candidate_files(dirs: dict) -> list[Path]:
    parent = dirs["analyses"]
    if not parent.exists():
        return []
    return sorted(parent.glob("*/principles_candidates.json"))


def _find_candidate(dirs: dict, candidate_id: str):
    for path in _candidate_files(dirs):
        document = _read_json(path, {})
        for candidate in document.get("candidates", []):
            if candidate.get("id") == candidate_id:
                return path, document, candidate
    return None, None, None


def _accepted(dirs: dict) -> list[dict]:
    return _read_json(dirs["principles_meta"], {"principles": []}).get("principles", [])


def _render_evidence(evidence) -> str:
    if isinstance(evidence, list):
        bits = []
        for item in evidence:
            if isinstance(item, dict):
                frame = item.get("frame", "")
                time = item.get("time", "")
                why = item.get("why", "")
                bits.append(" ".join(part for part in (frame, time, why) if part))
            else:
                bits.append(str(item))
        return "; ".join(bits)
    return str(evidence or "")


def _upsert_markdown(path: Path, principle: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else "# 口味基准\n\n"
    marker = re.escape(principle["id"])
    block = f"""<!-- principle:{principle['id']}:start -->
### {principle['title']}

{principle['detail']}

- 来源视频：{principle.get('source_video', '—')}
- 证据：{_render_evidence(principle.get('evidence')) or '—'}

<!-- principle:{principle['id']}:end -->
"""
    pattern = rf"<!-- principle:{marker}:start -->.*?<!-- principle:{marker}:end -->\n?"
    if re.search(pattern, text, flags=re.S):
        text = re.sub(pattern, block, text, flags=re.S)
    else:
        text = text.rstrip() + "\n\n" + block
    path.write_text(text, encoding="utf-8")


def _remove_markdown_block(path: Path, candidate_id: str) -> None:
    if not path.exists():
        return
    marker = re.escape(candidate_id)
    pattern = rf"<!-- principle:{marker}:start -->.*?<!-- principle:{marker}:end -->\n?"
    text = path.read_text(encoding="utf-8")
    text = re.sub(pattern, "", text, flags=re.S)
    path.write_text(text, encoding="utf-8")


def update_candidate(dirs: dict, candidate_id: str, action: str,
                     title: str = "", detail: str = "") -> dict:
    if action not in {"accept", "reject"}:
        raise ValueError("action must be accept or reject")
    path, document, candidate = _find_candidate(dirs, candidate_id)
    if candidate is None:
        raise FileNotFoundError(f"principle candidate not found: {candidate_id}")
    if title.strip():
        candidate["title"] = title.strip()
    if detail.strip():
        candidate["detail"] = detail.strip()
    candidate["status"] = "accepted" if action == "accept" else "rejected"
    candidate["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json(path, document)

    accepted = [p for p in _accepted(dirs) if p.get("id") != candidate_id]
    if action == "accept":
        accepted.append({
            **candidate,
            "accepted_at": datetime.now().isoformat(timespec="seconds"),
        })
        _upsert_markdown(dirs["principles"], candidate)
    else:
        _remove_markdown_block(dirs["principles"], candidate_id)
    _write_json(dirs["principles_meta"], {"schema_version": 1, "principles": accepted})
    return candidate


def state(dirs: dict) -> dict:
    candidates = []
    for path in _candidate_files(dirs):
        document = _read_json(path, {})
        candidates.extend(document.get("candidates", []))
    return {
        "pending": [c for c in candidates if c.get("status") == "pending"],
        "candidates": candidates,
        "accepted": _accepted(dirs),
    }
