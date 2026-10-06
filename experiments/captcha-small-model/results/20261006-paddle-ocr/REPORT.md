# PP-OCRとcommon_oldの公開文字画像比較（2026-10-06）

公開CAPTCHA文字画像3,070枚を、画像全体を1行として認識する条件で評価した。大小文字を区別し、正解文字列と全体が一致した画像だけ正解とした。

今回の数字6桁中心のProject Slothでは **PP-OCRv6 Small**、歪んだ英数字5桁のKaggleでは **common_old (ddddocr 1.5.6)** が有力。完全一致率が同じモデルはCPU時間の短いものを選んだ。この結論は今回の2データセットに対する結果。

## 完全一致と処理時間

| モデル | Sloth / 2,000 | Kaggle / 1,070 | 全体 / 3,070 | 全体完全一致率 | 平均CPU ms/枚 | 推論エラー |
|---|---:|---:|---:|---:|---:|---:|
| common_old (ddddocr 1.5.6) | 1509 (75.45%) | 916 (85.61%) | 2425 | 78.99% | 19.69 | 0 |
| PP-OCRv6 Tiny | 984 (49.20%) | 485 (45.33%) | 1469 | 47.85% | 6.45 | 0 |
| PP-OCRv6 Small | 1891 (94.55%) | 598 (55.89%) | 2489 | 81.07% | 23.77 | 0 |
| PP-OCRv6 Medium | 1891 (94.55%) | 550 (51.40%) | 2441 | 79.51% | 87.93 | 0 |
| en_PP-OCRv5 Mobile | 1834 (91.70%) | 375 (35.05%) | 2209 | 71.95% | 22.22 | 0 |
| PP-OCRv5 Server | 278 (13.90%) | 405 (37.85%) | 683 | 22.25% | 85.02 | 0 |

## データの記録

Project Slothの公開test 2,000枚と、Kaggle CAPTCHA Images v2の重複除外後1,070枚。Kaggle ZIPには2,140画像エントリがあり、同一SHA-256の複製をまとめ、ラベルの整合性も確認した。Project Slothの正解はファイル名の最初のドットまで（先頭ゼロを保持）、Kaggleは画像ファイルのstem。画像の再保存や追加学習は行っていない。

- Project Sloth revision: `eeaf2b6ec9086645f270f7e2aaa9ea90730683de`
- Manifest SHA-256: `4af07d4e8125d3734ed8e9a9282fad2a8d0683383a6ab4ca9638ec3a87a6090d`
- [manifest.json.gz](manifest.json.gz): 全画像のID・出典・正解・元メンバー名と別名・画像SHA-256、入力アーカイブのSHA-256・サイズ。
- [predictions.jsonl.gz](predictions.jsonl.gz): 全3,070画像×6モデルの予測・一致判定・編集距離・処理時間・失敗記録。各行には元manifestのsampleも含む。
- [metrics.json](metrics.json): ソース別の完全一致・大小文字を無視した一致・文字誤り率・平均/p50/p95時間、モデルSHA-256・辞書SHA-256・設定・実行環境・検証結果。

元の画像manifestはmainにないため、前回と画像単位で同一だったことは検証できない。今回のcommon_oldのソース別正解数・大小文字を無視した正解数・編集距離・参照文字数・文字誤り率は、前回の集計と全て一致した。Kaggleの元split割当は不明のため、元test 214枚の再集計は行っていない。

## 実行条件と再利用

Python 3.12.14、RapidOCR 3.9.2、ONNX Runtime 1.30.0。AMD EPYC 9V74を使うCloud CPU環境、CPUExecutionProvider・intra-op 2・inter-op 1。PP-OCRは各モデルを順番に実行し、先頭画像1枚のwarmupを計測から除外した。入力はRGBからBGRへ変換し、RapidOCR標準の高さ48、幅320以上の動的padding。認識時間は画像decode、入力変換、モデル前処理、推論、文字列decodeを含み、画像ファイルopenとモデルロードを除く。common_oldは既存のevaluate_public_text.pyとtext_ocr.pyを使用した。

文字検出・向き分類・信頼度閾値・ノイズ除去・文字集合の制限・予測文字列の正規化は使っていない。時間は単発参考値で、common_old測定の冒頭には短時間のスモーク/単体確認が重なった。学習時のデータ重複は未確認。

gzipはPython標準ライブラリで読める。入力を復元したら、manifestの画像SHAを必ず照合する。実行例とモデル取得方法は[README](../../README.md#pp-ocrの比較)を参照。

```python
import gzip, json
manifest = json.loads(gzip.open("manifest.json.gz", "rt", encoding="utf-8").read())
with gzip.open("predictions.jsonl.gz", "rt", encoding="utf-8") as stream:
    rows = [json.loads(line) for line in stream]
```
