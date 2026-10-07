# Joshin Camoufox pagination run

検索語「グローブ」をCamoufox単独（Playwright制御、headful）で実行しました。条件はproxyなし、TLS検証有効、日本語/JST fingerprintです。

## ライブ結果

HTTP status: 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200。成功ページ 84、最後の試行 84（HTTP 200、表示 84）、完全性 `True`、終了 `last_page_reached_and_reported_total_matched`。
DOM総数 3343、商品行 3343、ユニークURL 3343、クリック 83。JA/JST実測: navigator=ja-JP, Intl=ja-JP, timezone=Asia/Tokyo, offset=-540分; 観測ページ=84, 全件一致=True。

初回liveは 30ページ/1200商品行を取得後、`experiment_error`（page.evaluate: Execution context was destroyed, most likely because of a navigation）で終了しました。HTTP 403観測=False。これはruntime/control errorとして保持し、WAF拒否とは分類していません。最終結果にはlive-recheckを採用しました。

検索request開始間隔（started_at連続差、秒）:

| 条件 | 間隔数 | 平均 | 中央値 | 最短 | 最長 | p90 |
|---|---:|---:|---:|---:|---:|---:|
| Camoufox | 83 | 3.619 | 3.657 | 2.637 | 5.082 | 4.76 |
| assembled | 83 | 4.132 | 4.032 | 1.826 | 8.156 | 5.036 |

p90は線形補間（position=0.9×(n−1)）。document観測差とdocument→click／click→clickの統計はsummary.jsonに記録しました。
旧runの比較元: `lab-runs/joshin-assembled-pagination-20261008-0005/live/measurement.json` と `ledger.json`。
旧assembled観測は 3343件/84ページ/83クリック。homepage settle 6秒とready polling 150msは設定上の待機で、人工クリック間delayとは別です。DOM clickの`isTrusted`はfalseです。

この速度比較は別条件・各run一回の観測で、差の原因や今後の成功を示すものではありません。
Cookie値は保存していません。Cookie名のみ記録し、Playwrightのtab/container IDは対象外です。ページ文書のtop-level frame属性はledgerの観測として記載しています。

## Fixture確認

| 条件 | HTTP | 成功ページ | 商品行/unique | click | 終了 |
|---|---|---:|---:|---:|---|
| fixture-complete-recheck | 200,200,200 | 3 | 6/6 | 2 | `last_page_reached_and_reported_total_matched` |
| fixture-denied-recheck | 200,403 | 1 | 2/2 | 1 | `http_403_on_page_2` |
| fixture-no-progress-recheck | 200,200 | 1 | 2/2 | 1 | `page_number_did_not_advance` |
| fixture-duplicate-recheck | 200,200,200 | 3 | 6/5 | 2 | `last_page_reached_and_reported_total_matched` |

初回4 fixtureは履歴として保持し、suffix `-recheck` の4 fixtureを最終確認に採用しました。
