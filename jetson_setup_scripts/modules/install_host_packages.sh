#!/usr/bin/env bash
set -euo pipefail

sudo apt update
sudo apt install -y \
  build-essential dkms git curl wget \
  ca-certificates libnss3 libnss3-tools \
  python3 python3-venv python3-pip \
  libgl1 \
  chromium-codecs-ffmpeg-extra
