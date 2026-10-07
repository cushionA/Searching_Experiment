# ジョーシン「グローブ」検索の通常版・アセンブル版比較（2026-10-07）

通常4playとCamoufox + 4playの両方で、トップは200、検索結果の1ページ目は403だった。取得できた製品名は両方0件。商品一覧とページ送りリンクが出なかったため、2ページ目以降には進めていない。検索結果が0件だったという意味ではなく、一覧へのアクセスが拒否された。

| 条件 | トップ | 検索1ページ目 | 取得した結果ページ | 取得製品名 | 停止理由 |
|---|---:|---:|---:|---:|---|
| 通常版：Firefox ESR + 4play | 200 | 403 | 0 | 0 | Access Denied |
| アセンブル版：Camoufox + 4play | 200 | 403 | 0 | 0 | Access Denied |

## 操作と待機

最新mainとして取得済みのcommitは `3a197da67cac7103f659c3c00833b1142a64c8df`。前回の遷移修正を含む作業ツリーを使った。両方式とも同じnative 4play WebExtension controllerで、新規プロファイル・Cookieコンテナから `https://joshinweb.jp/top.html` を開いた。トップのHTTP応答とドキュメント完了を待ち、6秒経過後、実在する `#suggest_input` に「グローブ」を入力し、フォーム内の `changeSubmit()` を呼ぶ検索アイコンのアンカーをDOM clickした。同じタブ・コンテナを維持した。

ページ側が生成した検索URLは両方とも次のとおり。手作業で検索パラメータやページ番号を組み立てていない。

```text
https://joshinweb.jp/srhzs.html?KEYWORD=&KEY=ZS_ALL&KEY_M=ALL&QS=&QK=%83O%83%8D%81%5B%83u&category_id=&REQUEST_CODE=1
```

`QK` はドキュメントのShift_JISで符号化された「グローブ」。遷移後はURLの変化、対応する実HTTP main-document応答、変更後ドキュメントのcompleteを照合した。通常版・アセンブル版とも検索GETは403、タイトルは `Access Denied`、本文は `You don't have permission to access joshinweb.jp on this server.`。応答本文309バイトとDOMを保存した。

探索用の待機追加なしの検索アイコン操作でも両方式ともトップ200・検索403だった。通常版ではフォームの `requestSubmit()` も同じ検索URLで403。トップ完了後に6秒待った正式比較でも結果は変わらなかった。ホイール操作は実行していない。

## 解釈と限界

今回止まったのは商品名抽出やページ送りより前の検索応答。製品総数、最終ページ、2ページ目以降の通過可否は不明。成功した結果DOMがないため、商品カードやNextのセレクタは推測で採用していない。認識できない200のDOMを「商品0件」や「最終ページ」と扱わない。

この環境の外部通信はCloudの管理プロキシとCAを継承し、TLS検証を有効にした。ユーザーが言及したローカル・プロキシなしのCamoufox成功とは条件が異なる。双方の出口IPの一致は未確認。4playは応答ヘッダーを提供せず、Akamai側の判定ルール・スコアは取得していないため、403の内部原因を断定できない。記録された検索リクエストは `Sec-Fetch-Site: same-origin` で、`Sec-Fetch-User` はなかった。DOM fill/clickのイベントは物理入力と同等とは確認していない。

## 証拠と再実行

[証拠・チェックポイント](../../benchmarks/joshin-glove-20261007/) に、両方式の台帳、HTTP本文、DOM、検索操作、抽出観測、製品名JSON/CSV、環境条件、実行時ソース、検証結果を保存した。製品名ファイルは空で、カタログ0件の証拠とは扱わない。通常版・アセンブル版とも台帳2応答、DOM2件のハッシュ・会計検証に合格した。未知レイアウトを成功扱いしないNodeテスト2件、および必須labテスト151件（skip15）が通過した。

`runs/*/sources.json` とblobが実行時のソース。測定後に、未知レイアウトの件数をnullにする処理と同期extractorを整えたが、観測された403の分岐は変えていない。現行再実行ソースはcheckpointの `replay-source/` に含める。raw runは上書きしていない。

ブラウザ・拡張・依存と `search-fourplay:local` imageが配備済みの同じ環境では、次のコマンドで新しいrunを作れる。

```sh
python3 -B benchmarks/joshin-glove-20261007/run-browser.py standard NEW_STANDARD_RUN experiments/bot-diagnostics/joshin-product-search.mjs 6000
python3 -B benchmarks/joshin-glove-20261007/run-browser.py assembled NEW_ASSEMBLED_RUN experiments/bot-diagnostics/joshin-product-search.mjs 6000
```

この比較器は現時点で確認できた検索拒否を保存する。成功した結果DOMが得られた場合はunknownで停止し、その実物から商品・ページ送りのセレクタを検証してから続ける。
