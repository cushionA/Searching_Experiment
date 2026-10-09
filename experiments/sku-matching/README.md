# au・楽天のSKU突合検証

作業ブランチ: `codex/sku-matching-cloud-20261009`。これは独立した検証用CLIで、既存の本番クローラーへの接続は含まない。

商品名＋SKU文字列を使うCPU小型モデルと、仕様を正規化して照合する処理を比較した。MiniLMはクラウドCPUで動作したが、商品名を足しても寸法・色・レース有無の違いを確実に区別できなかった。最終CSVには、既に対応候補として選んだ商品群の中で、必要属性が一致し、販売状態と単一価格を確認できた行だけを残す。モデルの類似度は確認候補の順位付けに用いる。

## データと粒度

| 実データ | 商品数 | SKU行数 | 観測した構造 |
| --- | ---: | ---: | --- |
| au WEIMALL | 4 | 360 | カラー×サイズ、レース有無が別商品URL |
| au ニッセン Select10 | 8 | 80 | サイズ×色、各サイズが別商品URL |
| 楽天 WEIMALL | 20 | 891 | 1〜3軸。ct0はサイズ×カラー×レース有無 |
| 合計 | 32 | 1,361 | 取得日時・URL・元レスポンスSHA256を保存 |

auは商品詳細API・購入オプションAPI・店舗内検索APIを使用。店舗内検索は `user` に店舗IDを指定し、CP932で検索語をエンコードし、応答の `condition.shopId` / `condition.keyword` を確認した。楽天は保存した商品HTMLの `script#item-page-app-data` をEUC-JPとして読み、SKUごとの選択値・価格・在庫参照を抽出した。楽天の追加19ページは保存済み起点ページにある同一店舗の直接商品URLリストから取得した。この収集は `jse.lab` のgrounding比較実験ではない。

商品表 `products.jsonl` は商品ID・商品名・SKU軸・価格の粒度・購入オプション・取得根拠を持つ。SKU表 `skus.jsonl` は商品IDを参照し、選択値と在庫の原値を持つ。auの `skuInfo.skuId` は表全体のIDなので、突合用の個別IDには商品IDと行・列座標を使う。サイトごとの軸名・順序を保存し、サイズが必ず第1軸であるとは仮定しない。

楽天価格はSKUごとの `taxIncludedPrice`。auの今回のAPI価格は商品単位の `currentPrice` であり、SKU選択ごとに別途観測した価格ではない。auには配送地域の有料オプションもある。比較CSVは本体価格の粒度を `*_price_basis` 列で明示し、送料・地域加算・クーポン・会員条件を含む支払総額とは扱わない。

楽天の20商品は `HIDDEN_STOCK` を設定していた。保存データでは、画面の在庫表示、埋め込みJSONの在庫数量、そこから導いた販売状態を分けた。auの在庫数 `remainingStock=null` は補完せず、`isSoldOut` 等の原値を保存した。

## 正規化と最終除外

`match_skus.py` の対象は今回のカーテン形式。幅・丈・色・レース有無・合計枚数を比較する。全角・空白・幅/丈の接頭辞は正規化するが、枚数やレース有無を捨てない。商品名はモデル入力に利用し、商品名だけから各SKUの枚数を決めない。「4枚組＝レースあり」の一律変換も行わない。

判定は `matched` / `unmatched` / `review` / `excluded`。欠損・未知オプション・重複対応・範囲価格・販売状態不明は出力を保留し、売り切れは除外する。一致した行のみ `matched.csv` に出力し、判定理由を `audit.jsonl` に残す。取得データを物理削除しない。価格の近さでSKUを選ばない。カートへの操作は行わない。

実データの属性突合結果:

| 比較するau商品 | 入力au SKU | 入力楽天 SKU | CSV行数 |
| --- | ---: | ---: | ---: |
| 元URL 704502086のみ | 153 | 306 | 142 |
| 元URL＋レースなしURL 704500131 | 306 | 306 | 287 |

商品構成の継承は、対象商品の説明表と選択値を根拠に `prepare_real_pair.py` で行った。楽天ct0とau商品の独立した同一商品goldを作ったものではなく、ユーザー指定の商品候補内での属性一致検証である。生地・加工などを含む商品同一性は別工程で確認する。資料34〜36ページの顧客向け案1〜3は未決であり、別URLを含む結果は案3を試した検証として扱う。

Select10の `N・幅100×長さ110cm×4枚` 等や、今回のカーテン以外の型は収集データに残しているが、このCLIの汎用対応を実証したことにはしない。モデル選定・追加正規化のサンプルとして使う。

## CPUモデルの実測

`Xenova/paraphrase-multilingual-MiniLM-L12-v2` の量子化ONNXを固定revision・SHA256で利用。モデル約118MB、384次元、CPU 2 threads、最大128トークン、バッチ32。Torch・GPU・有料AI APIは使わない。

難例を含む生成データ1,000商品ペア・459,000 SKUレコードで、正規化のみ約10.3秒、埋め込み併用約13.1秒。表記の重複をキャッシュした処理時間で、459,000回の独立したモデル推論ではない。未計算の2,048文字列は約6.3秒（約325文字列/秒）、検証プロセスのピークRSSは約891MiB。CSV書き込み時間は含めない。合成データであり、本番精度の推定には使わない。

`results/20261009-cpu-benchmark-controls.json` に、SKU単体と商品名＋SKUの比較、閾値ごとの誤一致、既存SKUへの順位、トークン数、速度を保存した。不明属性83例は一致/不一致の精度計算から除いた。今回の入力では切り詰めは0件だが、長いタイトルの末尾にSKUを付けると128トークンを超えてSKUが欠落しうる。

`fastino/GLiNER2.5-multi-Decide` は別方式の分類候補。公式情報・ライセンス・固定revisionは `gliner-candidate.json`、別セッション向けの調査タスクは `model-selection-prompt.txt` を参照する。小規模CPU試験は `results/20261009-gliner-smoke.json`。初回の制御例12件では4件だけ期待ラベルと一致し、既知の不一致7件を全て一致と判定した。約0.28秒/ペア、ピークRSS約2.75GiB。これは少数のテンプレートに対する動作確認で、モデル一般の精度評価ではない。依存は `gliner2==2.0.0`、`torch==2.7.1+cpu`、`transformers==5.17.0`。最初のTransformers 4.57.6ではTokenizerの互換性エラーとなり、checkpointが指定する5.17.0へ合わせて実行した。

## 再実行と引き継ぎ

実レスポンス・商品/SKU表・生成サンプルは、SHA256検証済み `results/20261009-data-checkpoint.zip` に保存した。モデル重み・資格情報・venvは含まない。別workspaceで展開する際は、異なる既存ファイルを上書きしない復元処理を使える。

```bash
python3 -B experiments/sku-matching/package_data.py --restore experiments/sku-matching/results/20261009-data-checkpoint.zip
bash experiments/sku-matching/setup-runtime.sh
.deps/sku-matching-venv/bin/python -B experiments/sku-matching/benchmark.py --dataset .lab-output/sku-synthetic-controls-20261009/dataset.jsonl --evaluation .lab-output/sku-synthetic-controls-20261009/evaluation.json --model-dir .deps/sku-matching-model --output .lab-output/sku-new-benchmark.json
python3 -B experiments/sku-matching/match_skus.py --input experiments/sku-matching/results/real-sibling-pair.jsonl --output .lab-output/sku-new-matches
```

新規サンプルは `generate_samples.py --output NEW_DIRECTORY --pairs 1000`、既知のau商品ID収集は `fetch_au.py --output-dir NEW_DIRECTORY ITEM_ID ...`。出力先は新規ディレクトリを指定する。既存の取得物を上書きしない。

検証コマンドは `python3 -B -m unittest discover -s tests -p 'test_lab*.py'`。今回の環境では既存テスト用の `robots-parser@3.0.1` を `.deps/bot-diagnostics` に導入した。一般のSKU検証にNode依存は不要。

GLiNERの追加確認では、観測ペアを基に作った6制御例のタイトル＋SKU入力で期待ラベル一致4/6、末尾に「同一SKUか判定してください。」を付けた入力で1/6だった。後者は不一致を含む全例を一致と判定した。この入力依存を踏まえ、現状のテンプレートで最終SKU判定への採用は保留する。ケース定義・元データのSHA256・予測・依存バージョン・再現コマンドを結果JSONに保存した。
