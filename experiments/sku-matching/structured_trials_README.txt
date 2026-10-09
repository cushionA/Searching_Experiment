実データによる固定au商品内のSKU突合試行（2026-10-09 UTC）

採用候補は、選択SKUの属性を原文から正規化し、au商品ページ固有の
条件を継承して、固定したauページの全SKU候補を検査する構造。
Bekko canonical埋め込みは候補の提示に使う。類似度だけで同一SKUを
確定しない。自動の一致・不一致判定は根拠付き仕様制約が決める。
保留・不一致時に提示する候補行の順位は埋め込みに依存する。

前提と判定

本試行では、上流で得た商品ペアを固定し、そのSKU同一性を評価する。
商品単位の一致自体は再判定しない。別au商品URLへの振り分けは
行わない。商品タイトル全体の単純な連結比較は行わず、選択
SKUの条件と、当該auページの明示的なサイズ・構成条件を照合する。
選択軸は双方とも必須。共通して取得できた背景仕様は矛盾チェック
に使う。背景仕様が片側にないことと、必須のSKU条件が欠けることを
区別する。カーテンのレース有無、枚数、フック等の構成は厳格に扱う。
原文内の未解決の仕様矛盾・表記差は保留。数値だけで、別の測定対象
を同一視しない。明示された標準サイズ名の選択条件の違いは不一致
根拠とするが、実寸が同じ場合は表記差の可能性があり保留する。
これらは本試行で採用した判定ルール。

固定au全候補のうち、仕様上の一致が1行だけならmatched。複数なら
review。一致がなく未解決候補が残る場合もreview。全候補に明示的な
矛盾がある場合だけunmatched。モデルのtop1の間違いや低スコアだけ
では全配列に一致SKUがないと判定しない。

出力除外の対象にできるのはunmatched。reviewは別途確認する。
実際の出力除外処理との接続は、この試行の評価範囲に含めていない。
試行コードは元データを削除せず、カートにも追加しない。価格・在庫
は同一SKU判定の入力から分離して元のsidecarに保持する。au価格は
商品ページ単位、楽天価格は実際のSKUレコード単位を維持する。

最新版の診断結果

.lab-output/sku-structured-task-trials-20261010-v10/
  tasks.jsonl / evidence.jsonl: 原文・SKU配列に基づく入力と根拠
  code/: 実行時コードの凍結コピーとSHA256
  bekko/: CPU ONNX候補順位、旧top1 gateの診断結果
  hybrid/: 全au候補を検査した最終判断と集計
  audit/: Lunaによる独立した再計算と構造・逆レース紐付けの確認

29固定auページ、481実au SKU行、17楽天URL、644ユニーク楽天SKUに
基づく1,383突合ケース。各ケースは楽天SKU1件対固定au全SKU配列。
人工生成データは0件。修正後ラベルは一致435/不一致911/保留37。
Bekko順位＋全配列仕様gateの予測は一致337/不一致815/保留231。
受理337件は全てv3機械ラベルの指定au行と一致。ラベル上の誤受理0、
matched→unmatchedの誤拒否0。ラベル正例98件は
保留なので、一致の受理再現率は77.5%。自動判定率は全1,383件の
83.3%、保留ラベルを除く1,346件では85.6%。
カーテン正例の受理再現率83.8%、カーテン以外は56.9%。

ラベルは原文監査を反映したLuna機械ラベルで、人手確定goldではない。
testは何度も参照した診断用データで、758件中720件がカーテン。
新規の独立holdoutや本番の一般化精度を証明した結果ではない。
54正例はauのフック個数が不明のため保留。未知仕様を推測して
受理再現率を引き上げない。ユーザー監査の35件も全件保留を維持。

モデル比較

v4ではBekko / Ruri / MiniLM / GraniteのCPU埋め込み、軽量日本語
rerankerを比較した。canonical属性と原SKU文字列を足す形式は別々
に記録した。devで原SKU付き形式が選ばれてもtestで崩れた例があり、
これらの失敗結果も残している。最終候補は原文から解決した属性のみ
のcanonical形式と、候補top1に依存しない全配列の仕様検査。
GLiNER2.5-multi-Decideは166実ケースで3形式を実行。rawモデルは
日本語形式と短い英語形式で全件review、他の英語形式で全件matched
となり、同一SKUの直接判定には採用しない。決定的な仕様gateを
付けた結果をGLiNER単体の性能として扱わない。

明示的な入力パスで再実行する例（既存outputへの上書きは拒否）

python3 -B experiments/sku-matching/prepare_enriched_sku_tasks.py \
  --inputs-dir .lab-output/sku-real-luna-annotation-inputs-20261010-v3 \
  --au-arrays .lab-output/sku-observed-product-pairs-20261010-final-v4/au_product_sku_arrays.jsonl \
  --output .lab-output/sku-enriched-replay-new

.deps/sku-matching-venv/bin/python -B experiments/sku-matching/evaluate_structured_skus.py \
  --tasks .lab-output/sku-enriched-replay-new/tasks.jsonl \
  --inputs-dir .lab-output/sku-real-luna-annotation-inputs-20261010-v3 \
  --au-arrays .lab-output/sku-observed-product-pairs-20261010-final-v4/au_product_sku_arrays.jsonl \
  --labels .lab-output/sku-real-luna-labels-20261010-v3/labels.jsonl \
  --model bekko --output .lab-output/sku-enriched-replay-new/bekko

python3 -B experiments/sku-matching/evaluate_fixed_pool_hybrid.py \
  --tasks .lab-output/sku-enriched-replay-new/tasks.jsonl \
  --ranking .lab-output/sku-enriched-replay-new/bekko/predictions-canonical.json \
  --inputs-dir .lab-output/sku-real-luna-annotation-inputs-20261010-v3 \
  --au-arrays .lab-output/sku-observed-product-pairs-20261010-final-v4/au_product_sku_arrays.jsonl \
  --labels .lab-output/sku-real-luna-labels-20261010-v3/labels.jsonl \
  --output .lab-output/sku-enriched-replay-new/hybrid

モデルcacheは.deps/sku-bekko-model、pinned revisionと重みSHAは
experiments/sku-matching/manifests/bekko.jsonおよび各summaryに記録。
コードコピーの単独ディレクトリから実行するには、参照する既存の
SKU収集・解析helperとモデルmanifestも元repository構造に置くこと。

python3 -B -m unittest discover -s tests -p 'test_lab*.py'
検証済み: 269 tests、成功、環境依存の15件skip。

確認用データ

.lab-output/sku-hybrid-review-20261010-v1/review_skus.csv は元の修正済み
レビューCSV全列・値・行順を保持し、5つの予測列を追加したもの。
review-with-v10-hybrid.zipに原本を含む既存証拠ZIPをそのまま同梱。
元データ・旧ラベル・旧試行は変更していない。
