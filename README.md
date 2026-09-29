# 発見型クロール実験場

網羅性を優先して、探索方法・取りこぼし・取得条件を検証するための実験基盤。従来の検索エンジンと既存データはローカル側へ保管し、この配布リポジトリには新しい `jse.lab` と運転・検証に必要なファイルだけを載せる。

CodexクラウドのGPT-6 Lunaが実験案・リンク選択・結果整理を担当し、Pythonが取得上限、保存、再開、引用照合を担当する。通常のクロールには追加のAPIキー、pipパッケージ、ブラウザは不要。Dockerだけを起動してもモデルは呼ばれず、Codex側が要求JSONへ回答する。

## すぐに動作確認

Python 3.12以上:

```bash
bash scripts/cloud_setup.sh
python3 -B -m jse.lab demo --run .lab-output/demo
```

demoは通信なしの架空サイト・決定的な模擬回答・模擬承認を使う。Lunaの品質測定ではない。既存のrunを上書きしないため、再実行時は別のrun名を使う。

Docker:

```bash
docker compose build
docker compose run --rm lab demo --run /data/demo
```

ネットワークを無効にして検証する場合:

```bash
docker build -t discovery-lab:local .
docker run --rm --network none --read-only --tmpfs /tmp:size=32m --mount type=volume,src=discovery-lab-data,dst=/data discovery-lab:local demo --run /data/demo
docker run --rm --network none --mount type=volume,src=discovery-lab-data,dst=/data discovery-lab:local verify --run /data/demo
```

最終イメージには新基盤と設定例だけを入れる。テストはビルド用stageに置く。既存コーパス、`.venv`、資格情報、実験結果はビルドコンテキストの許可リストから除外する。

## 必要時に使う取得ツール

Crawlee Python 1.10.3、Impit 0.14.1、SessionPoolと、Patchright 1.63.0＋Chromiumを任意の環境として用意している。通常イメージには追加しない。

```bash
docker compose --profile tools run --build --rm crawl-tools
docker compose --profile browser run --build --rm browser-tools
```

両コマンドは導入確認。Crawleeでローカルfixtureを1件取得し、browser環境では外部通信を無効にしてJavaScript実行も確認する。実サイトのブロック突破性能を測るものではない。ツールを使うコードを実行する場合は `--entrypoint python` で入口を指定できる。

Codex Cloudへまとめて導入するSetupは `bash scripts/cloud_setup.sh browser`、HTTPツールだけなら末尾を `http` にする。通常の `jse.lab` は従来の取得経路を使う。今回の追加はSDK・ブラウザの配備までで、自動切り替え、個別サイト対策、既存の予算管理への接続は必要になった段階で行う。[導入手順](docs/cloud-lab.md#取得ツールの配備)

## Codexクラウドで使う

接続するGitHubリポジトリへコードを置き、Pythonを3.12以上、SetupとMaintenanceを `bash scripts/cloud_setup.sh` にする。クロール対象ドメインだけAgent internet accessで許可する。調査モデルは実行画面でGPT-6 Lunaを指定する。[設定と運転手順](docs/cloud-lab.md)に、最初の依頼文・承認・再開・保存方法をまとめた。

このローカルフォルダ全体をアップロードせず、小さい配布物を作る場合:

```bash
python3 -B scripts/package_cloud.py --output .lab-output/discovery-lab-cloud.zip
```

ZIPはコードの配布用。実験途中の保存には `jse.lab export` を使う。

## GPUが必要な場合

[Kaggle操作スキル](.agents/skills/kaggle-ops/SKILL.md)と手動起動の `kaggle-job.yml` を使う。Repository Secretの `KAGGLE_API_TOKEN` で認証し、GPU残量の確認、Notebook送信、versionを固定したログ待機、結果回収まで実行する。通常のコンテナにKaggleやGPUの依存は含めない。

結果artifactの `continuation.json` が検証工程への受け渡しになる。[クラウドのKaggle運転手順](docs/cloud-lab.md#kaggleへの計算委譲)を参照。終了済みCodexタスクの自動起動は別途監視機構への接続が必要。

## 動く範囲

- 同じseed・上限によるBFSとLunaのリンク選択の比較。
- HTTPSのHTML、robots、同一の許可origin内のリダイレクト、取得間隔。
- 成功だけでなく失敗・robots・リダイレクトも含むリクエスト台帳。
- 元のHTML、本文、タイトル、リンク、取得元、時刻、ハッシュの保存。
- 計画・結果の判断待ち、プロセスをまたぐ再開、二重実行の排除。
- 保存物の再照合と引用位置の検証。別途goldがある場合の注釈URL到達率。

JavaScript描画、PDF、sitemap、Common Crawl、新しい検索索引への投入は未接続。既存の古い整形済みコーパスの欠落を、日本サイト全体やCommon Crawl全体の欠落とは扱わない。

クラウド環境への実配置・クラウド側のモデル選択・ネットワーク疎通は別の接続確認が必要。モデルの実ID、トークン、Codex利用料金はこの実行器では機械検証できず、レポートに未測定として残す。
