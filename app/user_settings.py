"""Small per-user, non-secret settings stored inside the user's workspace."""

from __future__ import annotations

import json
import os
from pathlib import Path


SETTINGS_FILENAME = ".trainer-settings.json"


def _path(workspace: Path) -> Path:
    return Path(workspace) / SETTINGS_FILENAME


def _value(value: object, label: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError(f"{label} 无效")
    return value.strip()


def load(workspace: Path) -> dict[str, str]:
    path = _path(workspace)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("用户设置无法读取") from exc
    if not isinstance(data, dict):
        raise RuntimeError("用户设置格式无效")
    return {
        "default_provider_id": _value(data.get("default_provider_id"), "provider"),
        "default_model_id": _value(data.get("default_model_id"), "model"),
    }


def normalize(default_provider_id: object, default_model_id: object) -> dict[str, str]:
    values = {
        "default_provider_id": _value(default_provider_id, "provider"),
        "default_model_id": _value(default_model_id, "model"),
    }
    if bool(values["default_provider_id"]) != bool(values["default_model_id"]):
        raise ValueError("provider 和 default model 必须同时设置")
    return values


def save(workspace: Path, default_provider_id: object, default_model_id: object) -> dict[str, str]:
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    values = normalize(default_provider_id, default_model_id)
    path = _path(workspace)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    os.chmod(path, 0o600)
    return values
