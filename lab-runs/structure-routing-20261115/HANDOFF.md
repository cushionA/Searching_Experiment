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
