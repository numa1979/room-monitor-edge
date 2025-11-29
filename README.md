# セットアップ手順

1. `https://developer.nvidia.com/embedded/learn/get-started-jetson-nano-devkit` を参考に OS イメージを取得し、`https://www.balena.io/etcher` で microSD に書き込む  
2. microSD を Jetson に挿し、有線 LAN で起動して初期セットアップ（ホストユーザーを作成）  
3. リポジトリをクローンしてセットアップを実行  
   ```bash
   git clone https://github.com/numa1979/room-monitor-edge.git
   cd room-monitor-edge
   ./jetson_setup_scripts/setup_dev.sh
   ```

## 事前準備
- Wi-Fi を非対話で繋ぐ場合はリポジトリ直下に `wifi_config` を置く:
  ```
  SSID=your-ssid
  PASS=your-password
  #IFACE=wlan0
  ```
- SSH クライアント設定は Jetson のホストユーザー名を使う。コンテナも同じユーザー名で作成される。

## 開発環境 (Ubuntu 22.04 コンテナ)
Jetson 上で以下を実行:
```bash
./jetson_setup_scripts/setup_dev.sh
```
- Wi-Fi ドライバ導入・接続、日本語入力、22.04 コンテナ起動、パスワード認証の Remote-SSH (2222/tcp) を有効化。
- リポジトリはコンテナ内で `/workspace` にマウントされる。

接続例 (VS Code Remote-SSH も同様):
```bash
ssh -p 2222 <Jetsonのホストユーザー>@<JetsonのIP>
```
接続後は `/workspace` を開く。

### USBカメラのパススルー確認
- `setup_dev.sh` は `/dev/video*` をすべて 22.04 コンテナへ `--device /dev/videoN:/dev/videoN` で渡す。  
- Jetson ホストで `ls /dev/video*` を実行してカメラが検出されているか確認。見つからない場合はケーブルや電源を確認。  
- 既存コンテナをこの変更前に作成していた場合は `docker rm -f jetson-watchdog-ubuntu2204` を実行し、`./jetson_setup_scripts/setup_dev.sh` を再実行して再作成する。  
- コンテナ内で `ls /dev/video*` を実行してパスが見えること、`v4l2-ctl --list-formats-ext` などで MJPEG が有効なことを確認。  
- それでも映らない場合は `docker logs jetson-watchdog-ubuntu2204` で FastAPI のログを確認。`CameraStreamer` が `/dev/video0` を開けていない場合はホスト側で使用中のアプリがないかを見直す。

### SSH クライアント例
（Windows/WLS/mac いずれもほぼ同じ設定です）
```
Host jetson-nano
    HostName 192.168.10.118
    Port 2222
    User <Jetsonで作ったホストユーザー>
    PubkeyAuthentication no
    PreferredAuthentications password
    StrictHostKeyChecking no
```
- Windows でホスト鍵を保存しない場合は `UserKnownHostsFile NUL` を追加。  
- 接続すると 22.04 コンテナ内で `/workspace` がリポジトリルート。

## 本番環境 (18.04 ホスト上)
Jetson 上で以下を実行:
```bash
./jetson_setup_scripts/setup_prod.sh
```
- 依存パッケージ導入と FastAPI アプリの起動のみ。`http://<JetsonのIP>:8080` でアクセス。

## ローカル実行 (任意)
```bash
./run.sh        # docker compose up --build
```
`Ctrl+C` で停止。バックグラウンドは `./run.sh -d`。
