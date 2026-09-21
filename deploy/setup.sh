#!/usr/bin/env bash
# VPS 初始化：安装依赖 + 建数据目录。以 root 或 sudo 运行。
set -euo pipefail

SERVICE_DIR="${1:-/srv/video-trainer/service}"   # 本仓库部署位置
WORKSPACE_DIR="${2:-$SERVICE_DIR/workspace}" # 项目目录内的数据目录

# 1) 系统依赖
apt-get update
apt-get install -y ffmpeg python3-venv ca-certificates curl gnupg

# Vite 7 and AI SDK 7 require Node.js 22+. Install it system-wide so the
# provider systemd unit can use the stable /usr/bin/node path (no nvm in a
# service account). A distro's nodejs package is not guaranteed to be new
# enough, so use the signed NodeSource 22.x repository when necessary.
export PATH="/usr/bin:/bin:/usr/local/bin:$PATH"
NODE_BIN="/usr/bin/node"
node_version=""
node_major=0
if [[ -x "$NODE_BIN" ]]; then
  node_version="$($NODE_BIN --version)"
  if [[ "$node_version" =~ ^v([0-9]+)\. ]]; then
    node_major="${BASH_REMATCH[1]}"
  fi
fi

if (( node_major < 22 )); then
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    | gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
  chmod a+r /etc/apt/keyrings/nodesource.gpg
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main\n' \
    "$(dpkg --print-architecture)" > /etc/apt/sources.list.d/nodesource.list
  apt-get update
  apt-get install -y nodejs
fi

if [[ ! -x "$NODE_BIN" ]]; then
  echo "Node.js 22+ installation failed: $NODE_BIN is missing" >&2
  exit 1
fi
node_version="$($NODE_BIN --version)"
if [[ ! "$node_version" =~ ^v([0-9]+)\. ]]; then
  echo "Could not parse $NODE_BIN --version: $node_version" >&2
  exit 1
fi
node_major="${BASH_REMATCH[1]}"
echo "Using system Node.js $node_version"
if (( node_major < 22 )); then
  echo "Node.js 22+ is required; found $node_version" >&2
  exit 1
fi

NPM_BIN="/usr/bin/npm"
if [[ ! -x "$NPM_BIN" ]]; then
  echo "Node.js 22+ installation did not provide $NPM_BIN" >&2
  exit 1
fi

# Run the two services as separate unprivileged accounts.  The API account
# can read training data; the provider account can read only encrypted provider
# data and its own secret file.
if ! id -u videotrainer-api >/dev/null 2>&1; then
  useradd --system --home-dir /srv/video-trainer --no-create-home \
    --shell /usr/sbin/nologin videotrainer-api
fi
if ! id -u videotrainer-provider >/dev/null 2>&1; then
  useradd --system --home-dir /srv/video-trainer --no-create-home \
    --shell /usr/sbin/nologin videotrainer-provider
fi
install -d -m 0700 /etc/taste-trainer

# 2) python 环境
python3 -m venv "$SERVICE_DIR/.venv"
"$SERVICE_DIR/.venv/bin/pip" install -r "$SERVICE_DIR/requirements.txt"

# 3) 数据目录骨架
mkdir -p "$WORKSPACE_DIR"/{videos/inbox,runs,analyses,exercises/_template,.a2h/runs}
install -d -m 0700 /srv/video-trainer/provider-data

# 4) React dashboard + AI SDK provider service
"$NPM_BIN" ci --prefix "$SERVICE_DIR/provider-service"
"$NPM_BIN" run build --prefix "$SERVICE_DIR/provider-service"
"$NPM_BIN" ci --prefix "$SERVICE_DIR/web"
"$NPM_BIN" run build --prefix "$SERVICE_DIR/web"

# Code and virtualenv are read-only to both service accounts.  Runtime data
# gets the narrow write permissions each service actually needs.
chown -R root:root "$SERVICE_DIR"
chown -R videotrainer-api:videotrainer-api "$WORKSPACE_DIR"
chown -R videotrainer-provider:videotrainer-provider /srv/video-trainer/provider-data

echo ""
echo "完成。下一步："
echo "  1. cp $SERVICE_DIR/.env.example $SERVICE_DIR/.env 并填写"
echo "     WORKSPACE_DIR=$WORKSPACE_DIR"
echo "  2. 参考 deploy/api.env.example 和 provider.env.example 创建 /etc/taste-trainer/*.env（权限 0600）"
echo "  3. 运行 python -m app.auth create-wj，记录一次性密码"
echo "  4. 先 dry-run 再迁移旧 workspace；不要启用 a2h-view"
echo "  5. 安装 systemd 单元（deploy/*.service）并配置 nginx 只代理 FastAPI"
