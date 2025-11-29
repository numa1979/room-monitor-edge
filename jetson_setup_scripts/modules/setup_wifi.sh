#!/usr/bin/env bash
# Install rtl88x2bu driver if needed and connect to Wi-Fi.
set -euo pipefail

DRIVER_REPO=${DRIVER_REPO:-https://github.com/cilynx/rtl88x2bu}
DRIVER_DIR=${DRIVER_DIR:-$HOME/rtl88x2bu}
IFACE=${IFACE:-wlan0}
SSID=${SSID:-}
PASS=${PASS:-}

need_driver_install() {
  if modinfo 88x2bu >/dev/null 2>&1; then
    return 1
  fi
  return 0
}

install_driver() {
  echo "[wifi] rtl88x2bu ドライバを準備します"
  mkdir -p "$HOME"
  if [ ! -d "$DRIVER_DIR" ]; then
    git clone "$DRIVER_REPO" "$DRIVER_DIR"
  fi
  cd "$DRIVER_DIR"
  make clean || true
  make ARCH=arm64
  sudo make install
  sudo modprobe 88x2bu
}

if need_driver_install; then
  install_driver
else
  echo "[wifi] 既に rtl88x2bu ドライバが導入済みのためスキップ"
  sudo modprobe 88x2bu || true
fi

if [ -z "$SSID" ]; then
  read -rp "SSID: " SSID
fi
if [ -z "$PASS" ]; then
  read -rsp "Password: " PASS
  echo
fi

echo "[wifi] Wi-Fi SSID='${SSID}' へ接続します"
sudo nmcli device wifi connect "$SSID" password "$PASS" ifname "$IFACE"
