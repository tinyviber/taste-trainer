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

# Run both services as a dedicated unprivileged account.
if ! id -u videotrainer >/dev/null 2>&1; then
  useradd --system --home-dir /srv/video-trainer --create-home \
    --shell /usr/sbin/nologin videotrainer
fi

# 2) a2h 查看器
"$NPM_BIN" install -g @tinyviber/a2h
command -v a2h >/dev/null 2>&1 || {
  echo "a2h installation failed: command not found" >&2
  exit 1
}

# 3) python 环境
python3 -m venv "$SERVICE_DIR/.venv"
"$SERVICE_DIR/.venv/bin/pip" install -r "$SERVICE_DIR/requirements.txt"

# 4) 数据目录骨架
mkdir -p "$WORKSPACE_DIR"/{videos/inbox,runs,analyses,exercises/_template,.a2h/runs}

# 5) React dashboard + AI SDK provider service
"$NPM_BIN" ci --prefix "$SERVICE_DIR/provider-service"
"$NPM_BIN" run build --prefix "$SERVICE_DIR/provider-service"
"$NPM_BIN" ci --prefix "$SERVICE_DIR/web"
"$NPM_BIN" run build --prefix "$SERVICE_DIR/web"

chown -R videotrainer:videotrainer "$SERVICE_DIR" "$WORKSPACE_DIR"

echo ""
echo "完成。下一步："
echo "  1. cp $SERVICE_DIR/.env.example $SERVICE_DIR/.env 并填写"
echo "     WORKSPACE_DIR=$WORKSPACE_DIR"
echo "  2. 把本地数据 rsync 到 $WORKSPACE_DIR（见 scripts/rsync-data.sh.example）"
echo "  3. 安装 systemd 单元（deploy/*.service，改路径和用户）"
