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
2. CodexのEnvironment設定でPython 3.12以上を指定する。`adaptive` を制限付きCloudコンテナで使う場合、環境変数欄に `CRAWLEE_DISABLE_BROWSER_SANDBOX=true` を追加する。Secretではない。Setup内の一時的なexportだけではagent phaseに引き継がれないため、環境設定に保存する。
3. Setup script: `bash scripts/cloud_setup.sh`。Maintenance scriptも同じ。標準構成では依存のダウンロードや有料実行はしない。取得ツールを配備する場合は、以下の `http` / `browser` / `adaptive` 引数を使う。
4. オフラインdemoではAgent internet accessを無効のままでよい。実サイトを取得するときだけ有効にし、設定JSONの `allowed_origins` に対応するドメインとGETを許可する。別hostへリダイレクトするサイトは、実験前に両方を許可する。
5. 実行画面のモデル選択でGPT-6 Lunaを指定する。利用できない場合は接続未完了として止める。モデルはコンテナの内側から選択・導入できないため、Codex CLIをイメージへ追加しても当該Cloudタスクのモデル可用性や実モデルIDは保証できない。モデルの自己申告も独立検証ではないため `model_runtime_verified` は常にfalseとして保存する。
6. クロールにはSecretsは不要。任意のKaggle計算は以下のGitHub Actions経路を使う。Codex CloudのSecretsはsetup後に除去されるため、setup中のキーをファイルへ残してagent phaseへ渡さない。

`adaptive`のコンテナ前提は `python3 -B scripts/check_cloud_environment.py --adaptive` で確認する。この検査はPython、Crawlee、Impit、Playwright、AdaptivePlaywrightCrawler、sandbox環境変数を検証するが、モデル実体の検証ではない。`model_selection_surface=codex_execution_ui` と `model_runtime_verified=false` を出力するのは、この境界を機械可読にするためである。

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

結果判断待ちで、ページを1件も取得できず最後の試行が`ProxyError`または`TimeoutError`だったarmは、明示的な再試行許可があれば同じrunの履歴・消費済み予算を保持して再試行できる。新しいrunで予算を戻さず、同じ失敗を無制限に繰り返さない。

```bash
python3 -B -m jse.lab retry --run lab-runs/pilot-001 --note '<実際のユーザー許可>'
python3 -B -m jse.lab run --run lab-runs/pilot-001
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

- live / impit / adaptiveはHTTPS・標準ポート・認証情報なし・許可origin内のみ。全パスが対象で、パス単位の限定は未実装。
- 直接通信はDNS結果をpublic IPに限定し、そのIPへ接続する。クラウドのHTTPS proxy利用時は許可hostを維持し、接続先IPの制御は環境proxyのポリシーに依存する。proxy側がprivate IPを拒否する保証は未確認。任意の未信頼proxyを指定しない。
- timeoutは接続と各読み取りへ適用し、bodyの残り時間も短縮する。OSのDNS待ちやレスポンスヘッダーを含む、厳密な総実行時間上限ではない。
- robotsの取得不能・非対応リダイレクトは保守的に取得を止める。JavaScriptはadaptive環境のみ、PDFは未対応。Accept-Encodingはidentityを要求するが、ImpitのSDKが透過展開する応答は展開後の本文を保存・計上する。未展開の圧縮応答は拒否する。charsetはHTTP、meta、UTF-8の順。誤判定の可能性は保存HTMLで確認する。
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
| adaptive | browserの依存＋AdaptivePlaywrightCrawler＋Playwright | `bash scripts/cloud_setup.sh adaptive` |

バージョンは `requirements/crawl-tools.txt`、`requirements/browser-tools.txt`、`requirements/adaptive-tools.txt` に固定している。追加環境のsetupではPyPI・ブラウザ配布元・OSパッケージ取得へのネットワーク接続が必要。API Secretや有料サービスは不要。Cloud環境でOS依存の導入権限がない場合、ブラウザ対応済み環境で実行する。導入に失敗した状態を配備済みと報告しない。

通常のDocker最終イメージはcoreのまま。`--target crawl-tools`、`--target browser-tools`、`--target adaptive-tools` を明示した場合だけ追加依存を含む。ブラウザは非rootで起動し、書き込み先をtmpfsにする。ローカル・CIの起動確認は外部サイトを使わない。

Adaptive用Dockerイメージでは、制限付きコンテナ内のChromium起動のため `CRAWLEE_DISABLE_BROWSER_SANDBOX=true` を設定する。ブラウザ内部のsandboxが無効になるため、Composeの非root・read-only・cap_drop・no-new-privilegesを維持し、資格情報をmountしない。Codex CloudはこのDockerfileを使わないため、同じ値をCloudの環境変数欄へ別途設定する。通常ホストのsandboxは無効化しない。setup script自体は設定を上書きしない。

Adaptiveテストが一斉に `TargetClosedError` になる場合、まずChromiumの起動ログを確認する。sandboxを作れない制限付きコンテナやroot実行では、上記の環境変数を保存してsetupを再実行する。`renderings` のKeyErrorは描画前の失敗に伴って発生しうるため、保存物だけを修正しない。`TargetClosedError` 単独では原因を確定できず、設定後も失敗する場合は `Browser logs:` のFATAL・sandbox・共有ライブラリ不足などを確認する。Cloudでの復旧成功は8テストが通った結果で判断する。

Dockerを使わない環境では専用venvへ次を実行する。LinuxでOS依存も必要ならinstallに `--with-deps` を付ける。

```bash
python -m pip install -r requirements/browser-tools.txt
python -m patchright install chromium
python -B scripts/check_crawl_tools.py --browser
```

`jse.lab` でブラウザ描画も使う場合はadaptive環境を導入し、新しい設定ファイルの `transport` を `adaptive` にして通常の `init` / `run` を実行する。進行中のrunの設定を変更せず、新しい比較実験として作成する。Dockerの入口は `docker compose --profile adaptive run --build --rm adaptive`。設定ファイルをコンテナへ渡す場合は読み取り専用mountを追加する。

Adaptiveは共通の本文・タイトル・リンク集合をHTTPとブラウザで比較し、URLの類似性から次の取得方式を予測する。定期的な再比較も行う。比較対象は別々のHTTP取得なので時刻差があり、実サイトでは更新・広告の差も描画差に混ざり得る。判定履歴はarmごとに保存し、再開時に学習を再構成する。BFSとLunaの判定履歴は共有しない。

すべてのHTTP、robots、リダイレクト、JSの追加取得、比較の二重取得を既存の台帳・回数・転送量上限に通す。`transport=impit` と `adaptive` はImpitのChromeプロファイルで通信する。通常の `live` は標準ライブラリのurllibを使う。ブラウザの通信はofflineにし、許可済みGETだけを予算管理下のImpit経由で返す。Playwrightの直接通信、Patchright、fingerprint自動生成、Crawleeの自動再試行は使わない。

Impit 0.14.1には宛先IP固定APIがないため、直接通信では1リクエスト専用のループバックCONNECTトンネルを作り、DNSの全候補がpublic IPであることを確認して選んだIPだけへ中継する。元のhostnameを使ったTLS・SNI・証明書照合はImpitが行う。HTTP/3、自動リダイレクト、Cookieの永続化は使わず、DiscoveryLabの識別用User-Agentを維持する。環境のHTTPS proxyがある場合は明示的にそのproxyを使い、IP制限は従来同様proxy側に依存する。proxyの資格情報は台帳に保存しない。

台帳の `http_client` / `http_version` に実装名と応答プロトコル、`network_route` に `direct_public_ip_tunnel` または `environment_proxy` を記録する。streamの途中エラーは成功扱いせず、予約した本文予算を保持する。バイト上限は保存・処理する応答本文の上限であり、SDKの先読み、TLS、ヘッダーを含む通信課金の上限ではない。

保存する `body_sha256` は元のHTTP本文、`dom_sha256` は描画後のDOM、`http_record_index` は取得元の台帳番号。`verify` は両方のハッシュと本文・リンクの再抽出を確認し、`export` はDOMも保存する。reportには採用した取得方式と比較試行数が加わる。

追加リソースのoriginも設定とCloudのネットワーク許可に必要。サイト別のセレクタ指定は不要だが、ログインCookie、POST、WebSocket、Service Worker、追加リソースのHTTPリダイレクトは未対応。初期ページのHTTPリダイレクトは台帳で解決してからブラウザへ渡す。範囲外・robots拒否・取得失敗・予算切れは不完全な成功にせず失敗へ記録する。

描画はnetworkidle後に500ms待つ共通設定。遅いタイマー・クリック・無限スクロールまでの網羅は保証しない。DOMには1応答と同じサイズ上限を適用する。取得処理は `timeout_seconds` の3倍を目安の期限とし、残時間を待機・HTTPへ伝えるが、OSのDNS解決やブラウザ起動を含む厳密な実時間上限ではない。コンテナや実行ジョブにも時間・メモリの上限を設ける。

Python版の標準HTTPクライアントはImpit。TypeScript版はgot-scrapingが標準で、`@crawlee/impit-client` を追加してImpitを選べる。今回は既存コードと同じPython版を配備する。

公式資料: [Python HTTP clients](https://crawlee.dev/python/docs/guides/http-clients)、[Session management](https://crawlee.dev/python/docs/guides/session-management)、[TypeScript HTTP clients](https://crawlee.dev/js/docs/guides/http-clients)、[Patchright Python](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python)。

## Impit接続の実サイト確認

2026-09-30に青空文庫の作家一覧（person35）と気象庁の天気予報（bosai/forecast）を固定seedとして確認した。2方式×2arm、各arm最大2ページ・30 HTTP・保存本文3MB、追加探索なし。同じseedをBFS/Luna各armで別取得しており、Lunaの選択性能を測った試験ではない。

| 方式 | 各armの成功ページ | 各armのHTTP | 各armの本文bytes | 観測 |
|---|---:|---:|---:|---|
| impit | 2/2 | 4 | 240,136 | 青空文庫326リンク、気象庁0リンク |
| adaptive | 1/2 | 14 | 526,962 | 青空文庫は同じ本文・326リンク。気象庁はoutside_scopeで描画未完了 |

合計36 HTTP・1,534,196 bytes。全通信にImpit・HTTP/2・direct_public_ip_tunnelを記録。両runのverifyとcheckpoint exportに成功し、結果判断待ちで保存した。気象庁の失敗は追加リソースの許可範囲による停止であり、サイト側ブロックやImpitの接続不能と混同しない。追加配信元の必要性・対象・上限を検討してから再試験する。

[Linux CI](https://github.com/cushionA/Searching_Experiment/actions/runs/36603329604)では52テストが成功。coreは119,476,676 bytes、Adaptiveは1,757,103,202 bytes。生の取得結果と資格情報は公開リポジトリに含めていない。
