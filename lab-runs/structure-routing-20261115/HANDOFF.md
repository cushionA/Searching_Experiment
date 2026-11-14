# 最終記録 2026-11-15（以下の旧再開コマンドは実施済み）

**比較試行・採点・採否判断を終了。現C1は本番採用見送り。100対象の連続取得目標は未達。** 最新アーカイブとSHA256は `CLOUD-CHECKPOINT.json`。最終判断は `FINAL-DECISION.json`、主報告は `report.md`、全80台帳は `cloud-all-80-sites.csv`。

- 主比較480行（80×B1/C1×cold/G/R）、B0 80行、公開課題本番20行（旧accidental20行は別保持）、楽天/au PAY補足12行を保存。独立gold v2を再選定・候補入力していない。元練習60行は不変。
- 主比較gold到達・期待型HTML保存 B1 1/88、C1 3/88、G13/80対15/80、R7/80対9/80。全80と独立verified47分母は併記。異なる環境層と503が多く方式優劣を断定しない。
- B0は45サイト84文書が全503、33サイトはverified固定URLなし、2アクセス停止。公開課題は12アクセス停止、8実行/成功0。楽天61/auPAY62の補足4入口も503で、商品リンクからの一般化は未成立。
- 段階Cは既存の独立一覧DOM11件から各100URLをbulk前にv4として固定。hash `56e0834d4ace84cb3d984ad77f3c72de66ae875b76dc209974a4dc0470aa2a1e`。候補にgold URLは入力せず、各1本の連続取得は入口503で1文書終了・到達0/100。10/30/100費用・増分はnull。HTMLキャッシュやfixtureを新規100件へ数えない。
- 主予算1794/3500、残1706。段階C124/400、残276（選定課金/保守的予約120 +bulk2 +通信診断2）。unknownを0にしない。中断77文書、段階C中断v2/60・v4/58の予約と証跡は維持。
- proxy HTTP1/ブラウザdirect各1文書も503。curl robots404/Git接続の回復だけではブラウザの健康を証明しない。C2は通信/UIの問題を直せると仮定せず追加せず、価値は未測定。B2へ置換なし。native使用量unknown。
- guardは終了前offline seal、5秒有界drain、pending拒否後rootdetachを使用。6guard +8integration +1recovery localhost tests。旧逸脱はdeviations.json/cloud-deviations.jsonに保持。最新verifyはallow-incompleteなし。

追加研究をする場合は既存結果を置換せず、ブラウザの有効HTML取得を残予算内の最小診断で確認してから別環境層として比較する。完了480行のresume再取得や予算リセットはしない。下の390行時点・旧手順は履歴資料であり、未完了65〜80の再開命令は既に実施済み。

---

# Cloud継続追記 2026-11-15（この節を先に読む）

**全工程未完了。環境上流503で新規公開取得を停止中。** 最新の保存メタデータは `CLOUD-CHECKPOINT.json`。下の旧引き継ぎは元資料として保持し、Cloudの現在値には `report.md`、`verify-cloud.json`、`cloud-failure-analysis.json` を使う。

- 元ZIP SHA256一致、inventory1277全件復元。元60行はそのまま保持。独立gold v2は再取得も再選定もしていない。
- 主比較390行。01〜64は各6行（アクセス停止・中断予算・上流503を含む処理記録）、65〜67はcold2行のみ、68〜80は未実行。完了行を再取得しない。
- 07/C1等の元逸脱はdeviations.json、Cloud13/B1の追加計数差はcloud-deviations.json。**現在のguardは `seal()` でcontextをofflineにしてからcontext.closeする。** root/iframe全targetでNetwork.enableとcache無効化、再帰OOPIF、非同期callback後の上限再確認を含む。5 guard tests +8 integration tests +動的課題localhost +lab81/0fail/2skipを確認。
- 途中で古いguardを読み込んだ18/20/21の実行を停止し、未記録分は各方式の残枠全部を予約した。interrupted-executions.jsonの77文書を全体・サイト方式の両方で計上する。追加取得しない。正確な費用はunknown。段階C v2中断も60文書予約、保存済み部分HTML/DOMは維持。
- 主比較予約1556/3500、残1944。段階C予約62/400、残338。v1予約742、C1/B1既知費用と中断費用を0扱いしない。最新値は `python3 -B experiments/structure-routing/verify.py --run lab-runs/structure-routing-20261115 --allow-incomplete`。
- 直接通信のrobots接続失敗後、Cloudの継承プロキシを明示した。v2独立選定では一部200取得があったが、その後Playwright、curl（booksとpython.org）、GitHub git ls-remoteも同じ上流503。Cloud statusのconnected/unrestrictedは実通信の健康を意味しない。原因unknown。サイト側ブロックや対象不存在として扱わない。
- Cloudブラウザは `/usr/bin/chromium` 151.0.7922.173、Playwright1.63.0。旧Windows157と比較差を混同しない。`STRUCTURE_USE_ENV_PROXY=true` の時だけ既存proxy変数を使用、認証値は保存しない。TLS検証は有効。

## 通信復旧後の続行

既存承認内で進め、許可を聞き直さない。新規robots診断をする場合も費用をcloud-network-diagnostic.jsonへ追記する。継続503では多数サイトへ繰り返さない。復旧が確認できてから以下を行う。

1. 65〜80の未完了phaseのみresume。同時最大4サイト・サイト内1。`CHROME_PATH=/usr/bin/chromium STRUCTURE_USE_ENV_PROXY=true STRUCTURE_ENVIRONMENT_LAYER=cloud-linux-chromium151-proxy-sealed-20261115 python3 -B experiments/structure-routing/orchestrate.py --run lab-runs/structure-routing-20261115 --ids 65,66,67,68,69,70,71,72,73,74,75,76,77,78,79,80 --jobs 4`。他の完了行を再取得しない。上流503の既存記録を置換しない。
2. 公開動的課題は元の `--execute=true --run ...` 必須。20 accidental行を除外し、本番20行を別途記録。主比較残枠と共有する。
3. B0は新しい `b0.mjs --run ...`。独立gold固定URLだけをB0へ渡し、B1/C1へ渡さない。各サイト3文書・全体3500以内、独立goldのverified target優先。B0はb0-results.jsonl、採点はscore.mjsへ `--results=.../b0-results.jsonl --methods=B0 --outdir=.../score-b0`。
4. 段階Cのv1/v3は接続失敗で対象0、v2は中断。既存ファイルを上書きせず `stage-c-evaluate.mjs --mode select --revision v4` で新たな独立対象をbulk前に固定する。候補資産を読ませない。現在累積選定予約62、新規選定は最大58文書、2サイト合計400内。候補bulkは各140文書まで（合計280）とすれば上限400。新規選定revisionのmanifest hashと時刻を保持し、bulkへgold URLを入力しない。
5. 各siteのC1 frozen rulesをそのまま `bulk.mjs --run ... --id 01 --documents 140` と08へ1回ずつ連続取得。明確な拒否・429は止める。100が固定/存在/取得できなければ理由を残す。既存HTMLは新規取得へ数えない。後で `stage-c-evaluate.mjs --mode score --revision v4 --manifest-sha256 HASH`、`stage-c-report.py --revision v4`。10/30/100は独立成功数で累積・増分を出す。未到達はnull。
6. C2は通信障害やUI失敗をモデルで直せるとせず、200/有効HTMLの候補選択失敗に限定して追加。現時点では追加モデル0、nativeモデル費用unknown。B2へ比較を置き換えない。
7. 独立score新出力先、全80/verified47、G/R・HTML保存・誤成功・重複・欠測費用・楽天61/auPAY62・モデル価値・Skill案をreport更新。現在の `summarize_cloud.py` は390行時点のCloud障害報告用であり、全工程完了版を自動生成するものではない。
8. verify（完了時はallow-incompleteなし）と新しい名前へのpackage.py export、SHA256/CRC/inventory照合、Git永続化を行う。元archiveを上書きしない。

`verify.py --allow-incomplete` のokは証拠整合性であり、完成や方式性能の合格ではない。候補比較結果は外部製品pinchtab-webgraphの実績ではない。設計・調査・報告nativeモデルの使用量はunknown。Luna指定は行ったが実モデル独立検証はできていない。

---

# クラウド継続用の引き継ぎ

ユーザーは2026-11-15、練習10サイトの比較が一巡した区切りでpushし、クラウドで続きを行うよう依頼した。元の80サイト検証は完了していない。原依頼の最重要条件は、各対象ページのHTMLをLLMへ送って判断させず、保存した構造・規則をプログラムから再利用すること。SKU・価格・属性の抽出精度は評価対象外。

ブランチは `codex-structure-routing-20261115`、リポジトリは `https://github.com/cushionA/Searching_Experiment`。runは `lab-runs/structure-routing-20261115`。このチャットのIDは `01a1db17-919a-7ca3-8076-fff5a754c879`。

## 現在の区切り

- 80登録ドメインを `targets.jsonl` に登録済み。追加10課題は同一サイト内の `challenge_tasks.json`。サイト数に加算しない。
- 練習01〜10のB1/C1はcold、G、R各1行、合計60行を実行・保存済み。再取得しない。`site-state/{id}.json` と `results.jsonl` を両方維持する。
- B0、実サイト11〜80の候補比較、公開の追加動的課題、段階C、C2は未実施。未実施を成功扱いしない。
- 独立評価のv1は対象選定・偽ブロック判定の不具合があり、候補測定前のpreflightとして保存。v2の対象・検証根拠は候補実行前に固定し、`gold-private-v2` へ隔離した。候補はgoldを読んでいない。
- v1費用も総予算に含める。v1は742 HTTP requests、25 captured documents。iframe等を含む正確な文書数とXHRは未計測なので0と扱わない。今後のグローバル予算予約では742を文書数の保守的上限として使用する。
- 候補監査は `candidate-audit/audit.md` と `audit.json`。C1は今回の自作汎用構造ルール比較器であり、pinchtab-webgraph製品のライブ実績ではない。
- pinchtab-webgraphはWindowsのlocalhostで1状態まで、materialize失敗。smart-crawlerはJava/Maven不在とWindowsファイル名問題等で実行不可。AutopagerはPyPI 0.3.1の配布CRFでfixture推論成功、Git 0.4.0には既定重みがない。他モデルの重みは未取得。

最終保存時の件数・費用・未実施は `report.md`、`score-v3/summary.json`、`score-v3/site-status.jsonl` を参照。scoreとscore-v2は採点バグ修正前の中間版で、最終値に使わない。途中集計を最終80サイト成績と表現しない。

**追加のライブ取得前に修正必須:** 07/C1でroute側予約40に対してdocument response 41を観測した。予算逸脱1文書を `deviations.json` に保持した。原因は未確定で、redirect/iframeを含むローカルfixtureで厳密なハード上限を修正してから残りの公開取得へ進む。現在のカウンターをそのまま信頼して再開しない。

challenges自己テストの末尾フラグ解釈ミスでrobots.txtへ8 requests、公開課題の文書取得0が発生した。初報4はサイト数との誤読で、保存行のrequests合計8へ訂正した。誤起動20行は監査用で本番完了ではない。`execution_origin=accidental_preflight_flag_parse` を本番のresume完了判定に使わない。mainrunnerにも同じ末尾boolean解析を修正し、完了01のresumeで新規通信・結果増加がないことを確認済み。測定時ソースはimplementation-snapshotsへ保持した。

## 復元と環境

証拠HTMLはGit直下へ大量に追加せず、リポジトリ内の `lab-runs/structure-routing-20261115-checkpoint.zip` に保存する。`CHECKPOINT.json` のSHA256を確認してリポジトリのルートへ展開する。ZIPにはstate、独立gold、HTML/DOM、graphs/rules/paths、実行ログ、監査ログ、ソースとinventoryを含む。モデル重み・インストール環境・公開候補のclone全体は含まない。

```bash
git fetch origin codex-structure-routing-20261115
git switch codex-structure-routing-20261115
python3 -m zipfile -e lab-runs/structure-routing-20261115-checkpoint.zip .
npm install --prefix .deps/bot-diagnostics --ignore-scripts --no-audit --no-fund playwright@1.63.0 robots-parser@3.0.1
```

Node 22以上、Python 3.12以上を使う。既存ブラウザを優先する。存在しなければ `.deps/bot-diagnostics/node_modules/.bin/playwright install --with-deps chromium` で導入する。`CHROME_PATH` があればrunner/challenges/bulkへ渡せる。Windowsの元測定はChrome `157.0.8103.51`、headless、ja-JP、Asia/Tokyo、1365×900、キャッシュ無効、service worker無効、新規context/Cookieで実施した。

クラウドでブラウザ・回線が変われば別環境層として記録し、Windows測定とのcold/warm費用差をアルゴリズム差と断定しない。同一サイトのB1/C1のブラウザ・回線・Cookie・待機条件は一致させる。goldを候補の都合で再選定しない。

## 続行の順序

1. `results.jsonl`、`site-state`、評価v2のcompletedを照合。未完了の独立対象選定だけ `node experiments/structure-routing/evaluate.mjs --version=v2 --ids=11,...,80 --jobs=2` で続行する。evaluateのCLIは `--name=value`、runnerは `--name value`。完了したgoldを再取得しない。
2. 実サイト11〜80のB1/C1を実行する。`python3 -B experiments/structure-routing/orchestrate.py --run lab-runs/structure-routing-20261115 --ids 11,12,...,80 --jobs 4`。実際には全IDを列挙する。候補と独立評価を同時実行する場合は合計4サイト以内。orchestratorはcoldとwarmを別プロセスで起動し、既存結果を再取得せずresumeする。主比較は独立選定・v1費用込み最大3500文書。予算予約により実行不可となったサイトも記録する。
3. 追加動的課題を `node experiments/structure-routing/challenges.mjs --execute=true --run lab-runs/structure-routing-20261115 --methods B1,C1` で実行する。通常の各方式・同一サイトcold+G+Rと文書40/UI100予算を共有し、残予算不足なら未実施理由を残す。JS、Load More、AJAX、iframe、Shadow DOM、仮想スクロールの可否を別採点。URL直達だけでは課題成功にしない。
4. B0は独立goldの少数の固定URLだけを専用manifestとして渡し、各サイト最大3文書。B1/C1へgold URLや専用セレクタを渡さない。
5. 段階Cは練習サイト内の十分な件数がある2サイトを独立に選び、最大100対象ずつを先固定する。02は対象にrobots制限があり避ける。候補は01と08だが100件存在を未確認。100未達なら存在・取得できた数と理由を記録し、外部サイトを無断追加しない。`bulk.mjs --run RUN --id 01 --documents N` は保存C1規則を使う連続取得器。goldを入力しない。対象選定・中間・対象HTML込み2サイト合計400文書、1本の実行ログから10/30/100の独立対象到達時点を後採点する。候補側のvalid100と独立gold100到達は別。構造再利用とHTMLキャッシュを分ける。
6. C1の失敗原因を分け、候補選択・ページ型分類に改善余地がある時だけMiniLM-L6-dmr等でC2を試す。robots/取得拒否/実行器未対応をモデルで直せると仮定しない。ローカル推論も回数・入力長・候補数・時間を計測する。外部API20 calls/合計20000 tokensを超えない。元比較は外部APIとページ判断モデル0。親の設計・調査・レポート作成のモデル使用量はAPIから未公開のためunknown。
7. `node experiments/structure-routing/score.mjs --candidate-run=lab-runs/structure-routing-20261115` で独立採点。G/R機構成功と固定gold対象の到達を別に出す。全80サイト分母と取得・検証可能なサイト分母を併記。レポート完成後に `package.py` で再export・SHA256検証する。既存ZIPは上書きせず新しいcheckpoint名を使う。

## 制約と残課題

- 既存400M索引・過去実験・ローカルの他ユーザー差分を移動/削除しない。今回のcommitに既存bot-diagnosticsの変更は含めない。
- 公開閲覧・検索・ページ送り・表示切替のみ。注文、投稿、問い合わせ、登録、ログインは禁止。明確な拒否、429、アクセス条件で停止する。関連ホストは同じサイトの予算を共有する。
- Gは既観測グラフの再生、Rは本文未観測の同型ページへの凍結規則の適用。全ページ先取得の地図をRと扱わない。query/hashを無条件に落とさない。iframeも文書予算へ計上する。
- 候補のDOMからLLMにページごとの判断をさせない。原文・HTML・長いログは会話へ出さずファイルへ。
- サイト専用セレクタの手作業0。共通評価器・実行器のpreflight修正はあり、厳密な修正回数は未計測。元のSkillは静的パーサーコードから代表経路を作るもので、ライブ構造探索・大量反復が既存機能だけで成立したとは仮定しない。
- 日本語で短く対応する。調査の委譲は指定 `gpt-6-luna`。native spawnの指定は行ったが実モデルの独立確認はできず `model_runtime_verified=false`。
- ユーザーは現方式全滅時の追加候補としてサイトマップ方式B2を提示した直後に「今のはそのまま続けて」と指示。現行比較を継続し、B2へ目的を置き換えない。必要時は既製Crawlee/Scrapyのsitemap部品、URL型分類、代表DOM検証を比較候補にする。サイトマップの列挙はG/Rのクリック経路発見とは区別する。

このチャット自身をCodex Cloudへ移動するhandoff APIは未対応。pushしたブランチとこのファイルから新しいクラウドチャットを開始すれば継続できる。クラウド保存環境 `Searching_Experiment` は確認済みで、新規有料クラウド基盤の展開は不要。
