# Joshin 検索ページ送り診断

検索語は「グローブ」。Camoufoxとnative 4playを同じprofile/tabで使い、観測済みのページ送りanchorをクリックし、DOMの`MAX_PAGE`に従ってページ送りを実行しました。ライブはproxyなし、日本語/JST fingerprint、TLS検証有効です。

## ライブ観測

検索ページHTTP status: 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 200。
成功ページ数: 84。最後の試行ページ: 84 (HTTP 200, 表示ページ 84)。pagination_complete=True。DOMのMAX_PAGE=84、終了状態=`last_page_reached_and_reported_total_matched`。
商品行: 3343、ユニークURL: 3343、ページ送りクリック: 83。経過: 372.39秒。

ページ別status、範囲、件数、時刻は`summary.json`に保存しました。Cookie値は保存していません。tab/containerとrequest cookie名の観測は同じブラウザ文脈の継続を示しますが、cookie値やサーバ側session内容の一致までは確認していません。
ページ送りは観測済みanchorへのelement.click()によるDOM操作で、click eventのisTrustedはfalseです。人間の操作との同等性や今後拒否されないことは示しません。操作間の人工delayは追加せず、homepageの6秒待機とdocument response/complete待ちを使いました。

## Fixture確認

| 条件 | HTTP | 成功ページ | 商品行/unique | click | 終了 |
|---|---|---:|---:|---:|---|
| fixture-complete-recheck-final | 200,200,200 | 3 | 6/6 | 2 | `last_page_reached_and_reported_total_matched` |
| fixture-denied | 200,403 | 1 | 2/2 | 1 | `http_403_on_page_2` |
| fixture-no-progress | 200,200 | 1 | 2/2 | 1 | `page_number_did_not_advance` |
| fixture-duplicate | 200,200,200 | 3 | 6/5 | 2 | `last_page_reached_and_reported_total_matched` |

JANを示す確定フィールドは観測されていません。URL末尾13桁は`product_code_candidate`として保持し、JANとは断定していません。価格は元の文字列でCSV/JSONに保存しました。

`fixture-complete-recheck-final`を最終fixtureとして採用しました。旧`fixture-complete`と旧`fixture-complete-recheck`は履歴としてexportに保持し、最終判定には使いません。
