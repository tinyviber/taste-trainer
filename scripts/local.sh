#!/usr/bin/env bash
# One-command local setup, verification, and development runner.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
ENV_FILE="$ROOT_DIR/.env"
WORKSPACE_DIR="${WORKSPACE_DIR:-$ROOT_DIR/workspace}"
TRAINING_ASSETS_DIR="${TRAINING_ASSETS_DIR:-$ROOT_DIR/training-assets}"
export WORKSPACE_DIR
export TRAINING_ASSETS_DIR
cd "$ROOT_DIR"
MODE="${1:-start}"

case "$MODE" in
  start|test|build) ;;
  *)
    echo "用法：$0 [start|test|build]" >&2
    exit 2
    ;;
esac

if [[ "$MODE" == "start" && ! -f "$ENV_FILE" ]]; then
  cp "$ROOT_DIR/.env.example" "$ENV_FILE"
  echo "已创建 $ENV_FILE；请填写 LLM_API_KEY 后重新运行。" >&2
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi
# Keep the low-friction project-local default when .env leaves it unset.
export WORKSPACE_DIR="${WORKSPACE_DIR:-$ROOT_DIR/workspace}"
export TRAINING_ASSETS_DIR="${TRAINING_ASSETS_DIR:-$ROOT_DIR/training-assets}"
export PROVIDER_DATA_DIR="${PROVIDER_DATA_DIR:-$WORKSPACE_DIR/.provider-data}"

if [[ "$MODE" == "start" ]]; then
  if [[ -z "${LLM_API_KEY:-}" || "$LLM_API_KEY" == "sk-xxx" ]]; then
    echo "请先在 $ENV_FILE 填写 LLM_API_KEY。" >&2
    exit 1
  fi
  if [[ -z "${PROVIDER_INTERNAL_SECRET:-}" || "$PROVIDER_INTERNAL_SECRET" == "replace-with-a-random-32-byte-secret" ]]; then
    echo "请先在 $ENV_FILE 填写 PROVIDER_INTERNAL_SECRET。" >&2
    exit 1
  fi
  if [[ -z "${PROVIDER_ENCRYPTION_KEY:-}" || "$PROVIDER_ENCRYPTION_KEY" == "replace-with-base64-32-byte-key" ]]; then
    echo "请先在 $ENV_FILE 填写 PROVIDER_ENCRYPTION_KEY（openssl rand -base64 32）。" >&2
    exit 1
  fi

  mkdir -p "$WORKSPACE_DIR/users"

  for required in \
    "$TRAINING_ASSETS_DIR/RUBRIC.md" \
    "$TRAINING_ASSETS_DIR/PROMPT_POOL.md" \
    "$TRAINING_ASSETS_DIR/submission-template.md"; do
    if [[ ! -f "$required" ]]; then
      echo "training-assets 缺少必需文件：$required" >&2
      echo "请补齐应用训练资产后重试。" >&2
      exit 1
    fi
  done
fi

venv_ok=0
if [[ -x "$VENV_DIR/bin/python" ]]; then
  "$VENV_DIR/bin/python" -c 'import sys' >/dev/null 2>&1 \
    && "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1 \
    && venv_ok=1
fi
if (( venv_ok == 0 )); then
  echo "创建或修复项目虚拟环境：$VENV_DIR"
  python3 -m venv --clear "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install -q -r "$ROOT_DIR/requirements.txt"

if [[ ! -d "$ROOT_DIR/provider-service/node_modules" ]]; then
  npm ci --prefix "$ROOT_DIR/provider-service"
fi
if [[ ! -d "$ROOT_DIR/web/node_modules" ]]; then
  npm ci --prefix "$ROOT_DIR/web"
fi

run_checks() {
  echo "== Python tests =="
  (cd "$ROOT_DIR" && "$VENV_DIR/bin/python" -m unittest discover -s tests -v)
  "$VENV_DIR/bin/python" -m compileall -q "$ROOT_DIR/app"
  bash -n "$ROOT_DIR/deploy/setup.sh"
  echo "== Web build =="
  npm run build --prefix "$ROOT_DIR/web"
  echo "== Provider build =="
  npm run build --prefix "$ROOT_DIR/provider-service"
  echo "== Provider tests =="
  npm test --prefix "$ROOT_DIR/provider-service"
  echo "== Checks passed =="
}

if [[ "$MODE" == "test" ]]; then
  run_checks
  exit 0
fi

if [[ "$MODE" == "build" ]]; then
  npm run build --prefix "$ROOT_DIR/web"
  npm run build --prefix "$ROOT_DIR/provider-service"
  exit 0
fi

run_checks

LOG_DIR="$(mktemp -d /tmp/taste-trainer-local.XXXXXX)"
PIDS=()
NAMES=()

cleanup() {
  trap - EXIT
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  for pid in "${PIDS[@]:-}"; do
    wait "$pid" 2>/dev/null || true
  done
  echo "服务已停止；日志保留在 $LOG_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

start_process() {
  local name="$1"
  shift
  echo "启动 $name"
  "$@" >"$LOG_DIR/$name.log" 2>&1 &
  PIDS+=("$!")
  NAMES+=("$name")
}

start_process api "$VENV_DIR/bin/python" -m uvicorn app.api:app \
  --host 127.0.0.1 --port "${API_PORT:-8421}"
start_process provider npm run dev --prefix "$ROOT_DIR/provider-service"

start_process web npm run dev --prefix "$ROOT_DIR/web" -- --host 127.0.0.1

echo ""
echo "Taste Trainer 已启动："
echo "  Web:       http://127.0.0.1:5173"
echo "  API:       http://127.0.0.1:${API_PORT:-8421}"
echo "  Provider:  http://127.0.0.1:${PROVIDER_PORT:-8765}"
echo "  Workspace: $WORKSPACE_DIR"
echo ""
echo "按 Ctrl-C 停止全部服务。"

while true; do
  for index in "${!PIDS[@]}"; do
    if ! kill -0 "${PIDS[$index]}" 2>/dev/null; then
      echo "${NAMES[$index]} 已退出，查看 $LOG_DIR/${NAMES[$index]}.log" >&2
      exit 1
    fi
  done
  sleep 2
done
