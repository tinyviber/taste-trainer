"""Minimal HTTP API + one-page dashboard.

Endpoints
  GET  /                          dashboard
  GET  /api/state                 latest exercise, inbox videos, job statuses
  POST /api/exercise/new?force=0  出题
  POST /api/exercise/{id}/submit  body: {"text": "..."} → 打分
  POST /api/analyze               multipart file 或 {"video": "name"} → 分析
  GET  /api/jobs                  in-memory + .a2h/runs/ job statuses
"""
import asyncio
import json
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from . import jobs
from .config import load_settings, ws_dirs
from .renders import load_meta, read

settings = load_settings()
dirs = ws_dirs(settings.workspace)
app = FastAPI(title="视频脚本训练服务")

JOBS: dict[str, dict] = {}


async def _auth(request: Request):
    if not settings.api_token:
        return
    token = request.headers.get("X-Token") or request.query_params.get("token")
    if token != settings.api_token:
        raise HTTPException(401, "bad token")


def _spawn(name: str, fn, *args):
    job_id = f"{name}-{int(time.time())}"
    JOBS[job_id] = {"status": "running", "detail": "", "started": time.time()}

    async def runner():
        try:
            meta = await asyncio.to_thread(fn, *args)
            JOBS[job_id].update(status="succeeded",
                                detail=json.dumps(meta, ensure_ascii=False)[:400])
        except Exception as e:  # noqa: BLE001 - surface to UI
            JOBS[job_id].update(status="failed", detail=str(e))
            try:
                jobs._write_run(dirs, job_id, name, "failed", str(e))
            except Exception:
                pass
    asyncio.create_task(runner())
    return job_id


# ------------------------------------------------------------------ api

@app.get("/api/state", dependencies=[Depends(_auth)])
def state():
    exs = []
    if dirs["exercises"].exists():
        for d in sorted(dirs["exercises"].iterdir()):
            if d.is_dir() and not d.name.startswith("_"):
                m = load_meta(d)
                if m.get("id"):
                    exs.append({"id": m["id"], "title": m.get("title"),
                                "status": m.get("status"),
                                "score": m.get("score")})
    videos = []
    for sub in ("videos", "inbox"):
        p = dirs[sub] if sub == "inbox" else dirs["videos"]
        if p.exists():
            videos += [{"name": f.name, "inbox": sub == "inbox"}
                       for f in sorted(p.iterdir())
                       if f.is_file() and not f.name.startswith(".")]
    latest = exs[-1] if exs else None
    submission = ""
    if latest and latest["status"] == "prompted":
        submission = read(dirs["exercises"] / latest["id"] / "submission.md")
    return {"latest_exercise": latest, "exercises": exs[-5:],
            "videos": videos, "jobs": JOBS,
            "submission_template": submission, "a2h_url": settings.a2h_url}


@app.post("/api/exercise/new", dependencies=[Depends(_auth)])
def exercise_new(force: int = 0):
    return {"job": _spawn("new_exercise", jobs.new_exercise, settings, bool(force))}


@app.post("/api/exercise/{exercise_id}/submit", dependencies=[Depends(_auth)])
async def exercise_submit(exercise_id: str, request: Request):
    body = await request.json()
    text = body.get("text", "")
    return {"job": _spawn("grade", jobs.grade, settings, exercise_id, text)}


@app.post("/api/analyze", dependencies=[Depends(_auth)])
async def analyze(request: Request, file: UploadFile | None = None):
    if file is not None and file.filename:
        dirs["inbox"].mkdir(parents=True, exist_ok=True)
        dest = dirs["inbox"] / Path(file.filename).name
        dest.write_bytes(await file.read())
        name = dest.name
    else:
        body = await request.json()
        name = body.get("video", "")
    if not name:
        raise HTTPException(400, "need file or video name")
    return {"job": _spawn("analyze", jobs.analyze, settings, name)}


@app.get("/api/jobs", dependencies=[Depends(_auth)])
def job_list():
    return JOBS


# ------------------------------------------------------------------ page

PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1">
<title>视频脚本训练</title>
<style>
body{font:15px/1.6 -apple-system,sans-serif;max-width:860px;margin:24px auto;padding:0 16px;color:#222}
.card{border:1px solid #ddd;border-radius:10px;padding:16px;margin:12px 0}
button{padding:8px 16px;border-radius:8px;border:1px solid #888;background:#fff;cursor:pointer}
button:hover{background:#f0f0f0}
textarea{width:100%;min-height:260px;font:13px/1.5 ui-monospace,monospace;padding:10px;border:1px solid #ccc;border-radius:8px;box-sizing:border-box}
.tag{display:inline-block;padding:2px 8px;border-radius:6px;background:#eee;font-size:12px;margin-right:6px}
.succeeded{background:#e6f7e6}.running{background:#fff3cd}.failed{background:#fde8e8}
#log{font:12px/1.5 ui-monospace,monospace;white-space:pre-wrap;color:#555}
a{color:#06c}
</style>
<h1>视频脚本训练</h1>
<p><a id="a2h" href="#">→ 打开 A2H 审阅视图</a></p>
<div class="card" id="ex"><h2>最新练习</h2><div id="exinfo">加载中…</div>
<div id="submitbox" style="display:none">
<p>把你的自然语言分镜粘到下面：</p>
<textarea id="sub" placeholder="00:00–00:05 画面……（按 submission.md 的结构写）"></textarea>
<button onclick="submitEx()">提交并打分</button></div></div>
<div class="card"><h2>操作</h2>
<button onclick="call('/api/exercise/new?force=0','POST')">出新题</button>
<button onclick="call('/api/exercise/new?force=1','POST')">跳过当前题重出</button></div>
<div class="card"><h2>分析视频</h2>
<div id="vids">加载中…</div>
<p>或上传新视频：<input type="file" id="up"><button onclick="upload()">上传并分析</button></p></div>
<div class="card"><h2>任务日志</h2><div id="log">—</div></div>
<script>
const tok = new URLSearchParams(location.search).get('token')||'';
const H = tok?{'X-Token':tok}:{};
async function call(u,m,opt={}){opt.method=m;opt.headers={...H,...(opt.headers||{})};
 const r=await fetch(u,opt);const j=await r.json();refresh();return j}
async function refresh(){
 const s=await (await fetch('/api/state',{headers:H})).json();
 document.getElementById('a2h').href=s.a2h_url;
 const e=s.latest_exercise;
 document.getElementById('exinfo').innerHTML=e?
  `<span class="tag">${e.id}</span><b>${e.title||''}</b>
   <span class="tag ${e.status}">${e.status}${e.score!=null?' '+e.score+'/100':''}</span>`
  :'还没有练习，点下方「出新题」。';
 document.getElementById('submitbox').style.display=
  e&&e.status==='prompted'?'block':'none';
 if(e&&e.status==='prompted'&&!document.getElementById('sub').value)
  document.getElementById('sub').value=s.submission_template||'';
 document.getElementById('vids').innerHTML=s.videos.length?
  s.videos.map(v=>`<div>${v.inbox?'📥 ':''}${v.name}
   <button onclick="call('/api/analyze','POST',{headers:{'Content-Type':'application/json'},
   body:JSON.stringify({video:'${v.name}'})})">分析</button></div>`).join('')
  :'videos/ 为空';
 document.getElementById('log').innerHTML=Object.entries(s.jobs).reverse()
  .map(([k,v])=>`<span class="tag ${v.status}">${v.status}</span>${k} ${v.detail||''}`)
  .join('<br>')||'—';
}
async function submitEx(){
 const e=(await (await fetch('/api/state',{headers:H})).json()).latest_exercise;
 if(!e)return;
 await call('/api/exercise/'+e.id+'/submit','POST',
  {headers:{'Content-Type':'application/json'},
   body:JSON.stringify({text:document.getElementById('sub').value})});
}
async function upload(){
 const f=document.getElementById('up').files[0];if(!f)return;
 const fd=new FormData();fd.append('file',f);
 await call('/api/analyze','POST',{body:fd});
}
refresh();setInterval(refresh,3000);
</script>"""


@app.get("/", response_class=HTMLResponse)
def page():
    return PAGE
