# ASTER PyTorch portの公開文字画像評価（2026-10-06）

同じ公開CAPTCHA画像3,070枚を、大小文字を区別した文字列全体の完全一致で評価した。beam 5とgreedyをCPUで順番に実行し、正解文字列は採点だけに使った。

| 設定 | 数字 Sloth / 2,000 | 英数字 Kaggle / 1,070 | 全体 / 3,070 | CER | 平均CPU ms/枚 | 失敗 |
|---|---:|---:|---:|---:|---:|---:|
| ASTER PyTorch port / beam 5 | 468 (23.40%) | 20 (1.87%) | 488 (15.90%) | 55.48% | 242.61 | 0 |
| ASTER PyTorch port / greedy | 429 (21.45%) | 21 (1.96%) | 450 (14.66%) | 62.28% | 103.42 | 0 |

既存common_oldの同一manifestによる保存値はSloth 1,509/2,000（75.45%）、Kaggle 916/1,070（85.61%）。今回のASTER checkpointは両ソースで弱いため、追加学習の対象にしない。既存baselineは再実行していない。

## モデルと実行条件

原著者bgshih/asterがリンクするMingkun Yang / ayumiymkのPyTorch移植を使った。移植には論文の双方向attention decoderが含まれないため、結果を論文完全版ASTERの精度とは扱わない。ResNet encoderと双方向LSTM、TPS rectifier、attention decoderを使用。

公式test-all設定に合わせ、RGB→PIL bilinear 256×64→tensor→[-1,1]とした。demo.pyの100×32既定値は使っていない。TPSは入力32×64、出力32×100、control points 20、margins 0.05。PyTorch 1.1時代の挙動を維持するためgrid_sampleのalign_corners=Trueを明示した。beam predecessorの整数floor除算、device上のscratch tensor、argv import除去等の互換patchを保存している。

CPU FP32、batch 1、intra-op 2 / inter-op 1、最大100 token。beam 5は公式beam_search、greedyは公式sample。STN→TPS→encoder→decoderを直接実行し、GT/dummy targetsとteacher-forced lossは使わない。weights_only=Trueのsafe loadとstrict state_dict照合に成功。外部文字制限、lexicon、大小文字・記号の正規化、confidence閾値は使っていない。beamのscoreはplaceholderなのでconfidenceは記録しない。

先頭1枚のwarmupを計測から除外し、画像decode・前処理・TPS・推論・文字列decodeを計測した。ファイルopenとモデルloadは除外。時間は単発の参考値。PADDING/UNKNOWNはEOS前ならtoken名をそのまま返す設定だが、両runの全6,140予測でその出現は0件だった。大小文字を無視した一致率は補助値としてmetrics.jsonに保存した。

## 再現・監査記録

- Source commit: `be670046c775b54de79766208f0c59321ae1eccf`
- Release weight SHA-256: `c2d730c1f96357bb605bc7ef5d263537c4b0ec47de9491c766eda3132cdd6021`（84,424,941 bytes。重み本体はrepoに含めない）
- Manifest SHA-256: `4af07d4e8125d3734ed8e9a9282fad2a8d0683383a6ab4ca9638ec3a87a6090d`
- [metrics.json](metrics.json): 全source/全体の完全一致・補助case-insensitive一致・CER・時間・エラー、run設定と全SHA。
- [manifest.json.gz](manifest.json.gz): 同一3,070画像のsource・label・画像SHAと復元provenance。
- [aster-beam5.predictions.jsonl.gz](aster-beam5.predictions.jsonl.gz) / [aster-greedy.predictions.jsonl.gz](aster-greedy.predictions.jsonl.gz): 各run全3,070行のraw予測・採点・時間・エラー。
- [evaluator-sources.json.gz](evaluator-sources.json.gz): 計測に使った評価コードと監査/exportコードのSHA付きsnapshot。
- [official-source-and-compat.tar.gz](official-source-and-compat.tar.gz) / [source-files-sha256.json](source-files-sha256.json): 公式source 37 files、compat 4 modules、patch、manifestの内容とSHA。READMEのMIT記載とnoticeも保持。

Sloth公開test2,000枚とKaggle重複除外後1,070枚を使った。元Kaggle splitは不明。この画像群は既にモデル比較に使用済みで、独立holdoutではない。事前学習との画像重複は未確認。旧比較結果は[追加OCR比較](../20261006-ocr-alternatives/REPORT.md)に保持した。

```bash
python -B evaluate_aster_text.py --manifest manifest.json --output new-aster-beam5 \
  --model-dir /path/to/aster --beam-width 5 --device cpu
# greedyは別outputと --beam-width 1 を使う。
```

model-dirに公式release weightを置き、source archive内のofficial-source/とcompat/を展開する。入力画像を復元したらmanifestの画像SHAを照合する。評価コードはevaluate_public_text.pyと同じディレクトリに置く。
