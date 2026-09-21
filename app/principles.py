"""Human-curated principle candidates, ledger, and active taste profile."""
import json
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4


LEDGER_SCHEMA_VERSION = 2
MAX_ACTIVE_PRINCIPLES = 40
MAX_PROFILE_CHARS = 12_000


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"原则数据损坏：{path}") from exc


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _write_json(path: Path, value) -> None:
    _atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2))


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


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_ledger(dirs: dict) -> tuple[dict, bool]:
    """Read schema 1 or 2 and return a normalized schema-2 ledger."""
    raw = _read_json(dirs["principles_meta"], {"principles": []})
    if not isinstance(raw, dict):
        raise RuntimeError("原则 ledger 格式无效")
    principles = raw.get("principles", [])
    if not isinstance(principles, list):
        raise RuntimeError("原则 ledger 格式无效")
    migrated = raw.get("schema_version", 1) < LEDGER_SCHEMA_VERSION
    normalized = []
    for item in principles:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        entry = dict(item)
        status = entry.get("status") or "active"
        entry["status"] = "active" if status == "accepted" else status
        normalized.append(entry)
    active_ids = raw.get("active_ids")
    if not isinstance(active_ids, list):
        active_ids = [
            item["id"] for item in normalized
            if item.get("status") == "active"
        ]
        migrated = True
    active_set = {
        str(value) for value in active_ids
        if isinstance(value, str)
        and any(item.get("id") == value and item.get("status") == "active"
                for item in normalized)
    }
    ledger = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "principles": normalized,
        "active_ids": sorted(active_set),
        "events": raw.get("events", []) if isinstance(raw.get("events", []), list) else [],
    }
    return ledger, migrated


def _ledger_entry(ledger: dict, principle_id: str) -> dict | None:
    return next(
        (item for item in ledger["principles"] if item.get("id") == principle_id),
        None,
    )


def _upsert_ledger_entry(ledger: dict, entry: dict) -> None:
    existing = _ledger_entry(ledger, entry["id"])
    if existing is None:
        ledger["principles"].append(dict(entry))
    else:
        existing.clear()
        existing.update(entry)


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
    """Keep a human-readable historical block for every ledger entry."""
    text = path.read_text(encoding="utf-8") if path.exists() else "# 口味基准（原则 ledger）\n\n"
    marker = re.escape(principle["id"])
    status = principle.get("status", "active")
    block = f"""<!-- principle:{principle['id']}:start -->
### {principle['title']}

{principle['detail']}

- 状态：{status}
- 来源视频：{principle.get('source_video', '—')}
- 证据：{_render_evidence(principle.get('evidence')) or '—'}

<!-- principle:{principle['id']}:end -->
"""
    pattern = rf"<!-- principle:{marker}:start -->.*?<!-- principle:{marker}:end -->\n?"
    if re.search(pattern, text, flags=re.S):
        text = re.sub(pattern, block, text, flags=re.S)
    else:
        text = text.rstrip() + "\n\n" + block
    _atomic_write(path, text)


def _profile_tokens(principle: dict) -> set[str]:
    value = f"{principle.get('title', '')} {principle.get('detail', '')}".casefold()
    return set(re.findall(r"[\w\u3400-\u9fff]+", value))


def _similar(left: dict, right: dict) -> bool:
    a, b = _profile_tokens(left), _profile_tokens(right)
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= 0.75


def _entry_sort_key(entry: dict) -> tuple[str, str]:
    return (str(entry.get("updated_at", entry.get("accepted_at", ""))),
            str(entry.get("id", "")))


def _curate_active(ledger: dict) -> list[dict]:
    active_ids = set(ledger.get("active_ids", []))
    candidates = [
        item for item in ledger["principles"]
        if item.get("id") in active_ids and item.get("status") == "active"
    ]
    candidates.sort(key=_entry_sort_key, reverse=True)
    selected: list[dict] = []
    total_chars = 0
    for candidate in candidates:
        if any(_similar(candidate, current) for current in selected):
            candidate["status"] = "merged"
            candidate["merged_into"] = selected[
                next(index for index, current in enumerate(selected)
                     if _similar(candidate, current))
            ]["id"]
            continue
        block = (
            f"### {candidate.get('title', '')}\n\n"
            f"{candidate.get('detail', '')}\n\n"
            f"来源：{candidate.get('source_video', '—')}\n\n"
        )
        if selected and (len(selected) >= MAX_ACTIVE_PRINCIPLES or
                         total_chars + len(block) > MAX_PROFILE_CHARS):
            candidate["status"] = "inactive"
            continue
        selected.append(candidate)
        total_chars += len(block)
        if len(selected) >= MAX_ACTIVE_PRINCIPLES:
            break
    ledger["active_ids"] = [item["id"] for item in selected]
    return selected


def _write_active_profile(dirs: dict, ledger: dict) -> list[dict]:
    previous = {
        item.get("id"): (item.get("status"), item.get("merged_into"))
        for item in ledger["principles"]
    }
    selected = _curate_active(ledger)
    for item in ledger["principles"]:
        current = (item.get("status"), item.get("merged_into"))
        if previous.get(item.get("id")) != current:
            _upsert_markdown(dirs["principles"], item)
    blocks = [
        f"### {item.get('title', '')}\n\n"
        f"{item.get('detail', '')}\n\n"
        f"来源：{item.get('source_video', '—')}\n"
        for item in selected
    ]
    text = "# 当前口味 profile\n\n" + "\n".join(blocks)
    _atomic_write(dirs["active_profile"], text)
    return selected


def _persist(ledger: dict, dirs: dict) -> list[dict]:
    selected = _write_active_profile(dirs, ledger)
    _write_json(dirs["principles_meta"], ledger)
    return selected


def active_profile_text(dirs: dict) -> str:
    """Rebuild the compact profile from the ledger before every grade."""
    ledger, migrated = _load_ledger(dirs)
    selected = _write_active_profile(dirs, ledger)
    _write_json(dirs["principles_meta"], ledger)
    if not selected:
        return "（暂无累积原则）"
    return dirs["active_profile"].read_text(encoding="utf-8")


def _accepted(dirs: dict) -> list[dict]:
    ledger, _ = _load_ledger(dirs)
    active_ids = set(ledger.get("active_ids", []))
    return [
        item for item in ledger["principles"]
        if item.get("id") in active_ids and item.get("status") == "active"
    ]


def update_candidate(dirs: dict, candidate_id: str, action: str,
                     title: str = "", detail: str = "") -> dict:
    if action not in {"accept", "reject", "activate", "deactivate"}:
        raise ValueError("action must be accept, reject, activate or deactivate")
    path, document, candidate = _find_candidate(dirs, candidate_id)
    ledger, _ = _load_ledger(dirs)
    entry = _ledger_entry(ledger, candidate_id)
    if candidate is None and entry is None:
        raise FileNotFoundError(f"principle candidate not found: {candidate_id}")
    if candidate is None:
        candidate = dict(entry)
    if title.strip():
        candidate["title"] = title.strip()
    if detail.strip():
        candidate["detail"] = detail.strip()
    now = _now()

    if action == "accept":
        candidate["status"] = "accepted"
        candidate["updated_at"] = now
        entry = {**(entry or {}), **candidate, "status": "active",
                 "accepted_at": (entry or {}).get("accepted_at", now)}
        ledger["active_ids"] = [*ledger.get("active_ids", []), candidate_id]
    elif action == "reject":
        candidate["status"] = "rejected"
        candidate["updated_at"] = now
        entry = {**(entry or {}), **candidate, "status": "rejected",
                 "rejected_at": now}
        ledger["active_ids"] = [
            value for value in ledger.get("active_ids", [])
            if value != candidate_id
        ]
    elif action == "activate":
        if entry is None or entry.get("status") in {"rejected", "merged"}:
            raise ValueError("只能激活已接受且未合并的原则")
        entry = {**entry, "status": "active", "updated_at": now}
        candidate["status"] = "accepted"
        ledger["active_ids"] = [*ledger.get("active_ids", []), candidate_id]
    else:
        if entry is None:
            raise ValueError("原则尚未接受，不能停用")
        entry = {**entry, "status": "inactive", "updated_at": now}
        candidate["status"] = "inactive"
        ledger["active_ids"] = [
            value for value in ledger.get("active_ids", [])
            if value != candidate_id
        ]

    if path is not None:
        _write_json(path, document)
    _upsert_ledger_entry(ledger, entry)
    ledger["events"].append({"at": now, "id": candidate_id, "action": action})
    _upsert_markdown(dirs["principles"], entry)
    _persist(ledger, dirs)
    return candidate


def state(dirs: dict) -> dict:
    candidates = []
    for path in _candidate_files(dirs):
        document = _read_json(path, {})
        candidates.extend(document.get("candidates", []))
    ledger, migrated = _load_ledger(dirs)
    active = _write_active_profile(dirs, ledger)
    _write_json(dirs["principles_meta"], ledger)
    active_ids = {item["id"] for item in active}
    inactive = [
        item for item in ledger["principles"]
        if item.get("id") not in active_ids
    ]
    return {
        "pending": [c for c in candidates if c.get("status") == "pending"],
        "candidates": candidates,
        "accepted": [item for item in active],
        "active": active,
        "inactive": inactive,
        "ledger": ledger["principles"],
    }
