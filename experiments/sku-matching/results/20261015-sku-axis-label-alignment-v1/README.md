# SKU軸ラベルだけによる対応付け

auの軸数が楽天以下の商品ペアに対し、auの各軸から最も似た楽天の軸名を選ぶ実験。
サイズ同士・色同士へ値の比較を絞る前段として使う。元データに軸名があるため、Lunaによる再注釈は行わなかった。

107商品ペア・1,370ケースの保存済み入力から、商品ペアごとに一度だけ軸を取り出した。
軸数条件を満たす103組で165軸を照合し、auの方が多い4組は処理しなかった。
元入力は全て`exploration`。以前のmembershipに含まれる20組も区別して保存したが、独立した評価集合ではない。
元の楽天軸名が空白の1件も補完せず保存した。

## 方法

- CPUのMiniLM量子化ONNXとRuri v3 30M系の固定モデルを使用。revision・重みSHA・依存バージョンは各`summary.json`に保存。
- エンコーダへ渡すのは元の軸名文字列だけ。値、商品名、本文、価格、SKU一致の注釈、過去の判定は渡さない。
- 一意の52ラベルをまとめて計算し、コサイン類似度の最大候補を各au軸から独立に選ぶ。
- 複数au軸が同じ楽天軸を選ぶ衝突、同点、余った楽天軸を出力する。同点の全候補は別の曖昧な軸として保存し、非対称候補から除外する。
- 辞書、カテゴリ分岐、閾値調整、学習は行わない。全出力は候補であり、SKU同一性や商品同一性の確定ではない。

## 観測結果

| 指標 | MiniLM | Ruri |
| --- | ---: | ---: |
| 処理対象の商品ペア | 103 | 103 |
| 対応候補を選んだau軸 | 165 | 165 |
| 唯一の同名軸を選択 | 123 / 123 | 123 / 123 |
| 先頭候補の衝突があるペア | 3 | 7 |
| 同点のあるペア | 2 | 2 |
| 非対称候補の楽天軸（同点候補を除く） | 16 | 20 |

165軸中127軸には元ラベルの完全一致候補があり、そのうち4軸は同名の楽天軸が複数あった。
残る38軸ではモデルが表記違いを含む候補を選んだ。2モデルの先頭候補は160/165軸で一致したが、これは正答率ではない。
以前のmembershipに含まれる20組・31軸ではMiniLMの先頭候補衝突は0、Ruriは1組・2軸。

`ワイズ→足囲`はMiniLMが3組で選べた。元の値にも両側で`3E / 4E / 5E`がある。
Ruriはこの3組で`ワイズ→サイズ（cm）`を選び、サイズ軸と衝突した。
`サイズ→サイズ（cm）`、`カラー→商品カラー`、`型→形状`なども候補として取り出せた。

ただし軸数が同じでも対応先が存在するとは限らない。
`raw-635b86c6e684e1d1`はauが`カラー / サイズ`、楽天が`サイズ / ヒール`。
auのカラー値は`シルバー`、楽天のヒール値は`5cm / 8cm / 10cm`だが、両モデルともカラーをヒールへ強制的に対応させた。
`種類`に対する楽天の`一つ目 / 二つ目`も、ラベルだけではどちらかを決められない。
制御例でも、対応先のない`カラー→サイズ`が必ず選ばれることを確認した。

## 値の比較への利用

対応候補を凍結した後で、元の選択値を参照し、比較する値の組合せ数を算数上集計した。
各ペアの全au軸の値と全楽天軸の値を横断的に比較すると8,094組。
提案した対応軸内だけならMiniLMは5,734組（29.2%減）、Ruriは5,815組（28.2%減）となる。
誤対応や衝突も含む候補数であり、処理速度や正しいSKU一致数の改善を測った数字ではない。

対応軸内の原文字列の完全一致も集計した（MiniLM 521 / Ruri 512、au側の軸別異なり値計725）。
`21.0cm`と`21.0`はこの集計では別文字列。単位正規化やSKU行単位の一致判定は行っていない。
結果は軸対応を値比較の候補絞込みに使う根拠になるが、相手のいない軸を自動確定する根拠にはならない。
衝突・同点・対応先不明を保留し、次段で値の全候補や商品側の固定属性を確認する必要がある。

## 再実行

リポジトリルートから実行する。入力JSONL、予測、モデルSHA、出典、制御例、検証結果はこのフォルダに保存した。
重み・venv・元ページ本文は含まない。

```bash
bash experiments/sku-matching/setup-runtime.sh
python3 -m venv .deps/sku-candidate-venv
.deps/sku-candidate-venv/bin/python -m pip install -r experiments/sku-matching/candidate-runtime.txt
python3 -B experiments/sku-matching/download_candidate.py --model ruri

.deps/sku-candidate-venv/bin/python -B experiments/sku-matching/align_axis_labels_v1.py \
  --input experiments/sku-matching/results/20261015-sku-axis-label-alignment-v1/input/axis-pairs.jsonl \
  --output-dir .lab-output/sku-axis-label-new-minilm --model minilm
.deps/sku-candidate-venv/bin/python -B experiments/sku-matching/align_axis_labels_v1.py \
  --input experiments/sku-matching/results/20261015-sku-axis-label-alignment-v1/input/axis-pairs.jsonl \
  --output-dir .lab-output/sku-axis-label-new-ruri --model ruri
```

source-only入力を再抽出する場合は`prepare_axis_label_pairs_v1.py`へ`--products`、`--cases`、`--membership`、新しい`--output-dir`を渡す。
元ファイルの位置とSHAは`input/provenance.json`にある。既存の軸・ラベル・出典を変更せず、値や本文を抽出結果に入れない。
どちらのCLIも既存出力への上書きを拒否する。

追加した17テストは成功。全`test_lab*.py`は1,503件中、失敗3・エラー35・skip32で成功していない。
失敗は既存の引用正規化テスト、エラーは過去の入力・raw・root manifestなどに依存するテスト。
復元した過去環境は完全には揃っておらず、詳細は`validation.json`に記録した。
