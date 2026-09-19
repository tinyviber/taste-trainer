# 视频脚本训练服务（service）

把原本需要交互式 agent 做的三件事——**出题、打分、分析视频**——变成
调用 OpenAI-compatible LLM API 的自动化服务。数据（工作区）与代码
完全分离：本目录只含代码，推到 GitHub；数据目录通过 `WORKSPACE_DIR`
环境变量指向，rsync 搬运，不进 git。

## 工作方式

LLM 只产出两类东西：

1. **给人看的 markdown**：prompt.md / review.md / revision.md / 分析.md
2. **给机器看的 meta.json**：分数、最弱维度、下次重点、关键帧列表

INDEX.md 和 `.a2h/manifest.json` 由脚本根据 meta.json **确定性重新生成**，
LLM 不手改结构化文件。每次任务还会写 `.a2h/runs/<job>.json`，进度直接
显示在 A2H 视图里。

## 目录

```
service/
├── app/
│   ├── api.py        FastAPI + 单页仪表盘（出题按钮/提交框/上传视频）
│   ├── jobs.py       三个 LLM 任务
│   ├── llm.py        OpenAI-compatible 客户端（chat_json + 视觉图传）
│   ├── pipeline.py   ffmpeg 抽帧/拼图/音频
│   ├── renders.py    JSON → markdown 渲染器 + INDEX.md 重生成
│   ├── manifest.py   .a2h/manifest.json 重生成（策展规则内置）
│   └── prompts/      每个任务的 system prompt（{{var}} 占位符）
├── deploy/           setup.sh + systemd 单元
└── scripts/          rsync-data.sh.example
```

## 本地开发

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 填 WORKSPACE_DIR / LLM_* 
uvicorn app.api:app --port 8421
# 另一个终端：a2h render $WORKSPACE_DIR -w -p 8420
```

打开 http://localhost:8421 出题/提交/上传分析。

## VPS 部署

```bash
git clone <repo> /srv/video-trainer/service
sudo bash /srv/video-trainer/service/deploy/setup.sh
# 配 .env → rsync 数据（scripts/rsync-data.sh.example）→ 装 systemd 单元
sudo cp deploy/*.service /etc/systemd/system/  # 改路径/用户
sudo systemctl enable --now trainer-api a2h-view
```

## 接口速查

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/` | 仪表盘 |
| GET | `/api/state` | 最新练习/视频列表/任务状态 |
| POST | `/api/exercise/new?force=0` | 出题（force=1 跳过当前题） |
| POST | `/api/exercise/{id}/submit` | `{"text":"..."}` → 打分 |
| POST | `/api/analyze` | multipart 上传 或 `{"video":"name"}` |
| GET | `/api/jobs` | 任务状态 |

设了 `API_TOKEN` 后请求需带 `X-Token` 头或 `?token=`。

## 数据约定（工作区侧）

- `exercises/<slug>/meta.json`：练习状态机 `prompted → submitted → reviewed|skipped`
- `analyses/<slug>/meta.json`：标题/摘要/stats/keyframe_notes
- `analyses/PRINCIPLES.md`：口味基准，评审时注入，越用越准
- `videos/inbox/`：丢视频进去即可分析
- `runs/`：中间产物，可再生，rsync 时排除
