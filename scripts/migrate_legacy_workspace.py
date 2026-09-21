"""One-shot migration of the old single-user workspace.

The application never calls this module as a fallback.  Run it while the
service is stopped, inspect ``--dry-run`` first, and keep the source until the
copy has been verified.  Provider API keys are migrated by the provider
service's encrypted-store migration, not copied into the new workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from uuid import UUID


COPY_DIRS = ("videos", "runs", "analyses")
SHARED_FILES = ("RUBRIC.md", "PROMPT_POOL.md", "AGENTS.md")


def _validate_user_id(user_id: str) -> str:
    try:
        UUID(user_id)
    except ValueError as exc:
        raise ValueError("user-id must be an immutable UUID") from exc
    return user_id


def _copy_tree(source: Path, target: Path, *, dry_run: bool) -> list[str]:
    copied: list[str] = []
    if not source.exists():
        return copied
    if dry_run:
        return [str(path.relative_to(source)) for path in source.rglob("*")]
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, copy_function=shutil.copy2)
    copied.extend(str(path.relative_to(source)) for path in source.rglob("*"))
    return copied


def _copy_exercises(source: Path, target: Path, *, dry_run: bool) -> list[str]:
    """Copy user history while leaving the old shared template in place."""
    if not source.exists():
        return []
    copied: list[str] = []
    for child in sorted(source.iterdir()):
        if child.name == "_template":
            continue
        relative = child.relative_to(source)
        if dry_run:
            copied.extend(str(path.relative_to(source)) for path in child.rglob("*"))
            if child.is_file():
                copied.append(str(relative))
            continue
        destination = target / relative
        if child.is_dir():
            shutil.copytree(child, destination, copy_function=shutil.copy2)
            copied.extend(str(path.relative_to(source)) for path in child.rglob("*"))
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, destination)
            copied.append(str(relative))
    return copied


def _remove_exercise_history(source: Path) -> None:
    """Remove migrated exercise history but preserve the legacy template."""
    if not source.exists():
        return
    for child in source.iterdir():
        if child.name == "_template":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def migrate(root: Path, user_id: str, *, dry_run: bool = False,
            remove_source: bool = False) -> dict:
    root = Path(root).expanduser().resolve()
    user_id = _validate_user_id(user_id)
    target = root / "users" / user_id
    if target.exists():
        raise FileExistsError(f"target already exists; refusing to overwrite: {target}")

    plan: list[str] = []
    for name in COPY_DIRS:
        source = root / name
        if source.exists():
            plan.append(name + "/")
    if (root / "exercises").exists():
        plan.append("exercises/ (shared _template excluded)")
    shared = [name for name in SHARED_FILES if (root / name).exists()]
    if shared:
        plan.append("shared assets retained at legacy root: " + ", ".join(shared))
    a2h = root / ".a2h"
    if a2h.exists():
        plan.append(".a2h/ (providers.json excluded; migrate it through Node encryption)")
    report = {"root": str(root), "target": str(target), "plan": plan, "dry_run": dry_run}
    if dry_run:
        return report

    if (a2h / "providers.json").exists():
        raise RuntimeError(
            "先用 provider-service 的一次性加密迁移处理 .a2h/providers.json；"
            "成功删除明文文件后再迁移 workspace"
        )

    target.mkdir(parents=True)
    try:
        for name in COPY_DIRS:
            _copy_tree(root / name, target / name, dry_run=False)
        _copy_exercises(root / "exercises", target / "exercises", dry_run=False)
        if a2h.exists():
            destination = target / ".a2h"
            for source in a2h.rglob("*"):
                relative = source.relative_to(a2h)
                if relative == Path("providers.json"):
                    continue
                destination_path = destination / relative
                if source.is_dir():
                    destination_path.mkdir(parents=True, exist_ok=True)
                elif source.is_file():
                    destination_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination_path)
        report["target_sha256"] = _tree_digest(target)
        if remove_source:
            for name in COPY_DIRS:
                path = root / name
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
            _remove_exercise_history(root / "exercises")
        return report
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--remove-source", action="store_true")
    args = parser.parse_args()
    report = migrate(args.root, args.user_id, dry_run=args.dry_run,
                     remove_source=args.remove_source)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
