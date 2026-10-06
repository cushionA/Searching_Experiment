# 発見型クロール実験場

網羅性を優先して、探索方法・取りこぼし・取得条件を検証するための実験基盤。従来の検索エンジンと既存データはローカル側へ保管し、この配布リポジトリには新しい `jse.lab` と運転・検証に必要なファイルだけを載せる。

CodexクラウドのGPT-6 Lunaが実験案・リンク選択・結果整理を担当し、Pythonが取得上限、保存、再開、引用照合を担当する。通常のクロールには追加のAPIキー、pipパッケージ、ブラウザは不要。Dockerだけを起動してもモデルは呼ばれず、Codex側が要求JSONへ回答する。

情報源の所在が未指定なら、`python3 -B -m jse.lab topic --run .lab-output/my-topic --topic '探したい情報'`から始める。テーマから検索の入口を作り、発見した外部サイトを予算内で辿り、回答文ごとの出典と原文を`ANSWER.md`へ保存する。[テーマ起点の探索手順と制約](docs/topic-discovery.md)

無料の検索入口は[調査記録](docs/free-search-engines.md)と[全候補の台帳](experiments/search-candidates-20261002.json)へ保存した。[全保存ZIPと復元方法](experiments/snapshots/README.md)には5回分の取得本文・出典・失敗記録と実装を含める。[自前4getと4play](experiments/fourget-selfhost/README.md)をDockerで起動でき、4playはブラウザ実験のToolAdapterとしてページ移動・応答本文・DOMの保存に対応する。検索用の単発検証は[4play検索手順](docs/bot-diagnostics-fourplay.md)を参照。

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

ブラウザとfetchのプロキシは `python3 -B scripts/with_proxy.py --proxy http://HOST:PORT -- COMMAND ...` で共通指定できる。未指定なら既存設定を継承する。[設定例・対応ツール・GCPの設定手順](docs/proxy.md)。管理Cloudの既存プロキシを別の宛先へ変更する操作は拒否する。

LightPanda・Playwright・Patchrightの速度、メモリ、HTTP通信量をローカルfixtureで比較する場合は、`npm run benchmark:setup`の後に`npm run benchmark:quick`を実行する。Chromiumはヘッドレス・ヘッドフル両方、並列数1/4、ブラウザの再利用/毎回起動で比較する。`npm run benchmark:parallel`はエンジン間も同時実行する。[比較条件・導入・出力の詳細](docs/browser-benchmark.md)

OxiBrowser 0.25.0を使った[Google・Amazon・ジョーシン・Homes・Indeedの比較結果と使用セレクタ](docs/oxibrowser-experiment.md)を記録した。実サイトのHTTP取得と、保存HTML上のAI操作を分けて検証している。

wreq-js・impit・Patchright・rebrowser-patches＋Lightpandaの[Bot検知と初回応答の比較](docs/bot-diagnostics-2026-10-01.md)、[共通シナリオとオプションの実行手順](experiments/bot-diagnostics/README.md)、[サイト別シナリオを追加するSkill](.agents/skills/bot-blocking-scenarios/SKILL.md)も用意した。

このNode.js診断には専用Dockerプロファイルを使う。Node・Chrome for Testing・固定Lightpandaを含み、非root・読み取り専用で動作する。実行結果は共有volumeの`/data`へ保存する。

```bash
docker compose --profile bot-diagnostics build bot-diagnostics
docker compose --profile bot-diagnostics run --rm bot-diagnostics plan --all-options
docker compose --profile bot-diagnostics run --rm bot-diagnostics smoke /data/new-session-fixture
docker compose --profile bot-diagnostics run --rm bot-diagnostics options-smoke /data/new-options-fixture
```

HTTPSプロキシと配布CAを使うCloud向けの追加設定、固定バイナリの再利用、検証済みZIPの取り出しは[診断用Dockerの手順](experiments/bot-diagnostics/README.md#dockerで実行する)に記載した。

Crawlee Python 1.10.3、Impit 0.14.1、SessionPoolと、Patchright 1.63.0＋Chromiumを任意の環境として用意している。通常イメージには追加しない。

```bash
docker compose --profile tools run --build --rm crawl-tools
docker compose --profile browser run --build --rm browser-tools
```

両コマンドは導入確認。Crawleeでローカルfixtureを1件取得し、browser環境では外部通信を無効にしてJavaScript実行も確認する。実サイトのブロック突破性能を測るものではない。ツールを使うコードを実行する場合は `--entrypoint python` で入口を指定できる。

Codex Cloudへまとめて導入するSetupは `bash scripts/cloud_setup.sh browser`、HTTPツールだけなら末尾を `http` にする。`"transport": "impit"` でブラウザを起動せずImpitのHTTP取得を使える。[導入手順](docs/cloud-lab.md#取得ツールの配備)

AdaptivePlaywrightCrawlerを実験へ接続する場合は `bash scripts/cloud_setup.sh adaptive` を実行し、新しい実験設定で `"transport": "adaptive"` を指定する。本文・リンクの比較からHTTP取得とブラウザ描画を選び、判定履歴は再開後も保持する。Dockerでは `docker compose --profile adaptive run --build --rm adaptive --help` が入口になる。

制限付きCodex Cloudコンテナでは、環境変数欄に `CRAWLEE_DISABLE_BROWSER_SANDBOX=true` を設定してからAdaptiveのsetupを実行する。ブラウザ内部のsandboxを無効化するため、隔離されたCloud環境で使用し、資格情報は持ち込まない。Docker用イメージには設定済みだが、Cloudへは自動では引き継がれない。

比較用の二重取得とJSの追加リクエストも同じ予算へ計上し、元HTMLと描画後DOMを分けて保存する。この経路のHTTPはImpitのChromeプロファイルを使い、直接通信では検査済みpublic IPへ固定する。識別用User-AgentとTLS証明書検証を維持する。Patchright、サイト固有の対策、クリック・無限スクロールは接続していない。

## Codexクラウドで使う

接続するGitHubリポジトリへコードを置き、Pythonを3.12以上、SetupとMaintenanceを `bash scripts/cloud_setup.sh` にする。標準構成にCodex CLI・Node.js/npmは不要。Kaggleも使う場合は続けて `bash scripts/setup_kaggle.sh` を実行する。クロール対象ドメインだけAgent internet accessで許可する。調査モデルは実行画面でGPT-6 Lunaを指定する。[設定と運転手順](docs/cloud-lab.md)に、最初の依頼文・承認・再開・保存方法をまとめた。

このローカルフォルダ全体をアップロードせず、小さい配布物を作る場合:

```bash
python3 -B scripts/package_cloud.py --output .lab-output/discovery-lab-cloud.zip
```

ZIPは標準Python基盤とBot診断のコード・テスト・Docker・Skillを含む配布用。公開ソースの許可リストから作り、CRCとSHA256を照合する。実験途中の保存には `jse.lab export`、Bot診断には `experiments/bot-diagnostics/export.py` を使う。

## GPUが必要な場合

[Kaggle操作スキル](.agents/skills/kaggle-ops/SKILL.md)を使い、個人用保管庫のネットワークシークレット `KAGGLE_API_TOKEN` でCloudから直接接続する。`api.kaggle.com` と `www.kaggle.com` を送信先として設定し、`bash scripts/setup_kaggle.sh` で専用venvへ固定バージョンを導入する。GPU残量の確認、Notebook送信、versionを固定した待機、結果回収まで同梱helperで実行する。設定フォルダは書き込み可能な `.deps/kaggle-config` を自動選択し、トークンをファイルへ保存しない。通常コンテナにはGPUライブラリを入れない。

回収結果の `continuation.json` が検証工程への受け渡しになる。[クラウドのKaggle運転手順](docs/cloud-lab.md#kaggleへの計算委譲)を参照。終了済みCodexタスクの自動起動は別途監視機構への接続が必要。

## 動く範囲

- 同じseed・上限によるBFSとLunaのリンク選択の比較。
- HTTPSのHTML、robots、同一の許可origin内のリダイレクト、取得間隔。
- 成功だけでなく失敗・robots・リダイレクトも含むリクエスト台帳。
- 元のHTML、本文、タイトル、リンク、取得元、時刻、ハッシュの保存。
- 計画・結果の判断待ち、プロセスをまたぐ再開、二重実行の排除。
- 保存物の再照合と引用位置の検証。別途goldがある場合の注釈URL到達率。

JavaScript描画はAdaptive環境で利用できる。PDF、sitemap、Common Crawl、新しい検索索引への投入は未接続。既存の古い整形済みコーパスの欠落を、日本サイト全体やCommon Crawl全体の欠落とは扱わない。

クラウド環境への実配置・クラウド側のモデル選択・ネットワーク疎通は別の接続確認が必要。モデルの実ID、トークン、Codex利用料金はこの実行器では機械検証できず、レポートに未測定として残す。
