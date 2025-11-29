# Jetson Watchdog (Prototype)

Jetson Nano 上で動かす見守り AI システムの最初のひな形です。まだ推論やカメラ連携はありませんが、Jetson の IP (例: `http://192.168.10.118:8080`) にスマホや PC からアクセスすると placeholder UI が表示されます。ここをベースに機能を肉付けしていきます。

## クイックスタート

初期化した Jetson での一連の流れ (JetPack 4.6.1 SD イメージ前提):

1. microSD を Jetson に挿し、有線 LAN で起動して Ubuntu 18.04 初期セットアップを完了する  
2. `room-monitor-edge` をクローンする  
3. **開発時:** `jetson_setup_scripts/setup_dev.sh` を実行  
   - Wi-Fi ドライバ + 接続、日本語入力、Ubuntu 22.04 コンテナ起動、VS Code Remote-SSH (2222/tcp) を構築  
4. **配布時:** `jetson_setup_scripts/setup_prod.sh` を実行  
   - 依存パッケージと Docker ベースの FastAPI アプリだけを起動（LAN 接続のままで OK）  

`setup_dev.sh` 実行後は `ssh -p 2222 dev@<JetsonのIP>` で 22.04 コンテナへ入って開発できます。`setup_prod.sh` は再起動後も自動で FastAPI が立ち上がる状態を想定しています。

FastAPI アプリをローカルで起動する最小コマンド:

```bash
# 1. 必要なら Docker Compose V2 をインストール
# 2. このリポジトリでアプリを起動
./run.sh
```

初回はイメージのビルドが走り、その後 `http://<JetsonのIP>:8080` でアクセスできます。停止は `Ctrl + C` です。バックグラウンドで動かしたい場合は `./run.sh -d` を利用してください。

## Wi-Fi ドライバについて

Realtek rtl88x2bu のスナップショット（上流コミット: `42ec4de8d36c9eac0ac26ae714837efbf1a09c1d`）を `jetson_setup_scripts/vendor/rtl88x2bu.tar.gz` として同梱しています。`setup_wifi.sh` は以下の優先順位でソースを取得します:
1. `DRIVER_DIR` に既にあるソースをそのまま利用
2. リポジトリ同梱の `rtl88x2bu.tar.gz` を展開して利用
3. 見つからない場合だけ GitHub の `DRIVER_REPO` を `DRIVER_COMMIT` でクローン

別バージョンを使いたい場合は `DRIVER_DIR` で手元のパスを指すか、`DRIVER_REPO` / `DRIVER_COMMIT` を上書きしてください。

## プロジェクト構成

```
app/
  main.py         # FastAPI アプリ本体
  templates/      # Jinja2 テンプレート (現状はトップページのみ)
Dockerfile        # python:3.10-slim ベース
run.sh            # `docker compose up --build`
docker-compose.yml
jetson_watchdog_design.md
jetson_setup_scripts/
  setup_dev.sh / setup_prod.sh
  modules/
    install_host_packages.sh
    setup_wifi.sh
    setup_japanese_input.sh
    setup_docker_env.sh
    setup_remote_ssh.sh
```

## 今後の追加予定

- Web カメラ入力と MJPEG/WS ストリーム
- YOLO + Pose 推論モジュール群
- 監視対象選択 UI / イベント通知
- ログ保存と状態監視 API

リモート開発や本番への展開に合わせてブランチや Issue を切りながら段階的に追加していきます。
