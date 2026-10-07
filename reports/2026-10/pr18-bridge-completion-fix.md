# 4play HTTP bridgeの初期blank誤認修正・追加待機（2026-10-07 UTC）

## 結論

初期`about:blank / complete`を対象ページの完了と誤認する問題を修正した。同じ無通信fixtureで、通常200・503・リダイレクトの遅延DOM完成が**修正前0/6→修正後6/6**。存在しない完了条件はtimeoutとなり、成功へ書き換えない。

ユーザーは先の実サイトで改善が出なかった結果を踏まえ、4playの処理修正とmainマージを明示承認した。今回の最終テスト/CI成功を条件にPR18→PR17→mainの順で統合する。以前のレポートの「改善未確認のためマージ見送り」は当時の判断であり、この承認更新により変更された。実際のmerge commit・CIはPR履歴と最終報告に記録する。

この修理はここで区切る。新サイトや新しい研究課題へ自動拡張していない。

## 方法・実装

- 最終fixture `bridge-gate-ready`のコードcommit `dcf188c`。比較旧版は`ada0ff2615a3fcba132f2fde1bbd0a912d3fe897`のserver。新旧server/helperの原文を証拠blobに保存し、`bridge-sources.json`とconditionsのSHA256で照合できる。
- Cloud Docker `--network none`、同じCamoufox 152.0.4-beta.30、4play拡張、fingerprint、表示、fixture、observe=100ms。順は旧→新→新→旧、新規profile各2回。7ケース×4profile=28ナビゲーション。公開サイトへの追加取得なし。
- 共通ToolAdapter/Evidenceを通す。新旧の差はbridgeの完了判定、関連エラーのscope、明示したDOM追加待機と結果metadata。native hybridの制御経路と混同しない。
- `navigation-gate.cjs`は同じtab ID/container、HTTP(S)の最終URL、main-frame応答、tab completeを要求する。fragmentは除外してURL照合。別tab/subresourceやredirect元だけの応答を完了根拠にしない。
- HTTP 503は有効な応答として待つ。faviconの失敗は対象文書のnetwork failureとして扱わない。URL/tab/containerが一致するネットワーク失敗は停止できる。

## 結果と原因への寄与

|ケース|旧版（各2回）|修正版（各2回）|
|---|---|---|
|200＋500ms遅延画像|DOM未完成 2/2|DOM完成 2/2|
|503＋同じ画像|DOM未完成 2/2|DOM完成 2/2、503を保持|
|302→200＋同じ画像|DOM未完成 2/2|最終文書DOM完成 2/2|
|load後900msのJS、追加条件なし|未完成 2/2|未完成 2/2（追加待機なし）|
|同じJS、表示要素`#ready`に`loaded`を要求|旧版は条件未対応、未完成|ready・DOM完成 2/2|
|存在しない`#missing`、上限500ms|旧版は条件未対応|timeout 2/2、待機約501ms|
|終わらない画像応答|約0.2秒で早期返却|gate timeout 2/2、全体約18.23秒|

最初の3ケースの取得・観測中央値は旧221.06ms、新780.52ms。旧版が約559ms「速い」のは未完成DOMで終了していたためで、正しい内容を同じ段階まで取得する性能の優位ではない。これは同じfixtureに限る因果結果であり、元の公開サイト16/40対14/40の差全体を説明したことにはならない。

前段階の`bridge-gate`（commit `4a223c5`、追加待機なし）でも0/6→6/6、終わらない画像は約18.21秒でtimeoutを確認。最終結果とは別ZIPに保存した。

## 待機の既定値・上限

追加待機は**既定で無効**。`ToolAdapter`の`tool:'4play'`に`options.readyCondition={selector:'#ready', text:'loaded', timeoutMs:3000}`を指定したページだけ、表示要素と任意の文字列を確認する。HTTP APIでは`ready_condition`。任意JSを受け取るAPIにはしていない。クリック・認証・CAPTCHA操作は行わない。

最大5000ms、100ms間隔、read RPCの停滞も待機deadlineで切り上げる。文書gateの最大18秒と追加待機には、既存observe時間・保存余裕を差し引く残予算を適用する。既存observeの既定値（直接HTTP API 1000ms、共通adapterの設定6000ms）は変更しない。load後に必ず固定sleepを増やす処理ではない。

`bridge_navigation.outcome`（complete/timeout/navigation_failed）と`ready_condition.outcome`（ready/timeout/not_run_navigation_incomplete）を保存する。HTTP200や`content_observed`とDOM条件達成は別である。

18秒は文書gateの上限で、全lifecycleの絶対上限ではない。既存のtab-open・capture等の4play RPC timeoutは今回変更していない。クライアント側25秒のtimeoutも維持する。今回の実ブラウザでの終わらないresourceと、unitでのstalled tab-list/selector readは、それぞれの待機上限で終了した。

## 検証・保存

- 回帰テスト: 初期blank、別tab/container/subresource、redirect最終URL、fragment、HTTP503、favicon失敗、真のnetwork failure、終わらないresource、停止したtab-list RPC、DOM条件ready/timeout/無指定、残予算を確認。
- 最新Python lab suite151件（15 skip）、Node25件、source package、Docker buildを確認。復元sourceでも151件成功（15 skip）、Docker demo/verifyもok:true。新helperのCOPY/権限設定を再現したDockerチェックで非rootのrequireを確認した（standalone bridge image全体の再buildとは別）。remote最終head CIも確認してからマージする。
- 全4fixture runのEvidence verify成功。完全checkpointは474 files / 1,446,067 bytes、SHA256 `18af52d4986f255c515b844e5b2e20459568a4fa3e431149ad018a614e2aa366`。
- [条件・結果・trace・完全ZIP](../../benchmarks/pr17-20261006/)。測定時刻は各JSONのUTCを参照。既存のベンチマーク群として20261006フォルダーを継続利用した。
- Dockerのbridge imageにもhelperをCOPYし、配布ZIPへ含める契約をテストした。資格情報とbrowser profileはcheckpointに含めない。

## 残る制約・次

先のBrave/Bingでの追加待機は抽出量を改善しておらず、その結果は[先行レポート](pr18-phase-and-wait-followup.md)に保持する。hybridとPlaywrightのgoto区間の細かな差も未解明。今回の修正を検知耐性向上や実サイト全般の改善と扱わない。

今回のblank誤認修正と任意の追加待機が完了したら、このタスクを終了する。別のCI保守や新技術調査は別タスクで扱う。
