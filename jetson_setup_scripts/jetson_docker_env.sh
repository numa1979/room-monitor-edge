#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT="$SCRIPT_DIR"

CONTAINER_NAME=${CONTAINER_NAME:-jetson-watchdog-ubuntu2204}
IMAGE_NAME=${IMAGE_NAME:-ubuntu:22.04}
HOST_PORT=${HOST_PORT:-8080}
CONTAINER_PORT=${CONTAINER_PORT:-8080}
SSH_PORT=${SSH_PORT:-2222}
VENV_PATH=${VENV_PATH:-/opt/jetson_watchdog_venv}
APP_ENTRY="uvicorn app.main:app --host 0.0.0.0 --port ${CONTAINER_PORT}"

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; }

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    err "コマンド '$1' が見つかりません。先にインストールしてください。"
    exit 1
  fi
}

require_cmd docker

container_exists() {
  docker ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"
}

start_container() {
  if ! container_exists; then
    info "Ubuntu 22.04 コンテナ ${CONTAINER_NAME} を作成します"
    docker pull "$IMAGE_NAME"
    docker create \
      --name "$CONTAINER_NAME" \
      --hostname "$CONTAINER_NAME" \
      --restart unless-stopped \
      -p "${HOST_PORT}:${CONTAINER_PORT}" \
      -p "${SSH_PORT}:22" \
      -v "$REPO_ROOT:/workspace" \
      -w /workspace \
      "$IMAGE_NAME" \
      bash -c "tail -f /dev/null" >/dev/null
  fi

  if ! docker ps --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
    info "コンテナ ${CONTAINER_NAME} を起動します"
    docker start "$CONTAINER_NAME" >/dev/null
  fi
}

exec_root() {
  docker exec -u root "$CONTAINER_NAME" bash -lc "$1"
}

prepare_runtime() {
  info "コンテナ内で Python 実行環境を準備します"
  exec_root "apt-get update"
  exec_root "DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip git curl"
  exec_root "mkdir -p /workspace && test -d ${VENV_PATH} || python3 -m venv ${VENV_PATH}"
  exec_root "source ${VENV_PATH}/bin/activate && pip install --upgrade pip"
  exec_root "cd /workspace && source ${VENV_PATH}/bin/activate && pip install -r requirements.txt"
}

start_app() {
  info "既存のアプリプロセスを停止します"
  docker exec "$CONTAINER_NAME" bash -lc "pkill -f 'uvicorn app.main:app'" >/dev/null 2>&1 || true

  info "アプリを起動します (${APP_ENTRY})"
  docker exec -d "$CONTAINER_NAME" bash -lc "cd /workspace && source $VENV_PATH/bin/activate && $APP_ENTRY"
}

start_container
prepare_runtime
start_app

info "セットアップ完了: http://<JetsonのIP>:${HOST_PORT} へアクセスしてください (SSHポート: ${SSH_PORT})"
