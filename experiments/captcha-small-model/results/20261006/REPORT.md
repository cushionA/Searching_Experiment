# 公開画像と文字OCRの評価（2026-10-06）

4,068枚のラベル付き画像と1,000問の保存ボードを、固定したプロンプトと選択ルールで評価した。保存出力のID・ラベル・スコア・参照・集計を全件照合した。

## 画像モデル

| モデル | 画像正解 / 4,068 | 分類正解率 | ボード完全一致 / 1,000 | 完全一致率 |
|---|---:|---:|---:|---:|
| TinyCLIP-ViT-40M-32-Text-19M | 3772 | 92.72% | 331 | 33.10% |
| MobileCLIP2-S0 | 3969 | 97.57% | 365 | 36.50% |
| MobileCLIP2-S2 | 4025 | 98.94% | 424 | 42.40% |
| MoE-ViE-B16 | 4025 | 98.94% | 427 | 42.70% |

ボード完全一致は、選んだタイルの組み合わせが公開の正解注釈と全て一致する割合。余分な選択も見逃しも不正解になる。画像分類はhCaptcha由来の8クラス、ボードはreCAPTCHAの公開保存画像であり、データセットと採点単位が異なる。

## 文字OCR

ddddocr 1.5.6に同梱された`common_old.onnx`を、`beta=False`、ONNX RuntimeのCPUExecutionProviderで使用した。大文字・小文字を区別し、文字列全体が一致した場合だけ正解とした。

| ソース | 枚数 | 完全一致数 | 完全一致率 | 平均CPU処理時間 |
|---|---:|---:|---:|---:|
| Project Sloth・公開test | 2000 | 1509 | 75.45% | 15.22 ms/枚 |
| Kaggle CAPTCHA Images v2・全split | 1070 | 916 | 85.61% | 14.48 ms/枚 |

Kaggleの元test splitだけでは183/214枚、85.51%。追加学習は行っておらず、既存OCRの事前学習データとの重複は確認できていない。

## 実行時間とメモリ

Tesla T4、CUDA 12.8、PyTorch 2.11.0+cu128での1回の計測。画像処理時間は前処理・転送・画像エンコード・類似度計算の合計で、モデル取得・ロード・テキスト埋め込み・warmupは含めない。MoEと他3モデルは別ジョブの出力を照合した。

| モデル | 画像encoder / 全パラメータ | 画像処理合計 | CUDA peak allocated |
|---|---:|---:|---:|
| TinyCLIP-ViT-40M-32-Text-19M | 39,691,776 / 84,205,569 | 63.71秒 | 380.6 MiB |
| MobileCLIP2-S0 | 11,357,504 / 74,785,601 | 86.06秒 | 583.5 MiB |
| MobileCLIP2-S2 | 35,702,992 / 99,131,089 | 125.13秒 | 782.0 MiB |
| MoE-ViE-B16 | 457,071,808 / 811,154,593 | 615.27秒 | 3618.6 MiB |

## エラーと保存結果の検証

MoEは482/482バッチ、15,420枚のunique画像を処理して結果を保存した後、実行器の1200秒上限でtimeout判定された。元のNotebookはerror、元のプロセスは`returncode: null, timed_out: true`のまま保持している。保存された4,068画像・1,000ボードの出力は、全件の照合とダウンロード時のSHA照合を通過した。

修正版は親プロセスの終了と、子プロセスが保持するstdoutの待機を分ける。将来のMoE上限は1800秒に変更し、実プロセスによる回帰テスト4件を通した。修正版NotebookはGPUへ再提出していない。

## 出典と解釈

- [orlov-ai/hcaptcha-dataset](https://github.com/orlov-ai/hcaptcha-dataset/tree/a1b180f9091719517d8890c33ab8b4d5df38ac10) — Repository LICENSE is MIT; image/source rights and fair-use status are not independently established.
- [ssivakorn/reCAPTCHA-study](https://github.com/ssivakorn/reCAPTCHA-study/tree/efb3595c33780abf9d326c729973e72d165366c2) — CC BY-NC 4.0 (repository LICENSE).
- [fournierp/captcha-version-2-images](https://www.kaggle.com/datasets/fournierp/captcha-version-2-images) — Other (specified in description), unverified; description text unavailable in source metadata.
- [project-sloth/captcha-images](https://huggingface.co/datasets/project-sloth/captcha-images) — WTFPL v2 (repository LICENSE; copy saved alongside this manifest).

公開注釈との一致を測ったオフライン評価。動的なタイル更新を伴うセッションや実サイトでの認証通過率は測っていない。注釈が競合する2問は主結果に残し、除外した998問の感度集計を[metrics.json](metrics.json)へ保存した。
