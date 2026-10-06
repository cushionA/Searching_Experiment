# 公開CAPTCHA画像のオフライン評価

次の実CAPTCHA検証に必要な公式デモ候補、学習済み重みの取得先、準備状況は[CAPTCHAテストの引き継ぎ](HANDOFF.md)を参照。

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

### PP-OCRの比較

`evaluate_paddle_text.py`はPP-OCRv6 Tiny / Small / Medium、en_PP-OCRv5 Mobile、PP-OCRv5 Serverの認識モデルを比較する。各画像を1行の文字画像として扱い、文字検出・向き分類・信頼度による除外・文字列の正規化は行わない。CPUExecutionProvider、intra-op 2スレッド、inter-op 1スレッドで、完全一致・大小文字を無視した一致・文字誤り率・処理時間を保存する。比較には同じmanifestを指定し、同時実行を避ける。

```bash
python -m pip install -r experiments/captcha-small-model/requirements-paddle-text.txt
python -B experiments/captcha-small-model/download_paddle_text.py --model-dir /path/to/model-cache
python -B experiments/captcha-small-model/evaluate_paddle_text.py \
  --manifest /path/to/text-images/manifest.json \
  --output /path/to/new-ppocr-small-output \
  --model ppocr-v6-small --model-dir /path/to/model-cache --warmup
```

モデルはRapidOCR 3.9.2のONNX配布を使い、ファイルのSHA-256と埋め込み文字辞書を検証する。認識器はRapidOCR既定の高さ48・幅320以上の動的paddingを使う。`--warmup`は先頭画像1枚を測定前に読む。予測JSONLと集計JSONを100枚ごとに保存し、`--resume`はmanifest・モデル・評価コード・設定・保存済み予測の整合性を確認して続行する。

前回の画像manifestがない場合は、取得済みの公開アーカイブから次の方法で復元できる。入力アーカイブのSHA-256は今回の取得物に固定して照合する。Project Slothはtest 2,000枚、Kaggleは2,140画像エントリから同一SHAの複製をまとめた1,070枚を使う。ラベルはファイル名から取得し、元メンバー名、別名、画像とアーカイブのSHA-256をmanifestに記録する。新しい出力先を使い、前回の画像単位の同一性やKaggleの元split割当を推測しない。

```bash
python -B experiments/captcha-small-model/prepare_text_manifest.py \
  --sloth-archive /path/to/sloth-test.tar.gz \
  --kaggle-archive /path/to/captcha-version-2-images.zip \
  --output-dir /path/to/new-text-inputs
```

今回の比較結果と画像単位の記録は[PP-OCR評価](results/20261006-paddle-ocr/REPORT.md)を参照。

### 追加OCRと前処理

同じ3,070枚でddddocrの`common.onnx`（beta=True）、PARSeq Tiny、EasyOCR English G2、Graf-J CAPTCHA CRNN Finetunedを追加評価した。common_oldへの固定コントラスト補正とOtsu二値化、common_oldとPP-OCRv6 Smallが一致した場合だけ採用する方法も[追加比較レポート](results/20261006-ocr-alternatives/REPORT.md)に記録している。英数字はcommon_oldの元画像85.61%、軽いコントラスト補正85.98%。この差は1,070枚中4枚で、数字側は悪化した。前処理の一律適用は推奨しない。

追加実験も独立した環境で行い、CPUの計測は1モデルずつ実行する。今回のPyTorchとtorchvisionはそれぞれ`2.14.1+cpu`、`0.29.1+cpu`。GPU版はこのCPU測定の再現条件に含めない。

```bash
python -m pip install torch==2.14.1+cpu torchvision==0.29.1+cpu --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r experiments/captcha-small-model/requirements-additional-text.txt
python -B experiments/captcha-small-model/download_additional_text.py --model-dir /path/to/model-cache

python -B experiments/captcha-small-model/evaluate_dddd_beta.py \
  --manifest /path/to/text-images/manifest.json --output /path/to/new-beta-output
python -B experiments/captcha-small-model/evaluate_crnn_text.py \
  --manifest /path/to/text-images/manifest.json --output /path/to/new-crnn-output \
  --model-dir /path/to/model-cache/captcha-crnn
python -B experiments/captcha-small-model/evaluate_easyocr_text.py \
  --manifest /path/to/text-images/manifest.json --output /path/to/new-easyocr-output \
  --model-dir /path/to/model-cache/easyocr
```

EasyOCRの固定英数字文字集合は`--ascii-alnum`を加え、必ず別の出力先を指定する。CRNNは公開コードを確認し、ローカルに同じネットワークと前処理を実装した。モデル、設定、公開コードとカードの9ファイルを固定SHA-256で照合し、safetensorsをstrict loadする。公開Pythonファイル自体は実行しない。

PARSeq Tinyは公式ソースを次のcommitに固定する。評価時にcommitとtracked filesの未変更を確認する。

```bash
git clone https://github.com/baudm/parseq.git /path/to/parseq-source
git -C /path/to/parseq-source checkout 1902db043c029a7e03a3818c616c06600af574be
python -B experiments/captcha-small-model/evaluate_parseq_text.py \
  --manifest /path/to/text-images/manifest.json --output /path/to/new-parseq-output \
  --source-dir /path/to/parseq-source --weight /path/to/model-cache/parseq_tiny-e7a21b54.pt

python -B experiments/captcha-small-model/evaluate_preprocessed_text.py \
  --manifest /path/to/text-images/manifest.json --output /path/to/new-autocontrast-output \
  --variant autocontrast
```

二値化は`--variant otsu`と新しい出力先を使う。前処理の設定を画像の正解に合わせて選ばず、元アーカイブと入力画像も変更しない。全件の生予測と一致判定、元のmanifest、実行条件とモデルSHA、評価コードスナップショットは追加比較フォルダーに保存した。一致した画像だけ採用する方法の精度には、保留を含むカバー率を必ず併記する。

Kaggle用Notebookは、利用者自身の公開またはアクセス可能なデータセット参照を指定して生成する。モデルごとに個別ジョブを作る場合は`--models`に1モデルずつ指定する。Notebookの継続・成果物回収スクリプトはリポジトリ内のkaggle-ops補助コードを使うため、その環境でセットアップと認証が必要である。認証情報はソース管理へ含めない。実行コードのDataset参照は利用者が指定し、実験記録には再現に必要なprivate job/versionの参照を保存する。

```bash
python -B experiments/captcha-small-model/build_public_eval_notebook.py \
  --manifest /path/to/public-evaluation.json \
  --dataset-ref OWNER/DATASET \
  --models MobileCLIP2-S0 \
  --output /path/to/new-notebook.ipynb
```

データ入力、モデルキャッシュ、実行出力、ノートブック、ログはこの公開コードとは別に管理する。公開画像と保存ボードの評価結果を、サイトでの成功率や動的セッション全体の評価として解釈しない。

## 実測上位の微調整（2026-10-06）

追加比較後、CRNNとMobileCLIP2-S3/S4だけを学習した。CRNNは認識器全体をCTCで微調整し、画像側はエンコーダーを固定して線形ヘッドだけを学習した。checkpointはvalidationで選び、下表は学習に使っていない同一test foldの比較である。

| 課題 | 候補 | 同じtestの基準結果 | 学習後 |
|---|---|---:|---:|
| 英数字OCR | 微調整CRNN | common_old 178/214（83.18%） | **201/214（93.93%）** |
| 数字OCR | PP-OCRv6 Small / Medium（未微調整） | Small 372/400（93.00%）、Medium 380/400（95.00%） | CRNN 366/400（91.50%） |
| 画像分類 | MobileCLIP2-S3＋線形ヘッド | S3 811/815（99.51%） | 811/815（99.51%） |
| ボードの選択全体の完全一致 | MobileCLIP2-S3＋線形ヘッド | S3 79/203（38.92%） | **135/203（66.50%）** |

学習後のS3/S4はvalidation分類403/406、ボード64/97で同率だったため、元の重みが小さいS3を採用した。S3は約997 MB、S4は約1.78 GBで、約49 KBのヘッドだけでは実画像を読めない。両ヘッドは上限1,000 epochに到達しており、収束の完了は確認していない。数字は先行2,000枚でSmall/Mediumとも94.55%だったため、軽いSmallを先行候補として維持する。

データ・分割・生予測・失敗を含むjob/version・コードSHA・監査結果を次へ保存した。

- [CRNN学習・同じ614枚のOCR比較・ONNX検証](results/20261006-crnn-finetune/REPORT.md)
- [画像5モデルの比較と上位2ヘッドの学習](results/20261006-image-eval-finetune/REPORT.md)
- [公開画像の復元と重複を跨がない固定分割](results/20261006-image-restoration/REPORT.md)
- [ASTERの追加比較](results/20261006-aster-ocr/REPORT.md)
- [学習済みモデルの非公開保存先・読み戻しSHA検証](results/20261006-tuned-artifacts/REPORT.md)

EfficientFormer、MobileOneの学習は実行していない。YOLOv5 Nano、EfficientDet-Lite0は今回の全対象を網羅する認識器ではなく、RRTrNは検証できる公開重みを確認できなかった。これらは未測定として扱う。橋のtestボードは0件なので精度を推定できない。公開コーパスは先行比較でも使っているため、今回のtestは新規外部データではない。

微調整CRNNのONNXは14,287,573 bytes。CPUで614枚すべての回答がPyTorch/GPU版と一致した。推論に必要な依存はONNX Runtime、NumPy、Pillowである。

```bash
python -m pip install onnxruntime numpy pillow
python -B experiments/captcha-small-model/recognize_finetuned_crnn_onnx.py \
  --model /path/to/captcha-crnn-finetuned.onnx \
  --model-sha256 63f750d945030a32f43c088e869db436e0dda1c321651e3f849f9df0c6b6733d \
  --image /path/to/saved-captcha.png
```

ONNXには専用のグレースケール化・150×40へのresize・語彙・greedy CTC decodeを組み合わせる。common_oldの既存前処理へグラフだけを差し替えない。元のsafetensorsで再評価する場合は、`evaluate_finetuned_crnn_text.py`へ元の9ファイルがある`--model-dir`、選択重みと`--checkpoint-sha256`、未変更の全体`--manifest`と`--split`、`--fold test`を指定する。

画像側は`recognize_finetuned_image_head.py`と保存済みS3ヘッド、`deployment.json`、元のS3モデルを組み合わせる。入力は保存画像で、分類には`--allowed-labels`、ボードには`--target`と順序付きタイル画像を指定する。元モデルはローカルに取得済みであることが必要で、CLIはダウンロードしない。

```bash
python -B experiments/captcha-small-model/recognize_finetuned_image_head.py \
  --bundle /path/to/exported-image-head --model-dir /path/to/s3-cache \
  --mode board --target bus \
  --images /path/to/tile-0.png /path/to/tile-1.png /path/to/tile-2.png
```

ヘッド読取もSHA・出力ラベル・寸法を照合する。元の保存特徴からtestの分類815件・タイル2,317件・ボード203問の出力が同一になることを確認した。画像の再encode全体を別環境で再測定した検証ではない。
