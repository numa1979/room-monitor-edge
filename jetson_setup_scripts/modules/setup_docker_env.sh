#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd)

CONTAINER_NAME=${CONTAINER_NAME:-jetson-watchdog-ubuntu2204}
IMAGE_NAME=${IMAGE_NAME:-ubuntu:22.04}
HOST_PORT=${HOST_PORT:-8080}
CONTAINER_PORT=${CONTAINER_PORT:-8080}
SSH_PORT=${SSH_PORT:-2222}
VENV_PATH=${VENV_PATH:-/opt/jetson_watchdog_venv}
APP_ENTRY="uvicorn app.main:app --host 0.0.0.0 --port ${CONTAINER_PORT}"

info() { printf '\033[1;34m[docker]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[docker ERROR]\033[0m %s\n' "$*" >&2; }

command -v docker >/dev/null 2>&1 || { err "docker コマンドがありません。"; exit 1; }
DOCKER=(docker)
if ! "${DOCKER[@]}" ps >/dev/null 2>&1; then
  if sudo docker ps >/dev/null 2>&1; then
    info "docker へのアクセスに sudo を使用します"
    DOCKER=(sudo docker)
  else
    err "docker デーモンにアクセスできません。ユーザーを docker グループに追加するか sudo 権限を確認してください"
    exit 1
  fi
fi

container_exists() {
  "${DOCKER[@]}" ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"
}

start_container() {
  local device_args=()
  for dev in /dev/video*; do
    [[ -e "$dev" ]] || continue
    device_args+=("--device" "${dev}:${dev}")
  done

  if ((${#device_args[@]} == 0)); then
    info "/dev/video* が見つかりません。カメラをコンテナへ渡せません。"
  fi

  if ! container_exists; then
    info "Ubuntu 22.04 コンテナ ${CONTAINER_NAME} を生成"
    "${DOCKER[@]}" pull "$IMAGE_NAME"
    "${DOCKER[@]}" create \
      --name "$CONTAINER_NAME" \
      --hostname "$CONTAINER_NAME" \
      --restart unless-stopped \
      -p "${HOST_PORT}:${CONTAINER_PORT}" \
      -p "${SSH_PORT}:22" \
      -v "$REPO_ROOT:/workspace" \
      "${device_args[@]}" \
      -w /workspace \
      "$IMAGE_NAME" \
      bash -c "tail -f /dev/null" >/dev/null
  fi

  if ! "${DOCKER[@]}" ps --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
    info "コンテナ ${CONTAINER_NAME} を起動"
    "${DOCKER[@]}" start "$CONTAINER_NAME" >/dev/null
  fi
}

exec_root() {
  "${DOCKER[@]}" exec -u root "$CONTAINER_NAME" bash -lc "$1"
}

prepare_runtime() {
  info "Python 実行環境を準備"
  exec_root "apt-get update"
  exec_root "DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip git curl"
  exec_root "mkdir -p /workspace && test -d ${VENV_PATH} || python3 -m venv ${VENV_PATH}"
  exec_root "source ${VENV_PATH}/bin/activate && pip install --upgrade pip"
  exec_root "cd /workspace && source ${VENV_PATH}/bin/activate && pip install -r requirements.txt"
}

start_app() {
  info "既存アプリを停止"
  "${DOCKER[@]}" exec "$CONTAINER_NAME" bash -lc "pkill -f 'uvicorn app.main:app'" >/dev/null 2>&1 || true
  info "FastAPI を起動"
  "${DOCKER[@]}" exec -d "$CONTAINER_NAME" bash -lc "cd /workspace && source ${VENV_PATH}/bin/activate && ${APP_ENTRY}"
}

start_container
prepare_runtime
start_app

info "完了: http://<Jetson IP>:${HOST_PORT} でアクセス可 (SSH:${SSH_PORT})"
