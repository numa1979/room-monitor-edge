#!/usr/bin/env bash
set -euo pipefail

# 1. パッケージ
sudo apt update
sudo apt install -y \
  build-essential dkms git \
  ibus-mozc mozc-utils-gui \
  ca-certificates libnss3 libnss3-tools chromium-codecs-ffmpeg-extra

# rm -rf ~/.cache/chromium ~/.config/chromium

# 2. Wi-Fi ドライバ (rtl88x2bu)
cd "$HOME"
if [ ! -d rtl88x2bu ]; then
  git clone https://github.com/cilynx/rtl88x2bu rtl88x2bu
fi

cd rtl88x2bu
make clean || true
make ARCH=arm64
sudo make install
sudo modprobe 88x2bu

# 3. 日本語入力 (Mozc)
im-config -n ibus || true

gsettings set org.freedesktop.ibus.general preload-engines "['mozc-jp']" || true
gsettings set org.gnome.desktop.input-sources sources "[('ibus', 'mozc-jp')]"

ibus restart || true

# 4. Wi-Fi 接続
SSID=${1:-}
PASS=${2:-}

if [ -z "$SSID" ]; then
  read -rp "SSID: " SSID
fi

if [ -z "$PASS" ]; then
  read -rsp "Password: " PASS
  echo
fi

sudo nmcli device wifi connect "$SSID" password "$PASS" ifname wlan0
