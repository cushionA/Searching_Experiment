# Joshin Patchright smoke

direct・headfulのPatchrightを日本語/JSTと英語/JSTで比較しました。各runは新しいbrowser sessionで、Cookie値は保存していません。

| run | status | 成功ページ | 商品行/unique | click | complete | stop |
|---|---|---:|---:|---:|---|---|
| fixture-ja-complete | 200,200,200 | 3 | 6/6 | 2 | True | `last_page_reached_and_reported_total_matched` |
| fixture-ja-denied | 200,403 | 1 | 2/2 | 1 | False | `http_403_on_page_2` |
| fixture-en-complete | 200 | 1 | 2/2 | 0 | False | `requested_page_limit_reached` |
| live-ja | 404 | 0 | 未確定/未確定 | 0 | False | `search_http_404` |
| live-en | 403 | 0 | 未確定/未確定 | 0 | False | `http_403_on_first_search_page` |

両liveのhomepageはHTTP 200。JA検索はHTTP 404でtitle「Joshin web | 家電とパソコンの大型専門店」、本文「サーバーが混み合っております。しばらく経ってからもう一度お試しください。」。EN検索はHTTP 403でAccess Denied/permission本文でした。両方で入力「グローブ」、action `/srhzs.html`、GET、Shift_JIS、accept_charset空を記録しました。表示文とstatusを報告し、原因は断定しません。
両条件はdirect/no proxyで同じ設定を使い、IP addressは測定していません。未確定件数は0に置き換えていません。
環境スナップショット、top-level frame、tab/container null、document timeOrigin、request開始間隔、実runtime詳細はsummary.jsonに保存しました。
過去観測はdirect English 403／direct Japanese 200／proxy Japanese 403で、出典run名とファイルをsummaryに記録しました。この比較だけでIPだけが原因とは断定できません。
live-ja/live-enはそれぞれ3ページ/1ページ上限のsmoke testです。上限到達はカタログ全件取得を意味しません。
