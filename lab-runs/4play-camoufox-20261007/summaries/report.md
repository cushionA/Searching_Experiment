# 4play / Camoufox 実測比較

観測検索語: `早稲田大学 教員`。固定50対象のうち検索ルート40件、homepage-only 10件。

検索結果は保存DOMからクエリ関連語に一致する候補リンク、または画面上の明示的0件表示を確認した場合だけ成功として数える。HTTP 200のみでは成功にしない。キーワード抽出は意味的な関連性を保証しない。OneSearch/Yahoo Search、Peekier/Kagiは別サービスとして分離する。

## 検索結果

|方式|確認済みquery|関連候補があったsite|候補URL合計（抽出）|成功query内候補URL|明示0件|Challenge|別サービス|未測定|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|4play|16|13|119|119|3|3|2|0|
|camoufox|14|11|98|98|3|3|2|0|

## 速度・メモリ

同一site・両方式で検索応答を確認したpairは 14 件。Camoufox−4playのcell所要時間差の中央値は -3800.5 ms、所要時間比の中央値は 0.9 倍 です。成功pairだけの単発観測です。待機条件が異なるため、性能優劣の断定には使えません。

|方式|対象site|PSS計測site|site別PSS中央値の中央値 (MiB)|site別PSS peak中央値 (MiB)|最大site PSS peak (MiB)|RSS計測site|site別RSS中央値の中央値 (MiB)|
|---|---:|---:|---:|---:|---:|---:|---:|
|4play|50|50|101.2 MiB|985.6 MiB|1532.0 MiB|50|170.3 MiB|
|camoufox|50|50|272.1 MiB|773.0 MiB|1340.1 MiB|50|321.9 MiB|

## ブロッキング・検知器

セル完走はtimeoutなし・pipeline `navigation_completed`・manifest記載target全件の証拠記録が揃った場合だけです。途中で観測したhome/target証拠は個別に残し、未到達targetがあるセルは「未ブロック」に数えません。

4play: 完走 1/4、部分観測 3
camoufox: 完走 2/4、部分観測 2

|方式|site|観測分類|pipeline state|homepage HTTP/outcome|target HTTP/outcome|elapsed|
|---|---|---|---|---|---|---:|
|4play|amazon|no_block_observed_for_requested_pages (target)|navigation_completed|200 content_observed|200 content_observed; 200 content_observed|79.5 s|
|4play|joshin|access_denied_observed (homepage)|site_rejected|403 access_denied_observed|未到達|38.6 s|
|4play|homes|partial_observation (部分観測)|cell_timeout|200 content_observed|200 content_observed|120.7 s|
|4play|indeed|challenge_observed (homepage)|recovery_budget_exhausted|403 challenge_observed|未到達|30.6 s|
|camoufox|amazon|no_block_observed_for_requested_pages (target)|navigation_completed|200 content_observed|200 content_observed; 200 content_observed|70.0 s|
|camoufox|joshin|access_denied_observed (homepage)|site_rejected|403 access_denied_observed|未到達|30.0 s|
|camoufox|homes|no_block_observed_for_requested_pages (target)|navigation_completed|200 content_observed|200 content_observed; 200 content_observed|107.1 s|
|camoufox|indeed|challenge_observed (homepage)|recovery_budget_exhausted|403 challenge_observed|未到達|25.9 s|

|方式|検知器|状態|赤旗|未評価|証拠時刻 (UTC)|
|---|---|---|---|---|---|
|4play|rebrowser|detector_partial|なし|dummyFn, sourceUrlLeak, mainWorldExecution, exposeFunctionLeak|2026-10-06T16:16:25.783410+00:00|
|4play|botd|detector_completed|なし|なし|2026-10-06T16:16:25.783410+00:00|
|camoufox|rebrowser|detector_partial|なし|dummyFn, sourceUrlLeak, mainWorldExecution, exposeFunctionLeak|2026-10-06T15:59:48.063172+00:00|
|camoufox|botd|detector_completed|なし|なし|2026-10-06T15:59:48.063172+00:00|

Challengeと403/429は別分類し、未実測を成功/失敗に混ぜない。詳細JSONには全cellのelapsed、process-tree PSS/RSS、detectorの未評価項目を保存する。

## 測定上の制約

- 検索応答成功はHTTP 200だけでは判定せず、保存DOMからクエリ語関連候補または明示的0件表示が必要。
- 候補語一致リンクは機械的抽出で、意味的関連性や正解率の評価ではない。Luna監査ではBaidu学生交流、Naver AI source carousel/図書館、Swisscows動画、Yahoo教員免許Q&Aなど、早稲田の在籍教員情報に直接関連しない候補も混在し、candidate precisionは未保証。
- OneSearchからYahoo Search、PeekierからKagiへ遷移した結果はalternate_serviceとして記録し、元サービスの結果に算入しない。
- レイテンシは全cell_elapsed_msを個別保存し、検索確認に成功した同一site pairだけを比較。homepage-onlyは検索成功分母に入れない。
- cell elapsedはworker/Node起動からhomepage/query遷移、証拠保存/verifyまでのend-to-end。4playはtabs.onUpdated complete、CamoufoxはDOMContentLoaded待ちで条件が異なるため、ブラウザ応答時間単独の公平比較ではない。
- メモリはworker/Nodeと観測した子孫プロセスのprocess-tree集計で、セル起動から終了までのサンプルを対象にする。
- 全期間PSS中央値はworker起動前後のNode/helperのみの低い値を含み、ブラウザ常駐時のメモリ消費量とは解釈しない。PSSピークのsite別中央値も併記し、対象catalog母数は50site。
- 同じプロキシ条件は対象タブ通信（GCP tunnel）に関する比較で、Firefox/extension背景通信全体を同じ経路に保証しない。未登録containerのupstream fallbackはdirectだが、背景通信自体は今回未観測。
- Process-tree RSSは共有ページを複数process分加算する可能性がある。PSSは共有メモリを比例配分した値。
- 方式ごとのrunは別時間帯に逐次実行しており、サイト側の変動や順序効果を除去していない。
- 各検索サービス1 query・1 attemptの単発観測。検索順位の網羅性、長期安定性、サイト群全体への一般化は評価しない。

このrunでは対象マスター・クエリ・画面・観測待ち・retry条件とtool buildをconditions JSONに固定し、過去観測や外部報告を採用していない。

4playの観測用拡張は画像・フォント等の本文もバッファし、base64で制御WebSocketへ送るため、メモリと処理時間にはこの証拠取得の負荷が含まれる。

`4play` の検索50セルは、HTTP statusLine通知も `dom_load_fail` にして早期終了する修正前runtimeで取得しました。`results.json` ではnavigation_error 20件中13件にHTTP statusLine（401×3、403×4、404×5、500×1）があり、対応native ledgerでHTTP 4xx/5xx応答を確認できたのは6件です。status/DOMが記録されている場合はその観測を保持しますが、待ち時間・完走・未確認分類に対する早期打切りの影響は否定できません。比較記録は修正前baselineとして維持し、修正後runtimeで測定したhybridは実装差も含むため、同一条件の方式比較とは扱いません。件別照合は [baseline-measurement-caveat.json](baseline-measurement-caveat.json) に記録しています。

## 直接リンク実験

Google検索画面を開かず、保存済みGoogle redirect URLを4playで直接開いたところ、最終URLはIndeed東京都板橋区の求人ページ、HTTP 403で `challenge_observed`（Additional Verification Required / Turnstile marker）でした。これは検索50件・ブロッキング4siteの比較集計には含めない単発の直接遷移観測です。画面本文とDOM hashは [direct-link-summary.json](direct-link-summary.json) と `4play-direct-link/results.json` に保存しています。4playはresponse headersを提供せず、challenge原因の断定やheader-level検証はしていません。
