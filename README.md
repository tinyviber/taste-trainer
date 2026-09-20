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
│   ├── api.py        FastAPI 业务 API + React 静态文件入口
│   ├── jobs.py       三个 LLM 任务
│   ├── llm.py        OpenAI-compatible 客户端（chat_json + 视觉图传）
│   ├── pipeline.py   ffmpeg 抽帧/拼图
│   ├── renders.py    JSON → markdown 渲染器 + INDEX.md 重生成
│   ├── manifest.py   .a2h/manifest.json 重生成（策展规则内置）
│   ├── principles.py 人工确认的口味原则候选与 ledger
│   ├── schemas.py    LLM 评分 JSON 校验与归一化
│   └── prompts/      每个任务的 system prompt（{{var}} 占位符）
├── web/              Vite + React + TypeScript 仪表盘
└── provider-service/ Node AI SDK provider registry 与模型发现服务
├── deploy/           setup.sh + systemd 单元
└── scripts/          rsync-data.sh.example
```

## 本地开发

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 填 WORKSPACE_DIR / LLM_* 
uvicorn app.api:app --port 8421
# 另一个终端：cd provider-service && npm install && npm run dev
# 再一个终端：cd web && npm install && npm run dev
# 另一个终端：a2h render $WORKSPACE_DIR -w -p 8420
```

打开 http://localhost:5173 出题/提交/上传分析。评审后先完成一个 micro-v2，
再展开 LLM 生成的完整 revision；视频分析产生的原则候选需要人工接受后才会
进入 `analyses/PRINCIPLES.md`。

### 前端与 provider

原来的前端是 `app/api.py` 里的内嵌 HTML + 原生 JavaScript。现在前端改为
Vite + React + TypeScript：React 只负责界面和交互，Python FastAPI 继续负责
训练任务、视频处理和数据一致性。

`provider-service/` 使用 Vercel AI SDK 的
`@ai-sdk/openai-compatible` 封装自定义 `baseURL` 的 provider，并用
`createProviderRegistry` 管理多个 provider。Providers 页面保存 name/base URL/API
key 到 `WORKSPACE_DIR/.a2h/providers.json`（权限 0600），浏览器只收到掩码状态和
模型列表。点击“检测模型”会由服务端请求 `<baseURL>/models`，勾选项会限制可用的
`providerId:modelId`；“全部/取消全部”只修改当前 provider 的允许模型集合。

生产环境先构建 `web` 和 `provider-service`，FastAPI 会提供 `web/dist`；把
`deploy/nginx.conf.example` 中的 `/provider-api/` 反向代理配置加入 Nginx，
再启用 `trainer-api`、`provider-service` 和 `a2h-view`。

## VPS 部署

```bash
git clone <repo> /srv/video-trainer/service
sudo bash /srv/video-trainer/service/deploy/setup.sh
# 配 .env → rsync 数据（scripts/rsync-data.sh.example）→ 装 systemd 单元
sudo cp deploy/*.service /etc/systemd/system/  # 改路径/用户
sudo systemctl enable --now trainer-api provider-service a2h-view
```

## 接口速查

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/` | 仪表盘 |
| GET | `/api/state` | 最新练习/视频列表/任务状态 |
| POST | `/api/exercise/new?force=0` | 出题（force=1 跳过当前题） |
| POST | `/api/exercise/{id}/submit` | `{"text":"..."}` → 打分 |
| POST | `/api/exercise/{id}/micro-revise` | 提交局部改写并展开完整 revision |
| POST | `/api/analyze` | multipart 上传 或 `{"video":"name"}` |
| GET | `/api/jobs` | 任务状态 |
| GET | `/api/principles` | 查看原则候选与已接受原则 |
| POST | `/api/principles/{candidate_id}` | 接受、修改后接受或拒绝候选 |

设了 `API_TOKEN` 后 API 请求需带 `X-Token`；浏览器可首次用 `/?token=...`
引导，服务会立即跳回无 token 的 URL 并写入 HttpOnly cookie。

## 数据约定（工作区侧）

- `exercises/<slug>/meta.json`：练习状态机 `prompted → submitted → needs_micro_revision → reviewed|skipped`
- `exercises/<slug>/grade.json`：完整、校验过的十维评分 JSON；`total` 由服务端计算
- `exercises/<slug>/micro_revision.md`：提交的局部 v2；提交后才生成 `revision.md`
- `analyses/<slug>/meta.json`：标题/摘要/stats/keyframe_notes
- `analyses/<slug>/frame_manifest.json`：frame id 到真实 timestamp 的映射
- `analyses/<slug>/principles_candidates.json`：待人工确认的原则候选
- 视频分析的报告按关键段落、镜头转换和注意力事件组织，不伪造逐秒 transcription
- `analyses/PRINCIPLES.md`：口味基准，评审时注入，越用越准
- `analyses/principles.json`：已接受原则的机器可读 ledger
- `videos/inbox/`：丢视频进去即可分析
- `runs/`：中间产物，可再生，rsync 时排除
