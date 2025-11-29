#!/usr/bin/env bash
# Configure VS Code Remote-SSH access into the Ubuntu 22.04 container.
set -euo pipefail

CONTAINER_NAME=${CONTAINER_NAME:-jetson-watchdog-ubuntu2204}
SSH_PORT=${SSH_PORT:-2222}
CONTAINER_USER=${CONTAINER_USER:-dev}
CONTAINER_PASS=${CONTAINER_PASS:-}
HOST_USER=${HOST_USER:-${SUDO_USER:-$USER}}
HOST_UID=$(id -u "$HOST_USER")
HOST_HOME=$(eval echo "~${HOST_USER}")

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || { err "'$1' が見つかりません"; exit 1; }; }

require_cmd docker

if ! docker ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
  err "コンテナ ${CONTAINER_NAME} が存在しません。先に jetson_docker_env.sh を実行してください。"
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
  info "コンテナ ${CONTAINER_NAME} を起動します"
  docker start "$CONTAINER_NAME" >/dev/null
fi

info "SSH 用パッケージをインストールします"
docker exec "$CONTAINER_NAME" bash -lc "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server sudo locales"

info "sshd 設定を調整します"
docker exec "$CONTAINER_NAME" bash -lc "mkdir -p /var/run/sshd && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config"
docker exec "$CONTAINER_NAME" bash -lc "sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config"

info "ユーザ ${CONTAINER_USER} を準備します (uid=${HOST_UID})"
if ! docker exec "$CONTAINER_NAME" id -u "$CONTAINER_USER" >/dev/null 2>&1; then
  docker exec "$CONTAINER_NAME" useradd -m -u "$HOST_UID" -s /bin/bash -G sudo "$CONTAINER_USER"
fi

if [ -z "$CONTAINER_PASS" ]; then
  read -rsp "${CONTAINER_USER} のパスワードを入力してください: " CONTAINER_PASS
  echo
fi
echo "${CONTAINER_USER}:${CONTAINER_PASS}" | docker exec -i "$CONTAINER_NAME" chpasswd

docker exec "$CONTAINER_NAME" bash -lc "mkdir -p /home/${CONTAINER_USER}/.ssh && chown -R ${CONTAINER_USER}:${CONTAINER_USER} /home/${CONTAINER_USER}/.ssh"
if [ -f "${HOST_HOME}/.ssh/id_ed25519.pub" ]; then
  info "ホストの公開鍵を authorized_keys へ追加します"
  docker exec "$CONTAINER_NAME" bash -lc "touch /home/${CONTAINER_USER}/.ssh/authorized_keys && chmod 600 /home/${CONTAINER_USER}/.ssh/authorized_keys"
  docker exec "$CONTAINER_NAME" bash -lc "cat '${HOST_HOME}/.ssh/id_ed25519.pub' >> /home/${CONTAINER_USER}/.ssh/authorized_keys"
  docker exec "$CONTAINER_NAME" bash -lc "chown ${CONTAINER_USER}:${CONTAINER_USER} /home/${CONTAINER_USER}/.ssh/authorized_keys"
else
  info "${HOST_HOME}/.ssh/id_ed25519.pub が無いため鍵コピーをスキップします"
fi
docker exec "$CONTAINER_NAME" bash -lc "chmod 700 /home/${CONTAINER_USER}/.ssh"

PORT_MAPPING=$(docker port "$CONTAINER_NAME" 22/tcp 2>/dev/null || true)
if [ -z "$PORT_MAPPING" ]; then
  err "コンテナ ${CONTAINER_NAME} は 22/tcp を公開していません。削除して jetson_docker_env.sh を再実行し、-p ${SSH_PORT}:22 を含めてください。"
else
  info "22/tcp は ${PORT_MAPPING} にバインドされています"
fi

docker exec "$CONTAINER_NAME" service ssh restart >/dev/null
info "設定完了: VS Code から Host=<JetsonのIP> / Port=${SSH_PORT} / User=${CONTAINER_USER} で接続できます"
