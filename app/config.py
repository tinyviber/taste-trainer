import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE = PROJECT_ROOT / "workspace"


@dataclass
class Settings:
    workspace: Path
    auth_db_path: Path
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_vision_model: str
    api_port: int = 8421
    a2h_url: str = ""
    provider_service_url: str = "http://127.0.0.1:8765"
    provider_internal_secret: str = ""
    web_origins: tuple[str, ...] = ()
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    max_upload_mb: int = 1024
    max_video_seconds: int = 1800


def load_settings() -> Settings:
    ws = os.environ.get("WORKSPACE_DIR", "").strip()
    workspace = Path(ws).expanduser().resolve() if ws else DEFAULT_WORKSPACE
    auth_db = os.environ.get("AUTH_DB_PATH", "").strip()
    auth_db_path = Path(auth_db).expanduser().resolve() if auth_db else workspace / "auth.sqlite3"
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
    key = os.environ.get("LLM_API_KEY", "")
    if not key:
        raise SystemExit("LLM_API_KEY is required")
    model = os.environ.get("LLM_MODEL", "gpt-4o")
    return Settings(
        workspace=workspace,
        auth_db_path=auth_db_path,
        llm_base_url=base,
        llm_api_key=key,
        llm_model=model,
        llm_vision_model=os.environ.get("LLM_VISION_MODEL", model),
        api_port=int(os.environ.get("API_PORT", "8421")),
        # A2H is intentionally disabled as a shared public viewer.  A future
        # per-user viewer must be explicitly opted into by the application.
        a2h_url="",
        provider_service_url=os.environ.get("PROVIDER_SERVICE_URL", "http://127.0.0.1:8765").rstrip("/"),
        provider_internal_secret=os.environ.get("PROVIDER_INTERNAL_SECRET", ""),
        web_origins=tuple(
            origin.strip().rstrip("/")
            for origin in os.environ.get(
                "WEB_ORIGINS", os.environ.get("WEB_ORIGIN", "http://127.0.0.1:5173")
            ).split(",") if origin.strip()
        ),
        ffmpeg=os.environ.get("FFMPEG_BIN", "ffmpeg"),
        ffprobe=os.environ.get("FFPROBE_BIN", "ffprobe"),
        max_upload_mb=int(os.environ.get("MAX_UPLOAD_MB", "1024")),
        max_video_seconds=int(os.environ.get("MAX_VIDEO_SECONDS", "1800")),
    )


# 工作区内的固定子路径
def ws_dirs(ws: Path) -> dict:
    return {
        "videos": ws / "videos",
        "inbox": ws / "videos" / "inbox",
        "runs": ws / "runs",
        "analyses": ws / "analyses",
        "exercises": ws / "exercises",
        "template": ws / "exercises" / "_template",
        "a2h": ws / ".a2h",
        "runs_meta": ws / ".a2h" / "runs",
        "index": ws / "exercises" / "INDEX.md",
        "rubric": ws / "RUBRIC.md",
        "pool": ws / "PROMPT_POOL.md",
        "principles": ws / "analyses" / "PRINCIPLES.md",
        "principles_meta": ws / "analyses" / "principles.json",
        "manifest": ws / ".a2h" / "manifest.json",
    }


def safe_child(root: Path, name: str) -> Path:
    """Resolve a user-provided relative name and keep it under ``root``."""
    if not name or Path(name).is_absolute():
        raise ValueError("path must be a non-empty relative path")
    root = root.resolve()
    candidate = (root / name).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("path escapes workspace")
    return candidate
