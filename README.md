# Jetson Watchdog (Prototype)

Jetson Nano 上で動かす見守り AI システムの最初のひな形です。まだ推論やカメラ連携はありませんが、Jetson の IP (例: `http://192.168.10.118:8080`) にスマホや PC からアクセスすると placeholder UI が表示されます。ここをベースに機能を肉付けしていきます。

## クイックスタート

初期化した Jetson での一連の流れ:

1. Jetson ホスト (JetPack Ubuntu18) で `jetson_setup.sh` を実行し、日本語 / Wi-Fi 等を整える  
2. `jetson_docker_env.sh` を実行して Ubuntu 22.04 コンテナ + FastAPI を起動 (`8080` 露出、`2222` は後述の SSH 用)  
3. リモート開発したい場合は `jetson_remote-ssh-setup.sh` でコンテナ内に openssh-server + ユーザーをセット  
4. PC から `ssh -p 2222 dev@<Jetson IP>` あるいは VS Code Remote-SSH で接続し、`/workspace` 内のソースを編集  

FastAPI アプリをローカルで起動する最小コマンド:

```bash
# 1. 必要なら Docker Compose V2 をインストール
# 2. このリポジトリでアプリを起動
./run.sh
```

初回はイメージのビルドが走り、その後 `http://<JetsonのIP>:8080` でアクセスできます。停止は `Ctrl + C` です。バックグラウンドで動かしたい場合は `./run.sh -d` を利用してください。

## プロジェクト構成

```
app/
  main.py         # FastAPI アプリ本体
  templates/      # Jinja2 テンプレート (現状はトップページのみ)
Dockerfile        # python:3.10-slim ベース
run.sh            # `docker compose up --build`
docker-compose.yml
jetson_watchdog_design.md
jetson_setup.sh
```

## 今後の追加予定

- Web カメラ入力と MJPEG/WS ストリーム
- YOLO + Pose 推論モジュール群
- 監視対象選択 UI / イベント通知
- ログ保存と状態監視 API

リモート開発や本番への展開に合わせてブランチや Issue を切りながら段階的に追加していきます。
