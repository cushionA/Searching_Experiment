# 検索SERPページ巡回

検索候補URLを収集したrunです。候補の関連性、検索精度、公式性は評価していません。公式件数は未採点のため0として記録しています。

- run: `search-pagination-tokyo-20261004`
- queries: ["東京都大田区 池上本門寺 松濤園 公開日", "早稲田大学 教員", "気象庁 台風情報", "国立国会図書館 デジタルコレクション", "Python 公式ドキュメント"]
- provider目標/上限: 100 unique URL
- 全サイトの観測候補URL上限/重複除く観測数: 500 / 397
- 各サイト100件まで採用したURLは計523レコード。サイト間の重複を除く採用URLは357件。最後のページで目標を超えて表示された40 URLは観測のみ。`unique-results.csv`のselected列で区別。
- requested display maximum: maximum_available; no configurable count observed in prior DOM（設定probe未確認。実測最大数への到達を示さない）
- providerごとの検索結果表示数設定probe: 未確認。requested paramと各ページの実測件数は別記し、最大到達とは判定しない。
- DDGの114件はMore Results後の累積DOMカード数（初回10件、累積114件）。単一応答件数やサービスの最大設定ではなく、accepted URLはprovider上限100件で集計。
- browser page navigations: 56; Google result-link resolver GET attempts: 107（ページ遷移とは別の追加GET。1秒target/QPSには含めない）
- target interval: 1000 ms（start-to-start）
- same-provider consecutive next-page start intervals (same query_index and evidence run): n=37, median=5779.0 ms, mean=5829.9 ms, min=1107 ms, max=24289 ms
- excluded adjacency pairs: query changes=3, same-query page gaps=0; initial visits and cross-run session boundaries are not next-page intervals.
- page elapsed: n=56, mean=4753.1 ms, max=24221 ms
- lateness against schedule: n=56, mean=3949.9 ms, max=23289 ms
- pages: 56 / 500; base global stop: all_sites_exhausted; continuation global stop: なし（base完了状態を維持）; Brave continuation stop: results_per_site_target

|provider|pages|provider unique URL|HTTP status observed|navigation outcome|Next URL|DOM change|URL increase|next-page interval median (n)|display setting (requested; unverified)|max observed items/page|stop reason|
|---|---:|---:|---|---|---|---|---|---|---|---:|---|
|google|12|100|200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200|12/12 navigation without recorded execution/403/429 failure; content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed|10/12|11/11|12/12|11040.0 ms (n=8)|maximum_available; no configurable count observed in prior DOM|10|results_per_site_target|
|bing|12|100|200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200|12/12 navigation without recorded execution/403/429 failure; content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed|12/12|11/11|12/12|6805.5 ms (n=10)|maximum_available; no configurable count observed in prior DOM|10|results_per_site_target|
|brave|6|100|200, 200, 200, 200, 200, 200|6/6 navigation without recorded execution/403/429 failure; content_observed, content_observed, content_observed, content_observed, content_observed, content_observed|5/6|4/5|6/6|2756.5 ms (n=4)|maximum_available; no configurable count observed in prior DOM|20|results_per_site_target|
|yandex|3|23|200, 200, 200|3/3 navigation without recorded execution/403/429 failure; content_observed, content_observed, content_observed|2/3|2/2|2/3|24289.0 ms (n=1)|maximum_available; no configurable count observed in prior DOM|14|challenge|
|duckduckgo|8|100|200, 200, 200, 200, 200, 200, 200, 200|8/8 navigation without recorded execution/403/429 failure; content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed|8/8|7/7|8/8|1220.0 ms (n=6)|maximum_available; no configurable count observed in prior DOM|114*|results_per_site_target|
|startpage|1|0|200|1/1 navigation without recorded execution/403/429 failure; content_observed|0/1|0/0|0/1|— (n=0)|maximum_available; no configurable count observed in prior DOM|0|verification_gate|
|qwant|1|0|200|1/1 navigation without recorded execution/403/429 failure; content_observed|0/1|0/0|0/1|— (n=0)|maximum_available; no configurable count observed in prior DOM|0|country_unavailable|
|ecosia|1|0|403|0/1 navigation without recorded execution/403/429 failure; challenge_observed|0/1|0/0|0/1|— (n=0)|maximum_available; no configurable count observed in prior DOM|0|http_403|
|yahoo-japan|11|100|200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200|11/11 navigation without recorded execution/403/429 failure; content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed, content_observed|10/11|10/10|11/11|1875.5 ms (n=8)|maximum_available; no configurable count observed in prior DOM|11|results_per_site_target|
|kagi|1|0|200|1/1 navigation without recorded execution/403/429 failure; content_observed|0/1|0/0|0/1|— (n=0)|maximum_available; no configurable count observed in prior DOM|0|auth_required|

## Continuation留保

Braveのbase runは`blocked`判定を、SERP breadcrumb URL内の数字403で生じたfalse positiveとして補正しています。 記録された根拠: 正常SERP本文のbreadcrumb URL内の数字403を拒否表示と誤判定。HTTP 200、20候補、次ページあり。
Brave continuationは新しいbrowser sessionで同一IPを継続使用した観測です。baseとの間隔は同一session内の次ページ間隔に含めず、IP独立の再現観測として扱いません。
