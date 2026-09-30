# Codexクラウドでの調査・検証

## 役割と判断点

実行器がJSONで次に必要な判断材料を返し、調査回答をGPT-6 Lunaが担当する。親タスクが別モデルでも、対応する環境ではLuna専用サブエージェントへ要求JSONだけを渡せる。任意の独立CLI/API実行経路も用意している。標準の `run` はエージェント回答待ちで終了し、勝手にモデル呼出しや認証を始めない。

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
5. 親自身が回答する場合は実行画面でGPT-6 Lunaを選ぶ。親が別モデルなら以下のLunaへの委譲手順を使う。Cloudの利用権限やspawn機能の有無はリポジトリ設定だけでは変更できない。Codex CLIの導入・ログインは不要。UI・サブエージェントの指定だけでは実モデルの独立検証にならず、`model_runtime_verified=false`。Responses APIではサーバー応答のモデルと回答の保存証跡を記録する。
6. クロールにはSecretsは不要。Kaggle計算は個人用保管庫の `KAGGLE_API_TOKEN` を使い、Cloudから直接接続する。送信先は `api.kaggle.com` と `www.kaggle.com`。Setupに `bash scripts/setup_kaggle.sh` を追加する。値をチャット・Git・Notebook・設定ファイルに保存しない。

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

### 同一run内の通信再試行

結果判断待ち（`awaiting_human`・result gate）で、ページ未取得のarmの最後の試行が `ProxyError` / `TimeoutError` の場合に限り使える。試行・HTTP・本文bytes・モデル要求・過去回答を保持し、失敗したseedを同じarmへ戻す。対象・上限・runは変更しない。予約済みbytesも返却しない。結果整理用の要求予算が残っていなければ拒否する。

```bash
python3 -B -m jse.lab retry --run lab-runs/cloud-pilot-001 --note '同一run・残予算内で再試行を許可'
python3 -B -m jse.lab run --run lab-runs/cloud-pilot-001
```

noteは実際のユーザー許可を記録する欄であり、この例を許可として使わない。HTTP 403、robots拒否、範囲外、成功済み・完了済みrunには適用しない。

### Proxyの診断

まず通信せずに経路・導入状況を確認する。`--probe-proxy` は環境proxyへのDNS/TCP接続だけを行い、取得先へのCONNECT・TLS・HTTPは送らない。成功しても取得先へのCONNECT許可や青空文庫からの応答を確認したことにはならない。

```bash
python3 -B scripts/check_cloud_environment.py --adaptive
python3 -B scripts/check_cloud_environment.py --url https://www.aozora.gr.jp/robots.txt --probe-proxy
```

取得台帳に `error` に加えて `error_type`、安全化した `error_detail`、`failure_stage_hint`、URLごとの `network_route`、資格情報を含まない `proxy_scheme/host/port` を保存する。Impitとurllibは同じNO_PROXY条件を適用する。環境のproxy設定自体は変更しない。Cloudが強制するproxyは、NO_PROXYへの追記で回避できるとは限らず、対処として勝手に追記しない。

失敗したrobots URLでクライアントの違いを比較する場合は、結果判断待ちで次を実行する。対象URLはそのarmの過去の失敗から選び、新しいoriginは追加しない。通常のHTTPと同じ待機・回数・本文bytes上限を消費し、`proxy_diagnostic` として保存する。設定のtransportと探索履歴は変更しない。診断後は台帳が変わるため結果判断IDを更新する。

```bash
python3 -B -m jse.lab diagnose --run lab-runs/cloud-pilot-001 --arm bfs --client urllib
python3 -B -m jse.lab diagnose --run lab-runs/cloud-pilot-001 --arm luna --client impit
python3 -B -m jse.lab verify --run lab-runs/cloud-pilot-001
```

両クライアントでproxy接続が失敗するなら環境経路の問題、urllibだけ成功するならImpitとの互換性を疑う材料になる。いずれも原因の確定ではない。例外内の403/407/502と取得先のHTTP応答を区別する。`failure_stage_hint` は例外文字列からの手掛かりであり、DNSのどちら側・TLS終端・接続先IP・サイト側拒否を断定しない。詳細を捨てた過去のcheckpointから元の例外本文は復元できない。

この変更の作業環境では、通常sandbox内でproxyへのTCP接続がPermissionErrorになり、ネットワーク許可後の同じ検査ではTCP接続に成功した。元レポートの `http://proxy:8080` とは異なる環境であり、元のCloud失敗原因を確定した結果ではない。

同じrunでの追加診断ではImpit/urllibとも `https://www.aozora.gr.jp/robots.txt` をHTTP 200で取得し、同一の115bytesとSHA256を保存した。各armは累計3 HTTP・2,000,115 charged bytesとなった。この環境でImpit 0.14.1とproxy経路が動くことは確認できたが、元のCloud proxyのCONNECT許可・障害時の応答は未確認である。

レポートの3コミットの変更は [PR #1](https://github.com/cushionA/Searching_Experiment/pull/1) に `bc6e58e` としてまとまっており、再試行2回までを含む元の `cloud-pilot-001` checkpointも同PRから引き継ぐ。元checkpointの過去の例外本文は欠落しているため、今回の詳細保存で遡って復元はできない。

### 別モデルによる回答

| 経路 | 親と異なるモデルの指定 | 実行条件・検証 |
|---|---|---|
| Nativeサブエージェント | `.codex/agents/luna.toml` またはspawn時の `gpt-6-luna` 指定 | 親の会話側にモデル指定spawnが必要。CLI・APIキーは不要。コンテナから利用権限や実モデルは検証できない |
| Responses API | `agent --backend responses` が `model: gpt-6-luna` を送信 | 実行プロセスのOPENAI_API_KEYとapi.openai.comへのPOST許可が必要。APIは別課金。サーバー応答のモデルを確認 |

Native経路では親のモデルを変更する必要はない。例えば親へ「調査回答はLunaサブエージェントに委譲し、active_requestのJSONだけを渡して」と依頼する。親が要求JSONを読む→LunaにJSONだけを渡す→回答を `answer --file ...` で検証・保存→親が `run` を続ける。LunaにBFSの取得履歴やcheckout全体を読ませない。Cloudでcustom agent設定の読み込みやモデル指定spawnを使えない場合、この経路は未接続として扱う。

Codex CLIの導入経路は削除した。以下の `agent` は `status=awaiting_agent` のrunに対してだけ使う。保存済みのrunを再開するときは先に `status` を確認する。

API利用が明示的に許可され、実行プロセスへ安全にキーを渡せる環境では次を使える。Cloudのsetup Secretを登録しただけではagent phaseで利用できないので、Native経路、信頼できる別の実行環境、または管理された認証経路が必要になる。

```bash
python3 -B scripts/check_cloud_environment.py --agent-backend responses
python3 -B -m jse.lab agent --run lab-runs/pilot-001 --backend responses --max-output-tokens 4000
python3 -B -m jse.lab run --run lab-runs/pilot-001
```

`agent` は1つのactive_requestだけを新規セッションへ渡し、自動のクロール・承認・新run作成は行わない。要求ハッシュ、要求モデル、既存の回答形式・候補URL・引用を検証する。APIにはツールを渡さず、JSON Schema付きResponses APIを使う。認証・403・モデル利用権限のエラーでは停止し、代替モデルやdirect通信へfallbackしない。

モデル呼出しを開始する前に台帳へ予約し、失敗・中断も1回と数える。各要求の既定上限は1回。明示的な再実行は `--retry-call --max-calls 2`（最大3回）。HTTP予算と別に `model_calls`、usage、`model-calls/*.json` を保存し、verify/exportへ含める。自動再試行はしない。`--max-output-tokens` はResponses APIで適用される。

`model_runtime_verified` は回答済み要求のすべてがサーバーのモデルフィールドと保存回答に対応する場合だけtrueになる。Native/手動回答を混ぜたrunではfalse。料金の推測はしない。APIの応答モデルがLunaまたはLunaの日付snapshotでなければ回答を受け付けない。preflightは推論を送らないため常にfalseであり、認証情報の存在や導入確認だけでモデル利用権限を保証しない。

公式資料: [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)、[Structured model outputs](https://developers.openai.com/api/docs/guides/structured-outputs)。

### 一時停止とcheckpoint

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
| エージェント予算 | 発行する要求数。独立実行ではモデル呼出しと取得できたusageも保存する。Native/Cloud内部の思考量・料金は計測できない |
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

KaggleはCloudから直接接続する。個人用保管庫のネットワークシークレット `KAGGLE_API_TOKEN` を環境へ紐付け、送信先を `api.kaggle.com` と `www.kaggle.com` に設定する。`KAGGLE_USERNAME` はaccess token認証では必須ではない。値を引数・Git・Notebook・認証ファイルへ保存しない。

```bash
bash scripts/setup_kaggle.sh
.deps/kaggle-venv/bin/python -B .agents/skills/kaggle-ops/scripts/kaggle_ops.py doctor
.deps/kaggle-venv/bin/python -B .agents/skills/kaggle-ops/scripts/kaggle_ops.py quota
```

専用venvへKaggle 2.0.0、kagglesdk 0.1.37、python-dotenv 1.2.3を導入する。helperはKaggle import前に書き込み可能な `.deps/kaggle-config` を自動選択し、明示済みの `KAGGLE_CONFIG_DIR` は維持する。通常SetupはGPUを開始しない。

Notebookは非公開、転送専用フォルダから送信する。`training-params.json` の `timeout_seconds` と取得したGPU残量を確認する。実験ごとに新しい結果ディレクトリを使い、既存jobを上書きしない。

```bash
.deps/kaggle-venv/bin/python -B .agents/skills/kaggle-ops/scripts/kaggle_ops.py submit --folder experiments/gpu/my-notebook --output .lab-output/kaggle-job-001
.deps/kaggle-venv/bin/python -B .agents/skills/kaggle-ops/scripts/kaggle_ops.py wait --job .lab-output/kaggle-job-001/job.json --output .lab-output/kaggle-job-001 --wait-seconds 3600
```

返ったref・version・ソース署名をjob.jsonへ保存し、同じversionのstatusとログを確認して出力を回収する。新規名へのGetKernelの403は、自分のNotebook一覧全ページに存在しないことを確認できた場合だけ新規作成として扱う。既存Notebookや他人の403は権限エラーのまま止める。

送信応答が曖昧でも再送しない。保存された新しいversionのコード・非公開設定・GPU/Internet設定が一致した場合だけ照合して待機へ進む。一致しなければsubmission_unknownを保持し、次のreconcileで指定versionと元ソースを照合する。

```bash
.deps/kaggle-venv/bin/python -B .agents/skills/kaggle-ops/scripts/kaggle_ops.py reconcile --job .lab-output/kaggle-job-001/job.json --version N
```

| continuation.json | 次の行動 |
|---|---|
| `ready_for_verification: true`, `next_phase: verification` | manifestのSHA256と計算結果を検証する |
| `failure_review` | 保存ログと残予算を確認する。自動再投入しない |
| `waiting_kaggle` / `waiting_timeout` | 同じref・versionで待機を再開する |
| `recovering_outputs` | 同じversionの回収を再開する |
| `submission_unknown`（job.json） | 新しいversion・元ソース・設定が一致するまで再送しない |

読み取り通信の一時的失敗は最大3試行。待機時間切れだけでジョブを停止・再投入しない。`continuation.json` だけでは終了済みCloudタスクを自動起動できないため、継続時は同じjob.jsonを渡す。

非公開・外部通信なしのダミーNotebookでTesla T4を2基認識し、両方のCUDA行列積・完了・出力回収・SHA256照合まで検証済み。[検証記録](cloud-validation.md)

## 取得ツールの配備

ブロッキング対策を必要時に試せるようにするための事前配備。サイト専用の調整、fingerprintの追加注入、プロキシ契約、自動fallbackは含めない。Camoufoxは未導入。

| 環境 | 配備内容 | Codex CloudのSetup / Maintenance |
|---|---|---|
| core | 標準ライブラリによる既存の実験器 | `bash scripts/cloud_setup.sh` |
| http | core＋Crawlee Python＋Impit＋SessionPool | `bash scripts/cloud_setup.sh http` |
| browser | http＋Patchright＋ChromiumとOS依存 | `bash scripts/cloud_setup.sh browser` |
| adaptive | browserの依存＋AdaptivePlaywrightCrawler＋Playwright | `bash scripts/cloud_setup.sh adaptive` |

バージョンは `requirements/crawl-tools.txt`、`requirements/browser-tools.txt`、`requirements/adaptive-tools.txt` に固定している。追加環境のsetupではPyPI・ブラウザ配布元・OSパッケージ取得へのネットワーク接続が必要。API Secretや有料サービスは不要。Cloud環境でOS依存の導入権限がない場合、ブラウザ対応済み環境で実行する。導入に失敗した状態を配備済みと報告しない。

`adaptive` setupは、まず外部通信なしのfixtureでPlaywright Chromiumの起動とJavaScript実行を確認する。成功時は既存ブラウザを再利用し、失敗時だけ `playwright install --with-deps chromium` を実行してから同じ確認を再実行する。実行ファイルの存在だけで導入済みとは判定しない。

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
