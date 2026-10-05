# 公開CAPTCHA画像のオフライン評価

公開データセットの画像ラベルと人手注釈済みボードを使い、画像エンコーダーと文字OCRをローカルで評価する。集計表、指標JSON、対象データと評価上の制約は[結果レポート](results/20261006/REPORT.md)と[metrics.json](results/20261006/metrics.json)を参照。

## 結果

4,068枚の公開画像分類はTinyCLIP 40Mが3,772枚、MobileCLIP2-S0が3,969枚、MobileCLIP2-S2が4,025枚、MoE-ViE-B/16が4,025枚で正解した。人手注釈済み1,000ボードの選択全体の完全一致は、それぞれ331、365、424、427問だった。これは公開ラベル・保存ボードとの一致で、ライブCAPTCHAの通過率を示さない。

CPU OCRではddddocr 1.5.6の`common_old.onnx`、`beta=False`を使った。Project Slothの2,000枚で1,509枚（75.45%）、既存の1,070枚で916枚（85.61%）、その元test split 214枚で183枚（85.51%）が文字列完全一致した。処理時間はCPUで約15 ms/枚。事前学習データとの重複は未確認。

MoE-ViEのNotebook実行は元プロセスが1200秒でタイムアウトし、Notebookにもエラーが記録された。一方、保存済み出力一式はオフラインで照合し、4モデル分すべての整合性を確認した。修正した実行器では、親プロセスの終了判定と子プロセスが保持するstdoutの排出上限を分け、将来の実行時間上限を1800秒にした。関連する確認はCPUプロセス回帰テストであり、GPUでの再実行はしていない。

## 環境

Python 3.12以上を使う。PyTorchとtorchvisionは実行環境に合う版を先に導入し、その後このフォルダーの依存を入れる。

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r experiments/captcha-small-model/requirements.txt
```

TinyCLIP、MobileCLIP2、MoE-ViEの公式モデル設定、重みのrevision、ハッシュ検証方法は各スクリプトに記載している。TinyCLIPは `download_model.py --size medium --output MODEL_CACHE` で取得する。MobileCLIP2は指定したキャッシュへ自動取得する。MoE-ViEはCUDA対応PyTorchを備えたGPU環境が必要で、[公式コード](https://github.com/facebookresearch/moe_vie)をスクリプト内の固定commitから取得する。

文字OCRの既定実装は同梱の`text_ocr.py`で、OCRモデルを同じ仮想環境へ追加導入する。既存の外部OCRアダプターを使う場合だけ、`--router-root`でそのcheckoutを明示する。

## 実行例

入力JSONの画像パスはJSONファイルからの相対パスである。独自の公開入力manifestとモデルキャッシュを指定し、毎回新しい出力先を使う。

```bash
python -B experiments/captcha-small-model/benchmark_public_samples.py \
  --input /path/to/public-evaluation.json \
  --output-dir /path/to/new-output \
  --models TinyCLIP-ViT-40M-32-Text-19M,MobileCLIP2-S0,MobileCLIP2-S2 \
  --model-dir /path/to/mobileclip-cache \
  --baseline-dir /path/to/tinyclip-cache \
  --device cpu --precision fp32
```

MoE-ViEはOpenCLIPのimportを分離するため、別プロセスで測定する。

```bash
python -B experiments/captcha-small-model/benchmark_public_samples.py \
  --input /path/to/public-evaluation.json \
  --output-dir /path/to/new-moe-output \
  --models MoE-ViE-B16 --device cuda --precision fp16 \
  --source-dir /path/to/moe-source --model-dir /path/to/moe-cache
```

文字OCRは画像manifestと新しい出力先を指定する。

```bash
python -B experiments/captcha-small-model/evaluate_public_text.py \
  --manifest /path/to/text-images/manifest.json \
  --output /path/to/new-ocr-output
```

Kaggle用Notebookは、利用者自身の公開またはアクセス可能なデータセット参照を指定して生成する。モデルごとに個別ジョブを作る場合は`--models`に1モデルずつ指定する。Notebookの継続・成果物回収スクリプトはリポジトリ内のkaggle-ops補助コードを使うため、その環境でセットアップと認証が必要である。認証情報や実行時のアカウント・ジョブ参照はソース管理へ含めない。

```bash
python -B experiments/captcha-small-model/build_public_eval_notebook.py \
  --manifest /path/to/public-evaluation.json \
  --dataset-ref OWNER/DATASET \
  --models MobileCLIP2-S0 \
  --output /path/to/new-notebook.ipynb
```

データ入力、モデルキャッシュ、実行出力、ノートブック、ログはこの公開コードとは別に管理する。公開画像と保存ボードの評価結果を、サイトでの成功率や動的セッション全体の評価として解釈しない。
