# 公開CAPTCHA文字OCRの微調整評価

## 結果

同一の614枚で比較したところ、validationで選んだCRNN epoch 37が567枚を完全一致で認識しました（92.35%、CER 1.585%）。未微調整の同じCRNNは162/614（26.38%）でした。Pilot epoch 19のtestは556/614（90.55%、CER 2.017%）です。Pilot epoch 19と拡張runの候補はvalidationだけで比較し、epoch 37（294/307完全一致、CER 0.922%）を選択しました。epoch 19は286/307、CER 1.441%です。test結果は選択に使っていません。

| 固定testの出典・文字種 | 枚数 | common_old | common_old autocontrast | PP-OCR v6 small | PP-OCR v6 medium | 未微調整CRNN | 選択CRNN epoch 37 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Kaggle CAPTCHA Images v2・英数字 | 214 | 178 (83.18%) | 177 (82.71%) | 107 (50.00%) | 99 (46.26%) | 148 (69.16%) | **201 (93.93%)** |
| Project Sloth CAPTCHA images・数字 | 400 | 303 (75.75%) | 286 (71.50%) | 372 (93.00%) | **380 (95.00%)** | 14 (3.50%) | 366 (91.50%) |
| **合計** | **614** | **481 (78.34%)** | **463 (75.41%)** | **479 (78.01%)** | **479 (78.01%)** | **162 (26.38%)** | **567 (92.35%)** |

正解率は大文字小文字を区別した文字列完全一致です。CERは生の文字列間の編集距離を参照文字数で割り、予測・ラベルの正規化はしていません。4種の既存OCR結果は、614件すべてのID・画像SHA-256・ラベルを固定testと照合してから集計しました。

この組み合わせでは英数字画像に微調整CRNN、数字画像にPP-OCR v6が強い結果です。先行する2,000枚全体の評価でPP-OCR v6 smallとmediumは同率94.55%となり、軽量なsmallを先行候補にしていました。同一の固定testに限るとsmall 93%、medium 95%です。この部分集合の差を理由に先行モデル選択を変更していません。

先行するKaggle英数字画像1,070枚全体ではcommon_old autocontrastが85.98%でした。同じ固定testの214枚では82.71%、微調整CRNNは93.93%です。

## データ・実行

公開CAPTCHA画像3,070枚を、画像バイトのSHA-256でグループ化し、出典ごとにtrain 2,149枚、validation 307枚、test 614枚へ固定分割しました。重複画像はなく、ハッシュのfold越境もありません。ID・ラベル・出典・画像ハッシュを含むmanifestとsplitはそれぞれ[data/input-manifest.json.gz](data/input-manifest.json.gz)、[data/split.json.gz](data/split.json.gz)に原バイトを保ったまま圧縮してあります。出典・ライセンス記録は[data/source-provenance.json](data/source-provenance.json)を参照してください。

初期重みは `Graf-J/captcha-crnn-finetuned` revision `8ca7bfadc2608b007b5cafe20a7d0c29888a5cbb` です。このrevisionは実行configと[evaluate_crnn_text.py](../../evaluate_crnn_text.py)の定数で照合しました。初期重みSHA-256は `df4c6fc59d1a6c0e3c7b7ef4ae9466a55285df18877f104b58a9c05eaffb26e5`。Tesla T4、PyTorch 2.11.0+cu128で実行し、全画像をグレースケール化して150×40へ縮小、固定語彙でgreedy CTC decodeしました。pilotは20 epochを完了しepoch 19を選択。拡張runは上限100 epoch、学習時間上限1,200秒、validation patience 5で42 epoch後に停止し、epoch 37を選択しました。

Kaggle notebook version 1の拡張試行は、学習器のguardがepoch上限20のままで、推論前に終了しました。修正版ではguardとエラーメッセージの2行だけを20から100へ変更し、1,200秒上限は維持しました。3回の実行のnotebook、Kaggleのjob/version、quota snapshot、monitor status、continuation receiptは[trials/](trials/)と[notebooks/](notebooks/)に保存しています。重み・optimizer state・元画像はGit記録に含めていません。

選択重みはprivate Kaggle notebook version 2の出力 `crnn-finetune-result/best.safetensors` に保存されています。SHA-256は `cce95d7cd320d332fecb606b39ccdcf269eb9794093bea7e2c0a4e4b30c11691`。場所は[captcha-ocr-ft-extended-20261006 version 2](https://www.kaggle.com/code/superbigzabuton/captcha-ocr-ft-extended-20261006)です。

## ONNXと再現性

epoch 37をONNX opset 17 / IR 8、dynamic batch、external dataなしで書き出しました。グラフは14,287,573 bytes、SHA-256 `63f750d945030a32f43c088e869db436e0dda1c321651e3f849f9df0c6b6733d`。ONNX Runtime CPUのbatch 1・32とも614件のPyTorch回答と一致し、予測JSONL SHA-256は同じ `2ec47a8a0acd50f4281a7d2e2f39bbbbb6b0c14202306c7e2edd111ae3c1c122` でした。前処理はbitwise一致、logitの最大絶対差はbatch 32で0.00145721、argmax token差は0です。検証記録、[README.txt](onnx-runtime/README.txt)、runtime CLIと圧縮済み予測を[onnx-runtime/](onnx-runtime/)に保存しています。14 MBのグラフ本体はGitへ含めていません。速度比較はしていません。

`python3 -B audit_record.py`をこのディレクトリで実行すると、固定分割、予測のID・ラベル・画像SHA、完全一致/CER、validationだけでの候補選択、既存4モデルとの同一614枚比較、CPU/GPU/ONNX回答一致、保存コードのSHAを再検証できます。`MANIFEST.sha256`は自分自身を除く全ファイルを対象にします。

## 制約

両公開コーパスは先行するモデル選択ベンチマークでも使われています。このtest foldは今回の微調整中の学習・checkpoint選択との漏洩を避けますが、新規収集した独立外部testではありません。初期重みの学習データとの重複は未確認です。これはオフラインCAPTCHA文字画像の評価で、ライブCAPTCHA通過率や一般的なスクリーンショットOCRの性能を示しません。ライセンス記述は入力manifestの記録を引き継ぎ、権利状態は独立確認していません。
