#!/usr/bin/env bash
set -euo pipefail

echo "== Wi-Fi Connect =="

SSID=${1:-}
PASS=${2:-}

if [ -z "$SSID" ]; then
  read -rp "SSID: " SSID
fi

if [ -z "$PASS" ]; then
  read -rsp "Password: " PASS
  echo
fi

# Wi-Fi ドライバがロードされているかチェック
if ! lsmod | grep -q 88x2bu; then
  echo "Warning: Wi-Fi driver 88x2bu 未ロード"
  echo "sudo modprobe 88x2bu でロード可"
fi

# 接続
sudo nmcli device wifi connect "$SSID" password "$PASS" ifname wlan0

echo "== 完了 =="
nmcli device status
