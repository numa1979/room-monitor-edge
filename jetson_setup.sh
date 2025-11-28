#!/usr/bin/env bash
set -euo pipefail

echo "== 1. Package Install =="
sudo apt update
sudo apt install -y \
  build-essential dkms git \
  ibus-mozc mozc-utils-gui \
  ca-certificates libnss3 libnss3-tools chromium-codecs-ffmpeg-extra

# rm -rf ~/.cache/chromium ~/.config/chromium

echo "== 2. Wi-Fi driver (rtl88x2bu) =="
cd "$HOME"
if [ ! -d rtl88x2bu ]; then
  echo "git clone ..."
  git clone https://github.com/cilynx/rtl88x2bu rtl88x2bu
fi

cd rtl88x2bu

# すでにビルド済みなら make をスキップ
if [ ! -f 88x2bu.ko ]; then
  echo "Build driver (first time only)"
  make clean || true
  make ARCH=arm64
else
  echo "Already built → skip build"
fi

sudo make install
sudo modprobe 88x2bu

echo "== 3. 日本語入力 (Mozc) =="
im-config -n ibus || true

gsettings set org.freedesktop.ibus.general preload-engines "['mozc-jp']" || true
gsettings set org.gnome.desktop.input-sources sources "[('ibus', 'mozc-jp')]" || true

ibus restart || true

echo "== 4. Wi-Fi Connect =="

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

echo "=========================="
echo " Setup 完了"
echo " 日本語入力 OK"
echo " Wi-Fi 接続 OK"
echo "=========================="
