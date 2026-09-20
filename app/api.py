"""Minimal HTTP API + one-page dashboard.

Endpoints
  GET  /                          dashboard
  GET  /api/state                 latest exercise, inbox videos, job statuses
  POST /api/exercise/new?force=0  出题
  POST /api/exercise/{id}/submit  body: {"text": "..."} → 打分
  POST /api/exercise/{id}/micro-revise  body: {"text": "..."} → 展开 revision
  POST /api/analyze               multipart file 或 {"video": "name"} → 分析
  GET/POST /api/principles         原则候选与人工确认
  GET  /api/jobs                  in-memory + .a2h/runs/ job statuses
"""
import asyncio
import json
import threading
import time
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import jobs, manifest
from .config import load_settings, ws_dirs
from .principles import state as principles_state
from .principles import update_candidate
from .renders import load_meta, read

settings = load_settings()
dirs = ws_dirs(settings.workspace)
app = FastAPI(title="视频脚本训练服务")

JOBS: dict[str, dict] = {}
JOB_LOCK = threading.Lock()
ACTIVE_SCOPES: set[str] = set()


async def _auth(request: Request):
    if not settings.api_token:
        return
    token = (request.headers.get("X-Token") or request.cookies.get("trainer_token")
             or request.query_params.get("token"))
    if token != settings.api_token:
        raise HTTPException(401, "bad token")


def _spawn(name: str, fn, *args, scope: str | None = None):
    if scope and scope in ACTIVE_SCOPES:
        raise HTTPException(409, "相同任务正在运行")
    if scope:
        ACTIVE_SCOPES.add(scope)
    job_id = f"{name}-{uuid4().hex[:12]}"
    JOBS[job_id] = {"status": "running", "detail": "", "started": time.time()}

    async def runner():
        try:
            # The service is intentionally single-user. Serialising file
            # mutations keeps INDEX/manifest/meta consistent without adding a
            # queue dependency.
            meta = await asyncio.to_thread(_run_serial, fn, args)
            JOBS[job_id].update(status="succeeded",
                                detail=json.dumps(meta, ensure_ascii=False)[:400])
        except Exception as e:  # noqa: BLE001 - surface to UI
            JOBS[job_id].update(status="failed", detail=str(e))
            try:
                jobs._write_run(dirs, job_id, name, "failed", str(e))
            except Exception:
                pass
        finally:
            if scope:
                ACTIVE_SCOPES.discard(scope)
    asyncio.create_task(runner())
    return job_id


def _run_serial(fn, args):
    with JOB_LOCK:
        return fn(*args)


def _update_principle_and_manifest(dirs, workspace, candidate_id, action, title, detail):
    candidate = update_candidate(dirs, candidate_id, action, title, detail)
    manifest.regenerate(workspace, dirs)
    return candidate


# ------------------------------------------------------------------ api

@app.get("/api/state", dependencies=[Depends(_auth)])
def state():
    ex_records = []
    if dirs["exercises"].exists():
        for d in sorted(dirs["exercises"].iterdir()):
            if d.is_dir() and not d.name.startswith("_"):
                m = load_meta(d)
                if m.get("id"):
                    ex_records.append((d, m))
    ex_records.sort(key=lambda item: item[1].get(
        "created_at", item[1].get("id", item[0].name)))
    exs = [{"id": m["id"], "title": m.get("title"),
            "status": m.get("status"), "score": m.get("score")}
           for _, m in ex_records]
    videos = []
    for sub in ("videos", "inbox"):
        p = dirs[sub] if sub == "inbox" else dirs["videos"]
        if p.exists():
            videos += [{"name": f.name, "inbox": sub == "inbox"}
                       for f in sorted(p.iterdir())
                       if f.is_file() and not f.name.startswith(".")]
    latest = exs[-1] if exs else None
    submission = ""
    micro_focus = None
    if latest and latest["status"] == "prompted":
        submission = read(dirs["exercises"] / latest["id"] / "submission.md")
    if latest and latest["status"] == "needs_micro_revision":
        latest_meta = load_meta(dirs["exercises"] / latest["id"])
        micro_focus = latest_meta.get("micro_revision_focus")
    return {"latest_exercise": latest, "exercises": exs[-5:],
            "videos": videos, "jobs": JOBS,
            "submission_template": submission, "micro_focus": micro_focus,
            "a2h_url": settings.a2h_url}


@app.post("/api/exercise/new", dependencies=[Depends(_auth)])
def exercise_new(force: int = 0):
    return {"job": _spawn("new_exercise", jobs.new_exercise, settings, bool(force),
                           scope="new_exercise")}


@app.post("/api/exercise/{exercise_id}/submit", dependencies=[Depends(_auth)])
async def exercise_submit(exercise_id: str, request: Request):
    body = await request.json()
    text = body.get("text", "")
    return {"job": _spawn("grade", jobs.grade, settings, exercise_id, text,
                           scope=f"grade:{exercise_id}")}


@app.post("/api/exercise/{exercise_id}/micro-revise", dependencies=[Depends(_auth)])
async def micro_revise(exercise_id: str, request: Request):
    body = await request.json()
    text = body.get("text", "")
    return {"job": _spawn("micro_revision", jobs.complete_micro_revision,
                           settings, exercise_id, text,
                           scope=f"micro:{exercise_id}")}


@app.post("/api/analyze", dependencies=[Depends(_auth)])
async def analyze(request: Request, file: UploadFile | None = None):
    if file is not None and file.filename:
        dirs["inbox"].mkdir(parents=True, exist_ok=True)
        original = Path(file.filename).name
        dest = dirs["inbox"] / original
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}-{uuid4().hex[:8]}{dest.suffix}")
        limit = settings.max_upload_mb * 1024 * 1024
        size = 0
        try:
            with dest.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"file exceeds {settings.max_upload_mb} MB")
                    output.write(chunk)
        except Exception:
            dest.unlink(missing_ok=True)
            raise
        name = dest.name
    else:
        body = await request.json()
        name = body.get("video", "")
    if not name:
        raise HTTPException(400, "need file or video name")
    return {"job": _spawn("analyze", jobs.analyze, settings, name,
                           scope=f"analyze:{name}")}


@app.get("/api/jobs", dependencies=[Depends(_auth)])
def job_list():
    return JOBS


@app.get("/api/principles", dependencies=[Depends(_auth)])
def principles():
    return principles_state(dirs)


@app.post("/api/principles/{candidate_id}", dependencies=[Depends(_auth)])
async def principle_action(candidate_id: str, request: Request):
    body = await request.json()
    try:
        candidate = await asyncio.to_thread(
            _run_serial,
            _update_principle_and_manifest,
            (dirs, settings.workspace, candidate_id, body.get("action", ""),
             body.get("title", ""), body.get("detail", "")),
        )
        return {"candidate": candidate}
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc


# ------------------------------------------------------------------ page

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/", response_class=HTMLResponse)
def page(request: Request):
    # Allow a one-time bootstrap link but immediately remove the token from
    # browser history, then use an HttpOnly cookie for subsequent API calls.
    token = request.query_params.get("token")
    if settings.api_token and token:
        if token != settings.api_token:
            raise HTTPException(401, "bad token")
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            "trainer_token", token, httponly=True, samesite="strict",
            secure=request.url.scheme == "https",
        )
        return response
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><title>Taste Trainer</title>"
        "<p>前端尚未构建。开发时运行 <code>cd web && npm run dev</code>；"
        "生产环境先运行 <code>npm run build</code>。</p>",
        status_code=503,
    )
