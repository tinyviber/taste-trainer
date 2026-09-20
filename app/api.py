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
#microfocus{white-space:pre-wrap;background:#fafafa;padding:10px;border-radius:6px}
.candidate{border-top:1px solid #ddd;padding:12px 0}.candidate textarea{min-height:90px}
.candidate button{margin:8px 8px 0 0}
a{color:#06c}
</style>
<h1>视频脚本训练</h1>
<p><a id="a2h" href="#">→ 打开 A2H 审阅视图</a></p>
<div class="card" id="ex"><h2>最新练习</h2><div id="exinfo">加载中…</div>
<div id="submitbox" style="display:none">
<p>把你的自然语言分镜粘到下面：</p>
<textarea id="sub" placeholder="00:00–00:05 画面……（按 submission.md 的结构写）"></textarea>
<button onclick="submitEx()">提交并打分</button></div></div>
<div class="card" id="microbox" style="display:none">
<h2>先做一个 micro-v2</h2><p id="microfocus"></p>
<textarea id="micro" placeholder="只重写上面指出的一个 5–10 秒片段"></textarea>
<button onclick="submitMicro()">提交 micro-v2，查看完整 revision</button></div>
<div class="card"><h2>操作</h2>
<button onclick="call('/api/exercise/new?force=0','POST').catch(err=>alert(err.message))">出新题</button>
<button onclick="call('/api/exercise/new?force=1','POST').catch(err=>alert(err.message))">跳过当前题重出</button></div>
<div class="card"><h2>分析视频</h2>
<div id="vids">加载中…</div>
<p>或上传新视频：<input type="file" id="up"><button onclick="upload()">上传并分析</button></p></div>
<div class="card"><h2>口味原则候选</h2><p>视频分析产生的原则不会自动进入评分基准，请人工接受或拒绝。</p><div id="principles">加载中…</div></div>
<div class="card"><h2>任务日志</h2><div id="log">—</div></div>
<script>
const H = {};
let currentExercise = null;
let refreshing = false;
function make(tag, text='', className=''){
 const el=document.createElement(tag);if(text)el.textContent=text;
 if(className)el.className=className;return el;
}
async function call(u,m,opt={}){
 opt.method=m;opt.headers={...H,...(opt.headers||{})};
 const r=await fetch(u,opt);const j=await r.json();
 if(!r.ok)throw new Error(j.detail||'请求失败');
 await refresh();return j
}
async function refresh(){
 if(refreshing)return;refreshing=true;
 try{
  const [sr,pr]=await Promise.all([
   fetch('/api/state',{headers:H}),fetch('/api/principles',{headers:H})]);
  const s=await sr.json(),p=await pr.json();
  if(!sr.ok||!pr.ok)throw new Error(s.detail||p.detail||'刷新失败');
  document.getElementById('a2h').href=s.a2h_url;
  currentExercise=s.latest_exercise;
  renderExercise(s);renderVideos(s.videos);renderPrinciples(p);renderJobs(s.jobs);
 }catch(err){document.getElementById('log').textContent=err.message}
 finally{refreshing=false}
}
function renderExercise(s){
 const box=document.getElementById('exinfo');box.replaceChildren();
 const e=s.latest_exercise;
 if(!e){box.textContent='还没有练习，点下方「出新题」。'}
 else{
  box.append(make('span',e.id,'tag'),make('b',e.title||''),
   make('span',e.status+(e.score!=null?' '+e.score+'/100':''),'tag '+e.status));
 }
 const submit=document.getElementById('submitbox');
 submit.style.display=e&&e.status==='prompted'?'block':'none';
 if(e&&e.status==='prompted'&&!document.getElementById('sub').value)
  document.getElementById('sub').value=s.submission_template||'';
 const micro=document.getElementById('microbox');
 micro.style.display=e&&e.status==='needs_micro_revision'?'block':'none';
 if(e&&e.status==='needs_micro_revision'){
  const f=s.micro_focus||{};
  document.getElementById('microfocus').textContent=
   `${f.dim||e.weakest||'最弱维度'}\n原方案：${f.original||'—'}\n差距：${f.gap||'请重写一个 5–10 秒片段。'}`;
 }
}
function renderVideos(videos){
 const box=document.getElementById('vids');box.replaceChildren();
 if(!videos.length){box.textContent='videos/ 为空';return}
 for(const v of videos){
  const row=make('div');row.append(document.createTextNode((v.inbox?'📥 ':'')+v.name+' '));
  const button=make('button','分析');
  button.addEventListener('click',()=>call('/api/analyze','POST',{
   headers:{'Content-Type':'application/json'},body:JSON.stringify({video:v.name})
  }).catch(err=>alert(err.message)));
  row.append(button);box.append(row);
 }
}
function renderPrinciples(data){
 const box=document.getElementById('principles');box.replaceChildren();
 if(!data.pending.length){box.textContent='暂无待确认的原则候选。';return}
 for(const c of data.pending){
  const card=make('div','', 'candidate');
  const title=make('input');title.value=c.title;title.style.width='100%';
  const detail=make('textarea');detail.value=c.detail;detail.style.minHeight='90px';
  const evidence=make('p','证据：'+(Array.isArray(c.evidence)?JSON.stringify(c.evidence):c.evidence||'—'));
  const source=make('small','来源：'+(c.source_video||'—'));
  const accept=make('button','修改后接受');
  accept.addEventListener('click',()=>call('/api/principles/'+encodeURIComponent(c.id),'POST',{
   headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'accept',title:title.value,detail:detail.value})
  }).catch(err=>alert(err.message)));
  const reject=make('button','拒绝');
  reject.addEventListener('click',()=>call('/api/principles/'+encodeURIComponent(c.id),'POST',{
   headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'reject'})
  }).catch(err=>alert(err.message)));
  card.append(title,detail,evidence,source,accept,reject);box.append(card);
 }
}
function renderJobs(jobs){
 const box=document.getElementById('log');box.replaceChildren();
 const entries=Object.entries(jobs).reverse();
 if(!entries.length){box.textContent='—';return}
 for(const [key,value] of entries){
  const row=make('div');row.append(make('span',value.status,'tag '+value.status),
   document.createTextNode(key+' '+(value.detail||'')));box.append(row);
 }
}
async function submitEx(){
 if(!currentExercise)return;
 try{await call('/api/exercise/'+encodeURIComponent(currentExercise.id)+'/submit','POST',
  {headers:{'Content-Type':'application/json'},body:JSON.stringify({text:document.getElementById('sub').value})})}
 catch(err){alert(err.message)}
}
async function submitMicro(){
 if(!currentExercise)return;
 try{await call('/api/exercise/'+encodeURIComponent(currentExercise.id)+'/micro-revise','POST',
  {headers:{'Content-Type':'application/json'},body:JSON.stringify({text:document.getElementById('micro').value})})}
 catch(err){alert(err.message)}
}
async function upload(){
 const f=document.getElementById('up').files[0];if(!f)return;
 const fd=new FormData();fd.append('file',f);
 try{await call('/api/analyze','POST',{body:fd})}catch(err){alert(err.message)}
}
refresh();setInterval(refresh,3000);
</script>"""


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
    return PAGE
