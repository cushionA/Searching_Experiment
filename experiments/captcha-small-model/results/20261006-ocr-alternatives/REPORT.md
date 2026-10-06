# 追加OCRモデルと前処理の比較（2026-10-06）

同じ公開CAPTCHA文字画像3,070枚を使い、大小文字を区別した文字列全体の完全一致を再集計した。正解は採点にだけ使い、画像前処理・予測・一致による採用判定には渡していない。

今回の最高値は数字Slothで **PP-OCRv6 Small（元画像）**、英数字Kaggleで **common_old + autocontrast 1%**。この順位は今回の2ソースに対する結果であり、事前学習時の重複は未確認。

## モデルと前処理

| モデル / 設定 | 数字 Sloth / 2,000 | 英数字 Kaggle / 1,070 | 平均CPU ms/枚 | エラー |
|---|---:|---:|---:|---:|
| common_old（元画像） | 1509 (75.45%) | 916 (85.61%) | 19.69 | 0 |
| PP-OCRv6 Small（元画像） | 1891 (94.55%) | 598 (55.89%) | 23.77 | 0 |
| ddddocr common / beta=True | 1567 (78.35%) | 888 (82.99%) | 19.84 | 0 |
| PARSeq Tiny | 1408 (70.40%) | 222 (20.75%) | 30.87 | 0 |
| EasyOCR English G2 / 標準文字集合 | 17 (0.85%) | 17 (1.59%) | 29.72 | 0 |
| EasyOCR English G2 / 固定ASCII英数字 | 146 (7.30%) | 47 (4.39%) | 32.07 | 0 |
| Graf-J CAPTCHA CRNN Finetuned | 104 (5.20%) | 751 (70.19%) | 9.47 | 0 |
| common_old + autocontrast 1% | 1393 (69.65%) | 920 (85.98%) | 20.48 | 0 |
| common_old + Otsu二値化 | 1190 (59.50%) | 813 (75.98%) | 19.05 | 0 |

`common_old`はddddocr 1.5.6でbeta=Falseを選んだモデル名。beta=Trueは別のcommon.onnxであり、名前だけで精度の優劣は決まらない。

CRNNはGraf-J/captcha-crnn-finetuned、固定revision `8ca7bfadc2608b007b5cafe20a7d0c29888a5cbb`。約14.3MBのsafetensorsをstrict loadし、公開モデルのCNN+BiLSTMをローカルPyTorchで再現。公開processor通りL画像150×40、PIL既定bicubic、[0,1]tensor、blank0のgreedy CTCで認識した。Transformersや未確認のremote codeは実行していない。モデルカード記載の学習元はhammer888/captcha-dataとPython Captcha Libraryによる生成画像。カードの精度値と今回の精度は分けて扱う。

PARSeq Tinyは公式commitと重みを固定し、標準RGB32×128入力・autoregressive decode・refine1を使用。文字の正規化やcharset adapterは省いた。EasyOCRはEnglish G2認識器だけを使い、画像全体を1行として認識。標準文字集合と、全画像共通のASCII英数字62文字を許可する設定を別々に記録した。英数字制限はCTC decode前の固定文字集合制約であり、答えから記号を削る後処理ではない。低confidence時の標準コントラスト再試行も含む。

追加前処理はcommon_oldに対して、RGB→L→PIL autocontrast(cutoff=1)と、RGB→L→OpenCV Otsu二値化の2種類。サイズを変えず、形態処理や線除去は行わない。各設定を全画像に固定して適用し、正解を見て画像ごとに結果を選んでいない。内部のリサイズ・入力tensor正規化は各モデルの標準処理のまま。前処理も含めて時間を計測した。

## 2モデル一致時だけ採用する方法

common_oldとPP-OCRv6 Smallの生の予測が完全一致した非空の文字列だけ採用し、それ以外は保留する固定ルール。正解は一致判定に使わない。

| データ | 採用枚数 / 全枚数 | カバー率 | 採用分の完全一致 | 採用した誤答 |
|---|---:|---:|---:|---:|
| Project Sloth test | 1496 / 2000 | 74.80% | 99.06% | 14 |
| Kaggle CAPTCHA Images v2 | 541 / 1070 | 50.56% | 98.89% | 6 |

**採用分の一致率は全画像を解いた精度ではない。** 保留があるためカバー率を併記した。2つの認識器を動かす時間が必要で、採用分にも誤答が残る。

## データ・再現記録

- [manifest.json.gz](manifest.json.gz): 3,070枚の出典、正解、画像SHA-256、アーカイブと別名。前のPP-OCR評価と同じmanifest。
- [predictions.jsonl.gz](predictions.jsonl.gz): 全画像×9モデル/設定の生予測、一致判定、編集距離、時間、エラー。
- [metrics.json](metrics.json): ソース別集計、前処理による改善/悪化枚数、モデルと評価コードのSHA-256、実行条件・環境・検証結果。
- [evaluator-sources.json.gz](evaluator-sources.json.gz): 記録済みの評価コードSHAと照合したスナップショット。標準EasyOCRは英数字制限を追加する前の版も保存。common_oldの元実行には評価コードSHAの記録がないため、その項目だけは現在のリポジトリ実装を収録した。

Sloth公開test2,000枚とKaggle重複除外後1,070枚を使用。元のKaggle split割当は不明。このデータは既に比較に使っており、EasyOCRの英数字設定も標準設定の失敗例を見た後の追加比較である。独立した新しいholdoutの評価とは扱わない。CPUは2 intra-op / 1 inter-op、1画像ずつfp32、モデルごとに順番に実行。速度は単発参考値。

入力manifest復元、固定モデル取得、実行方法は[README](../../README.md#追加ocrと前処理)を参照。入力画像や元アーカイブは変更していない。
