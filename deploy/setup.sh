#!/usr/bin/env bash
# VPS 初始化：安装依赖 + 建数据目录。以 root 或 sudo 运行。
set -euo pipefail

SERVICE_DIR="${1:-/srv/video-trainer/service}"   # 本仓库部署位置
WORKSPACE_DIR="${2:-/srv/video-trainer/workspace}" # 数据目录

# 1) 系统依赖
apt-get update
apt-get install -y ffmpeg python3-venv nodejs npm

# Run both services as a dedicated unprivileged account.
if ! id -u videotrainer >/dev/null 2>&1; then
  useradd --system --home-dir /srv/video-trainer --create-home \
    --shell /usr/sbin/nologin videotrainer
fi

# 2) a2h 查看器
npm install -g @tinyviber/a2h

# 3) python 环境
python3 -m venv "$SERVICE_DIR/.venv"
"$SERVICE_DIR/.venv/bin/pip" install -r "$SERVICE_DIR/requirements.txt"

# 4) 数据目录骨架
mkdir -p "$WORKSPACE_DIR"/{videos/inbox,runs,analyses,exercises/_template,.a2h/runs}
chown -R videotrainer:videotrainer "$SERVICE_DIR" "$WORKSPACE_DIR"

echo ""
echo "完成。下一步："
echo "  1. cp $SERVICE_DIR/.env.example $SERVICE_DIR/.env 并填写"
echo "     WORKSPACE_DIR=$WORKSPACE_DIR"
echo "  2. 把本地数据 rsync 到 $WORKSPACE_DIR（见 scripts/rsync-data.sh.example）"
echo "  3. 安装 systemd 单元（deploy/*.service，改路径和用户）"
