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
│   ├── api.py        FastAPI 认证、用户隔离 API + React 静态文件入口
│   ├── auth.py       SQLite session/password auth（无公开注册）
│   ├── jobs.py       三个 LLM 任务
│   ├── llm.py        OpenAI-compatible 客户端（chat_json + 视觉图传）
│   ├── pipeline.py   ffmpeg 抽帧/拼图
│   ├── renders.py    JSON → markdown 渲染器 + INDEX.md 重生成
│   ├── manifest.py   .a2h/manifest.json 重生成（策展规则内置）
│   ├── principles.py 人工确认的口味原则候选与 ledger
│   ├── schemas.py    LLM 评分 JSON 校验与归一化
│   └── prompts/      每个任务的 system prompt（{{var}} 占位符）
├── web/              Vite + React + TypeScript 仪表盘
└── provider-service/ Node AI SDK provider registry 与加密模型发现服务
├── deploy/           setup.sh + systemd 单元
└── scripts/          rsync-data.sh.example
```

## 本地开发

```bash
bash scripts/local.sh
```

脚本会把数据放在项目根目录的 `workspace/`（也可通过环境变量
`WORKSPACE_DIR` 覆盖），自动准备依赖、运行测试和构建，然后启动 API、Provider
和 React。首次使用需在 `.env` 填入随机的 `PROVIDER_INTERNAL_SECRET` 和
`PROVIDER_ENCRYPTION_KEY`，再用 `python -m app.auth create-wj` 创建唯一账号。
打开 http://127.0.0.1:5173 登录后出题/提交/上传分析。
题目全文、提交内容、评分意见、
micro-v2 和完整 revision 都会直接显示在 React 页面；A2H 只作为可选的工作区深度
浏览器。评审后先完成一个 micro-v2，再展开 LLM 生成的完整 revision；视频分析产生
的原则候选需要人工接受后才会进入 `analyses/PRINCIPLES.md`。

只跑检查或构建时可用：

```bash
bash scripts/local.sh test
bash scripts/local.sh build
```

### 前端与 provider

原来的前端是 `app/api.py` 里的内嵌 HTML + 原生 JavaScript。现在前端改为
Vite + React + TypeScript：React 只负责界面和交互，Python FastAPI 继续负责
训练任务、视频处理和数据一致性。

`provider-service/` 使用 Vercel AI SDK 的
`@ai-sdk/openai-compatible` 封装自定义 `baseURL` 的 provider，并用
`createProviderRegistry` 管理多个 provider。浏览器只收到掩码状态和模型列表。
浏览器访问的是同源 `/api/provider-api/*`；FastAPI 验证 session/CSRF 后，通过带
时间戳、nonce 和 body 签名的本机通道调用 Node。Provider 数据按用户写入 AES-GCM
加密文件，密钥只放部署侧的 provider secret 文件。检测失败后可以修改 key/base URL
并直接重新检测，不需要刷新。
轮换密钥时停掉 provider service，备份加密目录后生成新 key，并以
`PROVIDER_ENCRYPTION_KEY=<new> PROVIDER_ROTATE_FROM_KEY=<old> node dist/server.js`
运行一次；成功后只保留新 key，再启动服务。

生产环境需要 Node.js 22 LTS 或更高版本（`provider-service` 的 AI SDK 7 和 Vite 7
都依赖它）。先构建 `web` 和 `provider-service`，FastAPI 会提供 `web/dist`；把
`deploy/nginx.conf.example` 中的 FastAPI 反向代理配置加入 Nginx（不要公开 Node
provider 端口），
并确保 `client_max_body_size` 不小于 `.env` 的 `MAX_UPLOAD_MB`，再启用
`trainer-api`、`provider-service`。不要启用共享的 `a2h-view`。

## VPS 部署

```bash
git clone <repo> /srv/video-trainer/service
sudo bash /srv/video-trainer/service/deploy/setup.sh
# 默认 workspace 是 /srv/video-trainer/service/workspace；准备两个 0600 env 文件：
# /etc/taste-trainer/api.env 与 /etc/taste-trainer/provider.env
sudo cp deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trainer-api provider-service

# 停服务后，先 dry-run，再把旧单用户 workspace 迁移到 wj 的 UUID 目录。
sudo -u videotrainer-api /srv/video-trainer/service/.venv/bin/python -m app.auth create-wj
sudo -u videotrainer-api /srv/video-trainer/service/.venv/bin/python \
  scripts/migrate_legacy_workspace.py --root /srv/video-trainer/service/workspace \
  --user-id '<wj UUID>' --dry-run
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
| POST | `/api/auth/login` | 登录（唯一账号由本机 CLI 创建） |
| POST | `/api/auth/change-password` | 修改密码并撤销旧 session |
| POST | `/api/auth/logout` | 注销当前 session |
| GET | `/api/provider-api/providers` | 当前用户的 provider（不含 API key） |

认证入口为 `/api/auth/login`、`/api/auth/me`、`/api/auth/logout` 和
`/api/auth/change-password`；没有注册接口。密码 hash、session hash 和用户 ID 存在
SQLite，训练数据存放于 `WORKSPACE_DIR/users/<user-uuid>/`。旧的 `API_TOKEN`、
`X-Token`、query token 和公网 `/provider-api/` 都不再是认证旁路。

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
