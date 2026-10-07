# Hybrid方式の検索・ブロッキング小規模検証

Hybridの検索対象5件と、同じ5件の4play/Camoufox既存観測を並べています。50サイトcatalogを分母に含めていません。

検索クエリ確認: 4play 5/5、Camoufox 3/5、Hybrid 3/5。
blocking全targetがHTTP 200で完走した件数: 4play 1/4、Camoufox 2/4、Hybrid 2/4。
この単発では検知耐性改善は確認できず。

## 検索5件

|方式|サイト|HTTP/outcome|候補数|query confirmed|cell完走|elapsed (ms)|PSS peak (MiB)|
|---|---|---|---:|---|---|---:|---:|
|4play|bing|200 / content_observed|10|True|True|72204.0|1203.4|
|4play|brave|200 / content_observed|10|True|True|53569.0|975.8|
|4play|ecosia|200 / content_observed|10|True|True|45941.0|1002.6|
|4play|perplexity|200 / content_observed|7|True|True|80687.0|1505.3|
|4play|google|200 / content_observed|2|True|True|36734.0|1015.2|
|camoufox|bing|200 / content_observed|10|True|True|56862.0|1032.7|
|camoufox|brave|200 / content_observed|10|True|True|42457.0|763.7|
|camoufox|ecosia|— / 未実行（recovery_budget_exhausted）|0|False|False|30769.0|970.9|
|camoufox|perplexity|— / 未実行（recovery_budget_exhausted）|0|False|False|32029.0|938.4|
|camoufox|google|200 / content_observed|2|True|True|40465.0|862.9|
|hybrid|bing|200 / content_observed|10|True|True|97933.0|1038.8|
|hybrid|brave|200 / content_observed|10|True|True|53106.0|748.4|
|hybrid|ecosia|— / 未実行（recovery_budget_exhausted）|0|False|False|36036.0|798.5|
|hybrid|perplexity|— / 未実行（recovery_budget_exhausted）|0|False|False|37470.0|788.0|
|hybrid|google|200 / content_observed|2|True|True|48429.0|801.1|

## 完走・検索確認済みcellの同一site elapsed差

|比較|paired sites|右−左 elapsed中央値 (ms)|
|---|---:|---:|
|camoufox - 4play|3|-11112.0|
|hybrid - 4play|3|11695.0|
|hybrid - camoufox|3|10649.0|

## 対象subsetのprocess-tree PSS peak

|方式|計測site数|site別PSS peak中央値 (MiB)|
|---|---:|---:|
|4play|5/5|1015.2|
|camoufox|5/5|938.4|
|hybrid|5/5|798.5|

## Blocking 4件

|方式|site|分類|pipeline state|完走|homepage status/outcome|target status/outcome|elapsed (ms)|
|---|---|---|---|---|---|---|---:|
|4play|amazon|no_block_observed_for_requested_pages|navigation_completed|True|200 content_observed|200 content_observed; 200 content_observed|79468.0|
|4play|joshin|access_denied_observed|site_rejected|False|403 access_denied_observed|未到達|38603.0|
|4play|homes|partial_observation|cell_timeout|False|200 content_observed|200 content_observed|120718.0|
|4play|indeed|challenge_observed|recovery_budget_exhausted|False|403 challenge_observed|未到達|30632.0|
|camoufox|amazon|no_block_observed_for_requested_pages|navigation_completed|True|200 content_observed|200 content_observed; 200 content_observed|70033.0|
|camoufox|joshin|access_denied_observed|site_rejected|False|403 access_denied_observed|未到達|30028.0|
|camoufox|homes|no_block_observed_for_requested_pages|navigation_completed|True|200 content_observed|200 content_observed; 200 content_observed|107115.0|
|camoufox|indeed|challenge_observed|recovery_budget_exhausted|False|403 challenge_observed|未到達|25905.0|
|hybrid|amazon|no_block_observed_for_requested_pages|navigation_completed|True|200 content_observed|200 content_observed; 200 content_observed|73777.0|
|hybrid|joshin|access_denied_observed|site_rejected|False|403 access_denied_observed|未到達|30972.0|
|hybrid|homes|no_block_observed_for_requested_pages|navigation_completed|True|200 content_observed|200 content_observed; 200 content_observed|117024.0|
|hybrid|indeed|challenge_observed|recovery_budget_exhausted|False|403 challenge_observed|未到達|32968.0|

## 解釈上の制約

- 候補は保存DOMの機械的な語一致抽出です。候補語一致は在籍教員探索への意味適合や検索正解率を保証しません。
- 検索・blockingの完走はtimeoutなし、pipeline `navigation_completed`、manifest target全件の証拠記録が揃う場合に限ります。途中観測を未ブロック/成功に数えません。
- elapsedはブラウザ待機・制御方式の差を含むend-to-endで、4play tabs.onUpdatedとCamoufox DOMContentLoaded、およびhybridの拡張経由制御の違いがあります。応答時間単独の比較ではありません。
- メモリはsubset内のsite別process-tree PSS peak中央値。対象サービス、source/config、runtime/binary、サンプル期間に依存し、全50サイトや純粋なエンジン差へ一般化できません。
- 4playのメモリには全response body bufferをbase64化してIPC転送する実装負荷を含みます。
- 50件比較の4play baselineはHTTP statusLineをdom_load_failとして早期終了させる修正前観測です。保存status/DOMがある場合その観測は残しますが、タイミング・完走・未確認分類は影響を否定できません。baseline caveatは既存の../summaries/baseline-measurement-caveat.jsonを参照してください。
- hybridは修正後runtimeを使い、allowAddonNewtab=true、元4play拡張bg.js無変更、Camoufox 0.12.0 / Firefox 152.0.4-beta.30で測定します。baselineとはruntime実装差を含みます。
- 同一ホスト上の並行実行によるcontentionは各run conditionsに記録された範囲で考慮します。
- 各サービスは1 query・1 attemptの単発です。block/challenge分類は保存されたstatus・DOMに基づく観測で、原因の断定ではありません。

## 検知器fixture

|実行|検知器|状態|red_flags|未評価|botd.result|
|---|---|---|---|---|---|
|search-4play|rebrowser|detector_partial|なし|dummyFn, sourceUrlLeak, mainWorldExecution, exposeFunctionLeak|—|
|search-4play|botd|detector_completed|なし|なし|{"bot": false}|
|search-camoufox|rebrowser|detector_partial|なし|dummyFn, sourceUrlLeak, mainWorldExecution, exposeFunctionLeak|—|
|search-camoufox|botd|detector_completed|なし|なし|{"bot": false}|
|search-hybrid|rebrowser|detector_partial|なし|dummyFn, sourceUrlLeak, mainWorldExecution, exposeFunctionLeak|—|
|search-hybrid|botd|detector_completed|なし|なし|{"bot": false}|
|blocking-4play|rebrowser|detector_partial|なし|dummyFn, sourceUrlLeak, mainWorldExecution, exposeFunctionLeak|—|
|blocking-4play|botd|detector_completed|なし|なし|{"bot": false}|
|blocking-camoufox|rebrowser|detector_partial|なし|dummyFn, sourceUrlLeak, mainWorldExecution, exposeFunctionLeak|—|
|blocking-camoufox|botd|detector_completed|なし|なし|{"bot": false}|
|blocking-hybrid|rebrowser|detector_partial|なし|dummyFn, sourceUrlLeak, mainWorldExecution, exposeFunctionLeak|—|
|blocking-hybrid|botd|detector_completed|なし|なし|{"bot": false}|
