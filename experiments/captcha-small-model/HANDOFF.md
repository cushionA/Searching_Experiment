# CAPTCHAテストの引き継ぎ

更新日: 2026-10-06。モデル比較・学習・重み保存のコードと記録はcommit `d409c56e3b1a0208dd7a4943b51384e59ef1468c`、branch `work`にpush済み。

## ユーザーの方針と現在地

- 実測で強い候補だけをチューニングする。CRNNとMobileCLIP2-S3/S4だけ学習した。
- Kaggleのjob/versionを固定して完了を監視し、結果を回収・検証してから続行する。今回の計算と回収は完了済み。
- 重みを永続保存してからコードと評価記録をpushする。保存先の実ダウンロードとSHA一致まで確認済み。
- 次の候補は、CAPTCHAを出題する公式公開デモ。2026-10-06にページの存在と仕様を調査した。ブラウザでの実出題・回答・通過率の検証はまだ実施していない。

## 実CAPTCHAの検証候補

| 優先用途 | 公式デモ | 確認した内容・次に確認すること |
|---|---|---|
| 文字OCR | [BotDetect Features Demo](https://captcha.com/demos/features/captcha-demo.aspx) | 文字画像と入力欄、文字数・Localeの設定がある。最初のOCR検証候補。正解ラベルの公開は未確認。 |
| 画像ボード | [hCaptcha Demo](https://accounts.hcaptcha.com/demo) | 公開のサンプルフォームが存在する。実際の画像チャレンジ表示は未確認。 |
| 画像ボード | [Google reCAPTCHA v2 Demo](https://www.google.com/recaptcha/api2/demo) | チェックボックス型の公式デモ。リスク判定により画像問題が省略される場合がある。 |
| スライダー・アイコンなど | [GeeTest Adaptive CAPTCHA Demo](https://www.geetest.com/en/adaptive-captcha-demo) | 公式ページでSlide・Icon・Gobang・Icon Crush等の種類を案内。実問題の表示と、現在のモデルでの採点は未確認。 |

BotDetectの[画像スタイル集](https://captcha.com/demos/image-styles/captcha-demo.aspx)も文字画像の傾向確認に使える。Googleの[バージョン仕様](https://developers.google.com/recaptcha/docs/versions)と[テストキーの説明](https://developers.google.com/recaptcha/docs/faq)、[hCaptchaの開発文書](https://docs.hcaptcha.com/)を参照する。固定テストキーの自動成功は、実問題の認識精度を測った結果には数えない。

文字OCRはBotDetect、画像ボードはhCaptcha/reCAPTCHAからの確認が候補。GeeTestのスライダー等は、今回評価した文字認識・画像分類・ボード選択とは別の課題として記録する。

## 採用候補と実測

下表は学習に使っていない固定test foldの値。同じ公開素材を過去のモデル選択にも使っているため、新規の独立外部testではない。実サイトの通過率とも区別する。

| 課題 | 候補 | 同じtestでの結果 |
|---|---|---|
| 英数字OCR | 微調整CRNN epoch 37 | 201/214 = 93.93%。common_oldは178/214 = 83.18%。 |
| 数字OCR | PP-OCRv6 Small / Medium（未微調整） | Small 372/400 = 93%、Medium 380/400 = 95%。CRNNは366/400 = 91.50%。先行2,000枚ではSmall/Mediumとも94.55%で、軽いSmallを先行候補として維持。 |
| 画像分類 | MobileCLIP2-S3 | raw/headとも811/815 = 99.51%。 |
| ボード選択全体の完全一致 | MobileCLIP2-S3＋線形ヘッド | 同じ203問でraw 79/203 = 38.92%からhead 135/203 = 66.50%へ改善。 |

CRNNは認識器全体をCTCで学習。画像側はエンコーダーを固定し、小さい線形ヘッドだけ学習した。S3/S4のvalidationは分類403/406、ボード64/97で同率だったため、元の重みが小さいS3を選んだ。testでモデルを選択していない。両ヘッドは上限1,000 epochに到達しており、完全収束は未確認。bridgeのtestボードは0件で未評価。

詳細は[CRNN記録](results/20261006-crnn-finetune/REPORT.md)、[画像比較・ヘッド学習記録](results/20261006-image-eval-finetune/REPORT.md)、[追加OCR比較](results/20261006-ocr-alternatives/REPORT.md)、[ASTER比較](results/20261006-aster-ocr/REPORT.md)。EfficientFormer/MobileOne等の追加候補は実学習していない。

## 重み・入力データ・復元先

[非公開Kaggle Dataset](https://www.kaggle.com/datasets/superbigzabuton/captcha-tuned-results-20261006)のversion 1に、次を保存済み。取得には所有者の認証が必要。

- ファイル: `captcha-tuned-results.bin`（通常のZIP）、116,371,209 bytes。
- SHA-256: `dcb5153663328b4bbe721a6e45d5633c880e5c5a7402ee148c302a94346d655d`。
- 選択CRNN重み・再開状態、約14.3 MBのONNX、S3/S4ヘッド、S3の保存特徴、固定分割・全予測・コード・出典記録を含む。
- 非公開設定・ready・version 1・ファイル容量を照合し、version 1の個別ファイルを読み戻してSHA一致を確認した。一括ZIP取得APIは404だったため、個別ファイル取得を使った。
- 元画像と初期CRNNファイルはprivate input Datasetの`superbigzabuton/captcha-ocr-ft-input-20261006`、`superbigzabuton/captcha-image-ft-input-20261006`（ともにversion 1）に保持。

保存時の[検証記録](results/20261006-tuned-artifacts/REPORT.md)を参照。元の大きいMobileCLIP2-S3エンコーダー（996,928,503 bytes）は上の成果物ZIPに含まない。公式`apple/MobileCLIP2-S3`の固定revisionと重みSHAはZIP内`runtime/exported-image-head/deployment.json`に記録している。約49 KBのヘッドだけでは画像を読めない。

取得したファイルの展開例:

```bash
python -m zipfile -e captcha-tuned-results.bin restored-results
```

選択CRNN checkpointは展開先の`models/crnn-epoch37-and-resume/best.safetensors`。SHA-256は`cce95d7cd320d332fecb606b39ccdcf269eb9794093bea7e2c0a4e4b30c11691`。

CRNN ONNXは展開先の`runtime/exported-crnn/captcha-crnn-finetuned.onnx`。SHA-256は`63f750d945030a32f43c088e869db436e0dda1c321651e3f849f9df0c6b6733d`。CPU/GPU/PyTorch/ONNX間で同じ614枚の回答一致を確認済み。Torchなしの読取CLIは[recognize_finetuned_crnn_onnx.py](recognize_finetuned_crnn_onnx.py)。グレースケール化、150×40 resize、固定語彙とCTC decoderを含めて使う。

S3の読取CLIは[recognize_finetuned_image_head.py](recognize_finetuned_image_head.py)。展開先の`runtime/exported-image-head/`と、ローカル取得済みの元S3エンコーダーを指定する。分類は指定ラベル内argmax、ボードは指定targetのsigmoid >= 0.5。正解の選択枚数を予測へ渡さない。CLIは保存画像用で、エンコーダーを自動ダウンロードしない。

## 実サイト検証の準備状況と次回の作業

新モデルはBot Diagnosticsの実サイト実行器にまだ接続されていない。Kaggleの重み保存だけでサイト上のモデルが切り替わるわけではない。まず公式デモの実出題を確認・保存し、保存画像を上のCLIで読むところから進める。

2026-10-06 03:11 UTCのこのworkspaceの通信なしpreflightでは、Nodeとsystem Chromiumは存在したが、Patchright依存とheadful表示環境（DISPLAY/Xvfb）が不足していた。次回はその環境状態を再確認する。preflightのscratchは`/workspace/work/live-crawl-preflight-20261006/`で、永続証拠の保存先には扱わない。

1. ブラウザと表示環境を準備し、BotDetectで文字画像、hCaptcha/reCAPTCHAで実チャレンジが表示されることを確認する。
2. 新しいrunへURL・時刻・CAPTCHA種別・テストキー使用の有無・画面・元画像のSHA・モデルSHA・生予測を保存する。未出題、取得失敗、問題表示、採点済みを区別する。
3. 正解ラベルは公開されているとは限らない。人が確認した文字列・選択タイルの正解と照合できた問題について、OCRの文字列完全一致とボード全体の完全一致を報告する。サービス側の通過結果が得られた場合は別指標として記録する。
4. 結果を検証・保存してからpushする。既存の学習データ、過去run、固定分割、結果のSHA manifestを上書きしない。

従来の検索サイト50件の再検証とは別の計画として扱う。検索対象マスターは変更していない。既存検索設定の通信なしplanは既定で49件、`--include-google`付きで50件だった。これは実CAPTCHA出題を確認した件数ではない。

直近のコード検証は149 tests中147成功・2 skip。画像ヘッドの保存特徴からtestの分類815件・tile 2,317件・203ボードの保存予測一致も確認済み。詳細なコマンドは[実験README](README.md)にある。
