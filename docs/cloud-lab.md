# Codexクラウドでの調査・検証

## 役割と判断点

Codex側がGPT-6 Lunaとして調査し、実行器がJSONで次に必要な判断材料を返す。APIキーを埋め込まず、クラウドタスク自身のモデルを利用する。Python単独ではエージェント回答待ちで終了する。

```text
init → run → 計画の要求 → Lunaの回答 → 計画判断
  → BFS → Lunaの候補選択と取得を反復 → 保存物の検証
  → Lunaの結果整理 → 結果判断・次案 → 終了
```

人が見る単位は対象・上限・方針・結果。ユーザーが当該runの対象と全体上限を既に許可した場合、計画判断ではその許可を記録して進めてよい。ページや候補ごとの確認は不要。予算を変更する場合はstateを編集せず、結果を示し、許可後に別の設定とrunを作る。runを量産して同意済みの総予算を迂回しない。

## クラウド環境の設定

1. 接続先は `cushionA/Searching_Experiment`、branchは `main`。`scripts/package_cloud.py` は必要なコードだけをZIP化し、巨大な過去データを含めない。
2. CodexのEnvironment設定でPython 3.12以上を指定する。
3. Setup script: `bash scripts/cloud_setup.sh`。Maintenance scriptも同じ。標準構成では依存のダウンロードや有料実行はしない。取得ツールを配備する場合は、以下の `http` / `browser` 引数を使う。
4. オフラインdemoではAgent internet accessを無効のままでよい。実サイトを取得するときだけ有効にし、設定JSONの `allowed_origins` に対応するドメインとGETを許可する。別hostへリダイレクトするサイトは、実験前に両方を許可する。
5. 実行画面のモデル選択でGPT-6 Lunaを指定する。利用できない場合は接続未完了として止める。CLIの `codex cloud exec` にモデル指定フラグがある前提にしない。モデルの自己申告は独立検証ではないため `model_runtime_verified` は常にfalseとして保存する。
6. クロールにはSecretsは不要。任意のKaggle計算は以下のGitHub Actions経路を使う。Codex CloudのSecretsはsetup後に除去されるため、setup中のキーをファイルへ残してagent phaseへ渡さない。

Codex Cloudの公式経路は標準 `universal` イメージとsetup scriptである。このリポジトリのDockerfileをクラウド設定が直接ビルドするとは想定しない。Dockerfileはローカル・通常のLinuxコンテナで同じPythonコードを検証するために用意した。

公式資料: [Cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environment)、[Agent internet access](https://learn.chatgpt.com/docs/cloud/internet-access)、[Developer commands](https://learn.chatgpt.com/docs/developer-commands)。

## 最初の依頼文

対象と上限を具体化して、モデルを指定したクラウドタスクへ渡す。

```text
AGENTS.mdとdocs/cloud-lab.mdに従い、発見型クロールの比較実験を進めてください。
調査担当はGPT-6 Luna。対象は <サイトのHTTPS origin>、seedは <URL>。
調べたい内容は <情報の種類と対象時点>。網羅性を優先する。
experiments/pilot.example.jsonを基に設定を作り、runはlab-runs/pilot-001を使う。
上限は各armで12ページ・20試行・32 HTTPリクエスト・4MB、
エージェント要求は計画と総括を含め合計10回。対象host追加や増額は判断を求める。
この範囲内の調査、候補選択、小さな比較、検証は事前に許可する。
計画の判断点ではこの指示をnoteに記録して続行し、結果と次案まで進める。
失敗と未取得を区別し、正解集合なしで網羅率を断定しない。
最後にverifyとexportを行い、成果物と継続方法を示す。
```

上記は貼り付け用のテンプレートであり、それ自体を現在のrunへの承認とみなさない。Lunaが不明確な情報を見つけたら、既存の上限内で探索を修正してよい。別の取得方式・別host・大きな計算が必要なら、その案と必要予算を人へ返す。

## 実際のコマンド

`experiments/pilot.example.json` のobjective、seed、allowed_origins、連絡先を含むuser_agentを設定した `experiments/pilot.json` を作る。

```bash
python3 -B -m jse.lab init --config experiments/pilot.json --run lab-runs/pilot-001
python3 -B -m jse.lab run --run lab-runs/pilot-001
python3 -B -m jse.lab status --run lab-runs/pilot-001
```

`awaiting_agent`: 出力の `active_request` を読む。`response_format` に沿ってLunaが回答JSONを作り、次を繰り返す。requirementsにある文字列は説明文なので、そのまま回答として返さない。

```bash
python3 -B -m jse.lab answer --run lab-runs/pilot-001 --file /tmp/lab-answer.json
python3 -B -m jse.lab run --run lab-runs/pilot-001
```

`awaiting_human`: `lab-runs/pilot-001/DECISION.md` に案と判断IDがある。未承認ならクラウドタスクを終了して人に判断を求める。ユーザーが同じチャットで「そのIDの案で進めて」と答えた場合、または当該対象・総上限を明示的に事前許可している場合に次を行う。

```bash
python3 -B -m jse.lab decide --run lab-runs/pilot-001 --id <判断ID> --decision approve --note '<実際のユーザー許可と対象範囲>'
python3 -B -m jse.lab run --run lab-runs/pilot-001
```

却下は `--decision reject`。計画の却下では取得を始めない。結果の承認ではそのrunをcompleteとし、自動で次runを始めない。

## 保存・停止・再開

```bash
python3 -B -m jse.lab pause --run lab-runs/pilot-001
python3 -B -m jse.lab resume --run lab-runs/pilot-001
python3 -B -m jse.lab run --run lab-runs/pilot-001
python3 -B -m jse.lab verify --run lab-runs/pilot-001
python3 -B -m jse.lab export --run lab-runs/pilot-001 --output pilot-001-checkpoint.zip
```

pauseは次の取得境界で反映する。強制停止した取得は確定できないため、再開時に失敗・未確定として残す。HTTP前にリクエスト数と最大応答サイズを予約して保存し、正常終了時のみ実測バイトへ減額する。プロセス停止で上限が元に戻ることはない。エラー中の消費量は保守的に最大応答サイズを数える。OSのファイルロックがプロセス終了時に解除されるので、古いPIDを手で削除する必要はない。

Cloudの環境キャッシュを成果物保管庫として扱わない。同じチャットのfollow-upにはrunパスを明記し、ファイルが引き継がれていることをstatus/verifyで確認する。別タスクへ渡す場合はチェックポイントを取得・保管してから、信頼できるZIPを新しいrunディレクトリへ展開するか、state、requests、answers、blobsを含む変更をGitで保存する。run.lockは引き継がない。個別の `state.json` だけでは証拠本文が足りない。

## 指標と検証の限界

| 項目 | この基盤での扱い |
|---|---|
| 取得予算 | armごと。robots、リダイレクト、HTTP失敗もHTTP数へ加算。別にURL試行数を数える |
| バイト | 応答bodyの上限。HTTP/TLSヘッダーやネットワーク全体の転送課金上限ではない |
| エージェント予算 | 発行する要求数。Codex内部の思考量・トークン・利用料金は制御も計測もできない |
| 観測 | 最近3ページの先頭6000文字ずつと、先頭N件の候補。切り詰めを表示する |
| 引用検証 | モデルへ実際に渡した原文と一致し、保存本文の同じ位置に存在するか |
| 意味検証 | 未実装。原文一致だけで人物・情報の正しさや最新性は認定しない |
| 網羅率 | goldなしではnull。取得URL数・重複除外数を別表示 |
| 比較条件 | 同一seedと上限。BFS先行・別取得なので時刻差がある。統計的な優位性は断定しない |

探索後に別評価環境で `{"target_urls": ["https://..."]}` 形式のgoldを渡す場合:

```bash
python3 -B -m jse.lab grade --run lab-runs/pilot-001 --gold /evaluation/gold.json --output /evaluation/score.json
```

これは注釈URL集合への到達率。goldが網羅的であることや意味的正解は保証しない。gold・scoreを調査側のcheckoutや次のLuna要求へ混ぜない。

## 境界

- liveはHTTPS・標準ポート・認証情報なし・許可origin内のみ。全パスが対象で、パス単位の限定は未実装。
- 直接通信はDNS結果をpublic IPに限定し、そのIPへ接続する。クラウドのHTTPS proxy利用時は許可hostを維持し、接続先IPの制御は環境proxyのポリシーに依存する。proxy側がprivate IPを拒否する保証は未確認。任意の未信頼proxyを指定しない。
- timeoutは接続と各読み取りへ適用し、bodyの残り時間も短縮する。OSのDNS待ちやレスポンスヘッダーを含む、厳密な総実行時間上限ではない。
- robotsの取得不能・非対応リダイレクトは保守的に取得を止める。JavaScript、圧縮レスポンス、PDFには対応しない。charsetはHTTP、meta、UTF-8の順。誤判定の可能性は保存HTMLで確認する。
- 一般のCodexエージェントはworkspaceとコマンドへアクセスできる。この実行器は、そのエージェントが自分でコードを書き換えることまで隔離するセキュリティ境界ではない。独立評価用goldは物理的に別の環境へ置く。

## 次の実験候補

まず小さい複数サイトで、seed・取得予算・独立した評価集合を固定して比較する。到達しない原因を、入口不足、リンク選択、JS依存、失敗、古いページ、抽出欠落に分ける。

候補は、アンカーテキストの利用、未探索の枝へ予算を残す選択、sitemapとの併用、Common CrawlのURL索引と原HTML回収。既存の過去実験では、答えが分かるURLを後から投入した結果を発見性能に数えない。新しい取得方式は独立したarmとして追加し、既存の固定条件・失敗台帳・同一評価を通してから比較する。

## Kaggleへの計算委譲

Kaggle 2.0.0とSDK 0.1.37を別プロセスに固定し、Repository Secret `KAGGLE_API_TOKEN` を `kaggle-job.yml` の操作stepだけに渡す。token値は引数・Git・Notebookへ渡さない。2026-09-30にRepository Secretを登録した。`KAGGLE_USERNAME` はaccess token認証では必須ではない。

ワークフローはpush時にはGPUを起動しない。GitHub ActionsのRun workflowまたはGitHubの操作権限がある `gh` から起動する。CloudタスクのGitHub接続がActionsの起動・読み取り権限も持つかは別途確認する。Git checkoutができるだけでその権限を得たとは扱わない。

```bash
gh workflow run kaggle-job.yml -R cushionA/Searching_Experiment -f operation=quota
gh workflow run kaggle-job.yml -R cushionA/Searching_Experiment -f operation=submit -f notebook_folder=experiments/gpu/example -f wait_seconds=18000
gh run list -R cushionA/Searching_Experiment --workflow kaggle-job.yml --limit 5
gh run view RUN_ID -R cushionA/Searching_Experiment
gh run download RUN_ID -R cushionA/Searching_Experiment --name kaggle-results --dir .lab-output/kaggle-RUN_ID
```

submitの例はテンプレート。GPU用Notebookはまだ登録していない。必要性・データ・実行時間を具体化したら、既存の許可に収まる試行を専用ディレクトリへ作成する。`training-params.json` の `timeout_seconds` とlive quotaを照合する。ワークフローはmainのコードを使うため、試行コードの保存後に起動する。出力は7日で失効するため、必要な結果を期限内に回収する。公開リポジトリのActions artifact・ログは機密保管先ではない。非公開データを扱う試行ではprivateな実行先を用意する。

開始したActions run IDを当該クロールrunの作業記録へ保存し、`job.json` のref・versionと結び付ける。実行中はversion固定のログstreamとstatusを確認する。stream終了だけで成功とせず、Kaggle completeと出力回収を経て継続する。

| continuation.json | 次の行動 |
|---|---|
| `ready_for_verification: true`, `next_phase: verification` | 出力manifestのSHA256を再計算し、目的に対する結果を検証して調査へ戻る |
| `failure_review` | 保存ログを調べ、修復案を作る。再投入は残予算と既存許可を再確認する |
| `waiting_kaggle` / `waiting_timeout` | 同じref・versionで待機を再開する。新しいNotebookを送らない |
| `recovering_outputs` | 同じversionの出力回収を再開する。取得未完了を成功扱いしない |
| `submission_unknown`（job.json） | Kaggle側の時刻・コード・新しいversionを照合するまで再送しない |

```bash
gh workflow run kaggle-job.yml -R cushionA/Searching_Experiment -f operation=resume -f kernel_ref=owner/kernel-slug -f kernel_version=7 -f wait_seconds=18000
```

Actions自体のRe-runでsubmitを再実行するとhelperが拒否する。APIの一時的な読み取り失敗は最大3試行。GPU再実行を伴う修復は別試行として数え、失敗でも予算を返却しない。許可した総時間を使い切ったら止める。`auto_resume.py` の無制限再開や別プロセスによる予算補充は移植していない。

この受け渡しは稼働中のCodexエージェントがActions完了を待って継続するためのもの。`continuation.json` だけでは終了済みCodex Cloudタスクを自動起動できない。長時間ジョブでタスクを終了する場合、実際のrun IDが得られた時点で利用可能なCodex監視機能へ接続し、完了・失敗・人の判断が必要な変化だけ通知する。監視機能がない環境では同じチャットへrun ID付きで継続を依頼する。

## 取得ツールの配備

ブロッキング対策を必要時に試せるようにするための事前配備。サイト専用の調整、fingerprintの追加注入、プロキシ契約、自動fallbackは含めない。Camoufoxは未導入。

| 環境 | 配備内容 | Codex CloudのSetup / Maintenance |
|---|---|---|
| core | 標準ライブラリによる既存の実験器 | `bash scripts/cloud_setup.sh` |
| http | core＋Crawlee Python＋Impit＋SessionPool | `bash scripts/cloud_setup.sh http` |
| browser | http＋Patchright＋ChromiumとOS依存 | `bash scripts/cloud_setup.sh browser` |

バージョンは `requirements/crawl-tools.txt` と `requirements/browser-tools.txt` に固定している。追加環境のsetupではPyPI・ブラウザ配布元・OSパッケージ取得へのネットワーク接続が必要。API Secretや有料サービスは不要。Cloud環境でOS依存の導入権限がない場合、ブラウザ対応済み環境で実行する。導入に失敗した状態を配備済みと報告しない。

通常のDocker最終イメージはcoreのまま。`--target crawl-tools` と `--target browser-tools` を明示した場合だけ追加依存を含む。ブラウザは非rootで起動し、書き込み先をtmpfsにする。ローカル・CIの起動確認は外部サイトを使わない。

Dockerを使わない環境では専用venvへ次を実行する。LinuxでOS依存も必要ならinstallに `--with-deps` を付ける。

```bash
python -m pip install -r requirements/browser-tools.txt
python -m patchright install chromium
python -B scripts/check_crawl_tools.py --browser
```

現在の `jse.lab` は `fixture` / `live` の取得経路だけを扱う。このツール配備でブラウザ取得が自動的に組み込まれるわけではない。将来接続するときは、リダイレクト・JSの追加通信・再試行も取得範囲と予算に含め、HTTP原文と描画後DOMを区別して保存する。導入確認のローカルHTTP例をそのまま本番の予算管理と見なさない。

Python版の標準HTTPクライアントはImpit。TypeScript版はgot-scrapingが標準で、`@crawlee/impit-client` を追加してImpitを選べる。今回は既存コードと同じPython版を配備する。

公式資料: [Python HTTP clients](https://crawlee.dev/python/docs/guides/http-clients)、[Session management](https://crawlee.dev/python/docs/guides/session-management)、[TypeScript HTTP clients](https://crawlee.dev/js/docs/guides/http-clients)、[Patchright Python](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python)。
