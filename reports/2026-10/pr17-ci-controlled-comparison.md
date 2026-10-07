# PR17 CI修正と4play / Camoufox条件統一試験

## 結論

判定 **B（条件付き）**。Dockerのcheckpoint export失敗を修正し、実ブラウザのloopback比較を完了した。外部検索サービスの優劣は未判定。旧記録の16/40対14/40は版・時刻・待機・HTTPエラー処理が異なり、優劣の根拠にしない。

差の由来として**待機条件がDOM完成度と時間を変えること**を実証した。同じCamoufox、同じ生成済みfingerprint、同じPlaywright制御で、`DOMContentLoaded`だけを`load`へ変更すると遅延画像のonloadによるDOM更新が0/4→4/4になった。観測時間中央値は167.37→658.26ms。fixtureの500ms遅延と整合する。これはサイト検知耐性・検索品質の改善ではない。

アセンブル版は本報告ではPR17の`camoufox-fourplay` / hybrid（Camoufox本体＋camoufox-js設定生成＋web-ext起動＋4play拡張制御）を指す。hybridとCamoufox-loadはいずれもDOM完成4/4。両者の中央値95.84ms差は制御経路、拡張、タブ操作、proxy relay、証拠計装が混在し、単一原因に帰属できない。FirefoxとCamoufoxは本体版も違うためエンジン差の因果比較にはならない。

## 概要・CI修正

起点はPR17 head `8b9ec9481def6cfdc3d3cecdc9c383c29cb0c19d`、main `d58c93a`。他のopen PRは確認時PR17のみ。既存`work` checkoutを変更せず、別worktree・専用ブランチに分離した。提案先はPR17ブランチ宛てのdraft PR18。PR17自身のhead/CIは直接更新していない。

`export.py`は`.dockerignore`だけでなくREADME、AGENTS、Compose、説明書などを含む再開用source archiveを要求する。必須ファイルの欠落を無視せず、Docker **test stageのみ**に明示COPYした。runtimeへの不要な文書追加はない。回帰テストでは必須ソースの内容、証拠の保存、runtime-stateの除外、全manifest SHA256を照合する。

## 方法

- 測定コード: `experiments/bot-diagnostics/controlled-fourplay-comparison.mjs`。確定試験`run-02`のcode commitは`fbdac6d76aa19205f8fc5db007761d62afc8698f`。dirty表示は未追跡の`benchmarks/`のみ。測定中はソースを変更していない。
- **Cloud内のブラウザ**。Docker `--network none`、同一host・image・DISPLAY `:99` / Xvfb 1280×720×24。GPU/Kaggle未使用。外部サイト、認証、CAPTCHA操作は一切実施していない。
- fixture固定クエリ`fixture`、catalogは`/ok?q=fixture`（200）と`/error?q=fixture`（503）。両方に500ms遅延画像とDOM更新を置く。実検索サービスcatalogではない。
- 共通ToolAdapter/Evidenceを使用。navigation timeout 25,000ms、観測100ms。native4playのgateは`tabs.onUpdated status=complete`、Camoufox-loadは`load`。Camoufox-DCLだけ意図的にgateを変更するアブレーション。
- 4条件を順方向・逆方向に各1回、合計8ブラウザ起動×2ページ＝16観測。各起動で新規profile、同一起動の2ページは同一セッション。ウォームアップなし、各条件のn=4であり性能順位は出さない。前条件のOSキャッシュ効果は除去できていない。
- Camoufoxの生成configは1回だけ作成し、全Camoufox条件へ同じものを注入。fingerprint/フォントhash、prefs、argsを保存。従来Camoufoxの完全な既定設定比較ではなく、設定を揃えた比較である。
- Firefox native条件もnative adapter lane（識別子`camoufox-fourplay`）に注入する。**既存の`4play` HTTP bridgeの性能測定ではない**。arm名とruntime実体を併記し、混同しない。

## 構成差分（コード確認と実測）

|項目|既存4play bridge（未測定）|Firefox native4play|アセンブル版/hybrid|Camoufox-load / DCL|
|---|---|---|---|---|
|本体|既存Docker内Firefox|Firefox ESR 153.4.0|Camoufox 152.0.4-beta.30|同じCamoufox binary|
|制御|HTTP bridge→4play|worker→4play 1.2.5|同左|Playwright-core 1.60.0/Juggler|
|拡張|4play|同じ署名XPI 1.10の展開物|同左|4playなし|
|起動|entrypoint/Firefox|web-ext 8.9.0|同web-ext＋生成env/prefs|launchPersistentContext|
|起動引数|別entrypoint|web-ext付加のdebugger/profile等|同系統＋生成args（今回空）|Playwright付加のJuggler等|
|Camoufox設定|なし|なし|固定fingerprint、allowAddonNewtab、userContext有効|同じ生成fingerprint、allowAddonNewtab、dump有効|
|gate|100ms polling、内部deadline18秒|complete event|complete event|load / DOMContentLoaded|
|対象通信|container proxy|loopback専用relay|同左|loopback直結|
|外部通信|今回は未起動|Docker network none|同左|同左|
|viewport|別条件|実測なし|実測なし、生成window1280×720|runtimeで測定。完全同値とは主張しない|
|証拠能力|応答＋返却DOM|応答＋DOM、画面なし|同左|応答＋DOM、画面機能は今回無効|

バイナリ版差、viewport、設定・計装差は残る。ブラウザ本体版を完全に一致させたFirefox対Camoufox比較とは表現しない。上流本体のパッチ差が原因かは未確認。UAだけで実ブラウザ版を推定せず、package/binary metadataも保存した。

## 結果

|条件|200/503の応答・DOM取得|遅延DOM完成|取得・観測・marker読出し中央値|起動中央値|
|---|---:|---:|---:|---:|
|Firefox native4play|4/4|4/4|768.11ms|1811.20ms|
|hybrid|4/4|4/4|754.10ms|1987.63ms|
|Camoufox load|4/4|4/4|658.26ms|1599.53ms|
|Camoufox DOMContentLoaded|4/4|0/4|167.37ms|1675.96ms|

503でもHTTP statusを200へ変更せず、本文・DOMが保存された。移植後のHTTP拒否通知を即座にnavigation failureとしない処理は実fixtureの503で確認済み。403とsubresource 404は既存native回帰テストで確認済み。旧処理を戻す実ブラウザアブレーションは未実施であり、旧サイト差をこの修正だけで説明はしない。

## 検証・保存

- ローカルPython `test_lab*.py`: 151件、成功（任意依存15 skip）。公開source ZIPを展開した環境でも151件成功、15 skip。
- native/hybrid Node回帰: 9/9成功。Cloud package test: 1/1成功。
- Docker build成功。test stageのlab suite 151件成功（Node等未配備分41 skip）、cloud environment 7件成功。network none/read-onlyのdemoとverify成功（`ok:true`）。
- `run-02`: 全8 Evidence verify成功。checkpoint ZIPのCRC・全SHA256照合済み、712 files / 2,268,606 bytes、SHA256 `8ce25ddd2e7c6f279a1a89a4589c759f1930f9685423fe77ef59e47653ea98c4`。
- `[benchmarks/pr17-20261006](../../benchmarks/pr17-20261006/)`に数値、条件、fingerprint、環境lock、検証ログ、完全証拠ZIPを保存。ZIP内に各cellのledger、DOM/body blobs、ソースsnapshotあり。ブラウザprofile、秘密情報、依存バイナリは含めない。
- `run-01`は予備試験。Firefox nativeを誤ってHTTP bridge laneへ入れ`browser.navigate is not a function`が発生。修正後に新しいrun-02を作り、予備試験のZIPも別保存した。予備試験終盤のソースsnapshotは修正作業と重なったため、比較の結論には使用しない。

## 問題点・導入判断

fixtureの遅延DOM差は再現できたが、WAF通過率・検索品質・実サイトの順位付けは未測定。hybridはPlaywright制御を使わなくても設定生成の依存としてplaywright-coreを保持する。ロードの有無と制御の有無は別物。完全headers/redirect/画面などの証拠能力も同等でない。

新しい機能としてはHTTPエラー本文を保持するnative観測と、同一fixtureでgateを切り分けるコードに価値がある。既存4play bridgeの置換、Camoufoxの優位、商用運用への採用はまだ推薦しない。これは新技術発見調査ではなく既存成果の追加検証のため、stars/コミュニティ/ライセンスの再調査は今回の範囲外（未更新）。

## 次

1. PR18のsource契約修正をレビューし、必要ならPR17へ取り込む。自動マージ・main push・デプロイはしない。
2. hybridとCamoufox-loadの制御経路差をさらに分ける場合、同じbinary・設定・viewport・タブ寿命・loopback relayを統一し、計装ON/OFFなど一要素ずつ変える。
3. 実サイト再比較はこのfixture結果と分離し、固定catalog/クエリ、時刻ブロック、交互順、同一観測条件を事前固定して小規模に行う。今回の16/16を公開サイトの通過率として扱わない。
