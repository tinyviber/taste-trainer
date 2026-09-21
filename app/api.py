"""FastAPI dashboard, closed-account auth, and per-user training API."""

import asyncio
import hashlib
import hmac
import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request as UrlRequest
from urllib.request import urlopen
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import auth, jobs, manifest, user_settings as user_settings_store
from .config import load_settings, ws_dirs
from .principles import state as principles_state
from .principles import update_candidate
from .renders import load_meta, read


settings = load_settings()
auth.init_db(settings.auth_db_path)
app = FastAPI(title="视频脚本训练服务")

# A user-scoped lock keeps each user's markdown/index mutation sequence
# deterministic while allowing independent users to run jobs concurrently.
JOBS: dict[str, dict[str, dict]] = {}
JOBS_LOCK = threading.Lock()
USER_JOB_LOCKS: dict[str, threading.Lock] = {}
USER_JOB_LOCKS_GUARD = threading.Lock()
ACTIVE_SCOPES: set[tuple[str, str]] = set()
ACTIVE_SCOPES_LOCK = threading.Lock()

EXERCISE_DOCUMENTS = {
    "prompt": "prompt.md",
    "submission": "submission.md",
    "review": "review.md",
    "micro_revision": "micro_revision.md",
    "micro_feedback": "micro_feedback.md",
    "revision": "revision.md",
}


def _scheme(request: Request) -> str:
    return request.headers.get("x-forwarded-proto", request.url.scheme).split(",", 1)[0].strip()


def _set_auth_cookies(response: Response, session: auth.Session, request: Request) -> None:
    secure = _scheme(request) == "https"
    response.set_cookie(
        auth.SESSION_COOKIE, session.token, httponly=True, secure=secure,
        samesite="strict", path="/", max_age=auth.SESSION_TTL_SECONDS,
    )
    # This is readable by the same-origin frontend for the double-submit CSRF
    # check; the session cookie remains HttpOnly.
    response.set_cookie(
        "trainer_csrf", session.csrf_token, httponly=False, secure=secure,
        samesite="strict", path="/", max_age=auth.SESSION_TTL_SECONDS,
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    response.delete_cookie("trainer_csrf", path="/")


def _check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in settings.web_origins:
        raise HTTPException(403, "origin not allowed")
    referer = request.headers.get("referer")
    if not origin and referer:
        parsed = urlsplit(referer)
        referer_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        if referer_origin not in settings.web_origins:
            raise HTTPException(403, "origin not allowed")


async def _auth(request: Request) -> auth.User:
    token = request.cookies.get(auth.SESSION_COOKIE)
    session = auth.resolve_session(settings.auth_db_path, token)
    if session is None:
        raise HTTPException(401, "请先登录")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        _check_origin(request)
        if not auth.verify_csrf(
            settings.auth_db_path, token or "", request.headers.get(auth.CSRF_HEADER)
        ):
            raise HTTPException(403, "csrf validation failed")
    request.state.auth_session = session
    return session.user


def _user_settings(user: auth.User):
    workspace = auth.user_workspace(settings.workspace, user.id)
    workspace.mkdir(parents=True, exist_ok=True)
    selection = user_settings_store.load(workspace)
    return replace(
        settings,
        workspace=workspace,
        a2h_url="",
        user_id=user.id,
        default_provider_id=selection["default_provider_id"],
        default_model_id=selection["default_model_id"],
    )


def _user_job_lock(user_id: str) -> threading.Lock:
    with USER_JOB_LOCKS_GUARD:
        return USER_JOB_LOCKS.setdefault(user_id, threading.Lock())


def _update_job(user_id: str, job_id: str, **values) -> None:
    with JOBS_LOCK:
        JOBS.setdefault(user_id, {})[job_id].update(values)


def _spawn(user: auth.User, user_settings, name: str, fn, *args,
           scope: str | None = None):
    scope_key = (user.id, scope) if scope else None
    with ACTIVE_SCOPES_LOCK:
        if scope_key and scope_key in ACTIVE_SCOPES:
            raise HTTPException(409, "相同任务正在运行")
        if scope_key:
            ACTIVE_SCOPES.add(scope_key)
    job_id = f"{name}-{uuid4().hex[:12]}"
    with JOBS_LOCK:
        JOBS.setdefault(user.id, {})[job_id] = {
            "status": "running", "detail": "", "started": time.time()
        }
    user_dirs = ws_dirs(user_settings.workspace, user_settings.training_assets_dir)

    async def runner():
        try:
            meta = await asyncio.to_thread(_run_serial, user.id, fn, args)
            _update_job(
                user.id, job_id, status="succeeded",
                detail=json.dumps(meta, ensure_ascii=False)[:400]
            )
        except Exception as exc:  # noqa: BLE001 - stable client-facing status
            _update_job(user.id, job_id, status="failed", detail="任务失败，请查看服务日志")
            try:
                jobs._write_run(user_dirs, job_id, name, "failed", str(exc))
            except Exception:
                pass
        finally:
            if scope_key:
                with ACTIVE_SCOPES_LOCK:
                    ACTIVE_SCOPES.discard(scope_key)

    asyncio.create_task(runner())
    return job_id


def _run_serial(user_id: str, fn, args):
    with _user_job_lock(user_id):
        return fn(*args)


def _update_principle_and_manifest(dirs, workspace, candidate_id, action, title, detail):
    candidate = update_candidate(dirs, candidate_id, action, title, detail)
    manifest.regenerate(workspace, dirs)
    return candidate


def _read_exercise_documents(exercise_dir: Path | None) -> dict[str, str]:
    if exercise_dir is None:
        return {key: "" for key in EXERCISE_DOCUMENTS}
    return {key: read(exercise_dir / filename) for key, filename in EXERCISE_DOCUMENTS.items()}


# ------------------------------------------------------------------ auth

@app.post("/api/auth/login")
async def login(request: Request):
    body = await request.json()
    username = str(body.get("username", "")).strip()
    password = body.get("password", "")
    client = request.client.host if request.client else "unknown"
    rate_key = f"login:{client}:{username.lower()}"
    if not auth.allow_rate(rate_key, limit=5, window_seconds=15 * 60):
        raise HTTPException(429, "登录尝试过多，请稍后再试")
    record = auth.get_user_with_hash(settings.auth_db_path, username)
    if not record or not isinstance(password, str) or not auth.verify_password(password, record[1]):
        raise HTTPException(401, "用户名或密码不正确")
    auth.clear_rate(rate_key)
    session = auth.create_session(settings.auth_db_path, record[0].id)
    response = JSONResponse({
        "user": {"id": session.user.id, "username": session.user.username},
        "csrfToken": session.csrf_token,
    })
    _set_auth_cookies(response, session, request)
    return response


@app.get("/api/auth/me")
async def me(request: Request):
    user = await _auth(request)
    return {"user": {"id": user.id, "username": user.username}}


@app.post("/api/auth/change-password")
async def change_password(request: Request):
    user = await _auth(request)
    body = await request.json()
    current = body.get("currentPassword", "")
    new = body.get("newPassword", "")
    if not isinstance(current, str) or not isinstance(new, str):
        raise HTTPException(400, "密码格式无效")
    try:
        session = auth.change_password(settings.auth_db_path, user.id, current, new)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    response = JSONResponse({
        "user": {"id": user.id, "username": user.username},
        "csrfToken": session.csrf_token,
    })
    _set_auth_cookies(response, session, request)
    return response


@app.post("/api/auth/logout")
async def logout(request: Request):
    await _auth(request)
    auth.revoke_session(settings.auth_db_path, request.cookies.get(auth.SESSION_COOKIE))
    response = JSONResponse({"ok": True})
    _clear_auth_cookies(response)
    return response


# ------------------------------------------------------------------ provider proxy

def _provider_signature(timestamp: str, nonce: str, method: str, path: str,
                       body: bytes, user_id: str) -> str:
    if not settings.provider_internal_secret:
        raise RuntimeError("provider internal authentication is not configured")
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = "\n".join((timestamp, nonce, method.upper(), path, body_hash, user_id))
    return hmac.new(
        settings.provider_internal_secret.encode("utf-8"),
        canonical.encode("utf-8"), hashlib.sha256,
    ).hexdigest()


def _proxy_provider(path: str, query: str, method: str, body: bytes,
                    user: auth.User) -> tuple[int, bytes, str]:
    timestamp = str(int(time.time()))
    nonce = uuid4().hex
    canonical_path = "/" + path.strip("/")
    if query:
        canonical_path += "?" + query
    signature = _provider_signature(timestamp, nonce, method, canonical_path, body, user.id)
    url = f"{settings.provider_service_url}/{path.strip('/') }"
    if query:
        url += "?" + query
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Provider-User": user.id,
        "X-Provider-Timestamp": timestamp,
        "X-Provider-Nonce": nonce,
        "X-Provider-Signature": signature,
    }
    request = UrlRequest(url, data=body or None, headers=headers, method=method.upper())
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, response.read(), response.headers.get_content_type()
    except HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get_content_type()
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("provider service unavailable") from exc


async def _provider_proxy(path: str, request: Request):
    user = await _auth(request)
    if request.method == "POST" and (
        path == "providers/test" or path.endswith("/discover") or path.endswith("/chat") or path.endswith("/completions")
    ):
        client = request.client.host if request.client else "unknown"
        if not auth.allow_rate(
            f"provider:{user.id}:{client}:{path}", limit=20, window_seconds=60
        ):
            raise HTTPException(429, "provider 请求过多，请稍后再试")
    body = await request.body()
    try:
        status, content, media_type = await asyncio.to_thread(
            _proxy_provider, path, request.url.query, request.method, body, user
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return Response(content=content, status_code=status, media_type=media_type)


@app.api_route("/api/provider-api/{path:path}", methods=["GET", "POST", "DELETE"])
async def provider_proxy(path: str, request: Request):
    return await _provider_proxy(path, request)


# ------------------------------------------------------------------ training api

@app.get("/api/settings")
async def settings_get(request: Request):
    user = await _auth(request)
    return user_settings_store.load(auth.user_workspace(settings.workspace, user.id))


@app.put("/api/settings")
async def settings_put(request: Request):
    user = await _auth(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "设置格式无效")
    provider_id = body.get("default_provider_id", body.get("defaultProviderId"))
    model_id = body.get("default_model_id", body.get("defaultModelId"))
    try:
        values = user_settings_store.normalize(provider_id, model_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if values["default_provider_id"]:
        status, content, _ = await asyncio.to_thread(
            _proxy_provider, "providers", "", "GET", b"", user
        )
        if status != 200:
            raise HTTPException(503, "provider service unavailable")
        try:
            payload = json.loads(content)
            providers = payload.get("providers", []) if isinstance(payload, dict) else []
        except (TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(503, "provider service returned invalid data") from exc
        if not isinstance(providers, list):
            raise HTTPException(503, "provider service returned invalid data")
        provider = next(
            (item for item in providers if isinstance(item, dict)
             and item.get("id") == values["default_provider_id"]),
            None,
        )
        selected = provider.get("selectedModelIds", []) if provider else []
        if not isinstance(selected, list) or values["default_model_id"] not in selected:
            raise HTTPException(400, "请选择当前 provider 中已勾选的模型")
    return user_settings_store.save(
        auth.user_workspace(settings.workspace, user.id),
        values["default_provider_id"], values["default_model_id"],
    )

def _state_snapshot(user_id: str, user_settings) -> dict:
    with _user_job_lock(user_id):
        dirs = ws_dirs(user_settings.workspace, user_settings.training_assets_dir)
        ex_records = []
        if dirs["exercises"].exists():
            for directory in sorted(dirs["exercises"].iterdir()):
                if directory.is_dir() and not directory.name.startswith("_"):
                    metadata = load_meta(directory)
                    if metadata.get("id"):
                        ex_records.append((directory, metadata))
        ex_records.sort(key=lambda item: item[1].get("created_at", item[1].get("id", item[0].name)))
        exercises = [
            {"id": metadata["id"], "title": metadata.get("title"),
             "status": metadata.get("status"), "score": metadata.get("score")}
            for _, metadata in ex_records
        ]
        latest_dir = ex_records[-1][0] if ex_records else None
        videos = []
        for sub in ("videos", "inbox"):
            directory = dirs[sub] if sub == "inbox" else dirs["videos"]
            if directory.exists():
                videos += [
                    {"name": item.name, "inbox": sub == "inbox"}
                    for item in sorted(directory.iterdir())
                    if item.is_file() and not item.name.startswith(".")
                ]
        latest = exercises[-1] if exercises else None
        submission = ""
        micro_focus = None
        if latest and latest["status"] == "prompted":
            submission = read(dirs["exercises"] / latest["id"] / "submission.md")
        if latest and latest["status"] == "needs_micro_revision":
            micro_focus = load_meta(dirs["exercises"] / latest["id"]).get("micro_revision_focus")
        documents = _read_exercise_documents(latest_dir)
    with JOBS_LOCK:
        jobs_snapshot = dict(JOBS.get(user_id, {}))
    return {
        "latest_exercise": latest,
        "exercises": exercises[-5:],
        "videos": videos,
        "jobs": jobs_snapshot,
        "submission_template": submission,
        "micro_focus": micro_focus,
        "documents": documents,
        "a2h_url": "",
    }


@app.get("/api/state")
async def state(request: Request):
    user = await _auth(request)
    user_settings = _user_settings(user)
    return await asyncio.to_thread(_state_snapshot, user.id, user_settings)


@app.post("/api/exercise/new")
async def exercise_new(request: Request):
    user = await _auth(request)
    user_settings = _user_settings(user)
    force = int(request.query_params.get("force", "0"))
    return {"job": _spawn(user, user_settings, "new_exercise", jobs.new_exercise,
                           user_settings, bool(force), scope="new_exercise")}


@app.post("/api/exercise/{exercise_id}/submit")
async def exercise_submit(exercise_id: str, request: Request):
    user = await _auth(request)
    body = await request.json()
    user_settings = _user_settings(user)
    return {"job": _spawn(user, user_settings, "grade", jobs.grade,
                           user_settings, exercise_id, body.get("text", ""),
                           scope=f"grade:{exercise_id}")}


@app.post("/api/exercise/{exercise_id}/micro-revise")
async def micro_revise(exercise_id: str, request: Request):
    user = await _auth(request)
    body = await request.json()
    user_settings = _user_settings(user)
    return {"job": _spawn(user, user_settings, "micro_revision", jobs.complete_micro_revision,
                           user_settings, exercise_id, body.get("text", ""),
                           scope=f"micro:{exercise_id}")}


@app.post("/api/analyze")
async def analyze(request: Request, file: UploadFile | None = None):
    user = await _auth(request)
    user_settings = _user_settings(user)
    dirs = ws_dirs(user_settings.workspace, user_settings.training_assets_dir)
    if file is not None and file.filename:
        dirs["inbox"].mkdir(parents=True, exist_ok=True)
        original = Path(file.filename).name
        dest = dirs["inbox"] / original
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}-{uuid4().hex[:8]}{dest.suffix}")
        limit = user_settings.max_upload_mb * 1024 * 1024
        size = 0
        try:
            with dest.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"file exceeds {user_settings.max_upload_mb} MB")
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
    if not auth.allow_rate(f"analyze:{user.id}", limit=10, window_seconds=60):
        raise HTTPException(429, "分析请求过多，请稍后再试")
    return {"job": _spawn(user, user_settings, "analyze", jobs.analyze, user_settings, name,
                           scope=f"analyze:{name}")}


@app.get("/api/jobs")
async def job_list(request: Request):
    user = await _auth(request)
    with JOBS_LOCK:
        return dict(JOBS.get(user.id, {}))


def _principles_snapshot(user_id: str, user_settings) -> dict:
    with _user_job_lock(user_id):
        return principles_state(
            ws_dirs(user_settings.workspace, user_settings.training_assets_dir)
        )


@app.get("/api/principles")
async def principles(request: Request):
    user = await _auth(request)
    user_settings = _user_settings(user)
    return await asyncio.to_thread(_principles_snapshot, user.id, user_settings)


@app.post("/api/principles/{candidate_id}")
async def principle_action(candidate_id: str, request: Request):
    user = await _auth(request)
    body = await request.json()
    user_settings = _user_settings(user)
    dirs = ws_dirs(user_settings.workspace, user_settings.training_assets_dir)
    try:
        candidate = await asyncio.to_thread(
            _run_serial,
            user.id,
            _update_principle_and_manifest,
            (dirs, user_settings.workspace, candidate_id, body.get("action", ""),
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
def page():
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><title>Taste Trainer</title>"
        "<p>前端尚未构建。开发时运行 <code>cd web && npm run dev</code>；"
        "生产环境先运行 <code>npm run build</code>。</p>",
        status_code=503,
    )
