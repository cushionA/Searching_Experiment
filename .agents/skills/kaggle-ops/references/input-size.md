# 入力サイズと転送

Notebookのコード、入力Dataset、回収する出力は別々に容量を管理する。MBは1,000,000 bytes、MiBは1,048,576 bytesとして、元のbytesも保存する。

## 提出前の確認

1. `kernel-metadata.json` の `code_file` が指す最終ファイルを生成し、UTF-8 bytesを計測する。画像、巨大なラベル一覧、manifest、重み、保存済みセル出力が混ざっていないか確認する。
2. このリポジトリでは最終コードを1 MiB未満に抑える。これはローカルの事前ガードであり、Kaggleの現行サービス上限を確認した記録とは分ける。APIがサイズ超過を返した場合は、応答の上限・実bytes・対象ファイルを記録し、その上限も守る。
3. gzipで圧縮してもbase64で約4/3に増える。圧縮前だけで判断せず、埋め込み文字列と最終コードを計測する。コードサイズのガードに収まらない場合は、入力をDatasetへ分離する。ガードを引き上げて再送しない。
4. 100 MB超の入力はprivate datasetへ置く。100 MB以下でも大きなmanifestや画像は分離してよい。Notebookにはdataset参照、設定、必要な小さいコード、manifest・アーカイブのSHAだけを残す。
5. Datasetがreadyであることとversionを記録し、`dataset_sources` で接続する。実行時にDatasetからmanifestを読み、SHAを確認してから必要な画像を使う。更新時は元のversionを保持する。

必要に応じてアーカイブを分割し、各ファイルのbytes・SHAと展開後のファイル数・容量を記録する。KaggleがZIPを自動展開する場合もあるため、ZIPと展開済みディレクトリを扱える構成にする。元ZIPが残っていなければZIPのSHAを再確認したとは扱わず、manifestと各入力ファイルのSHAで照合する。

入力や重みは `/tmp` 等へ、回収する小さい結果・設定・ログは `/kaggle/working` へ置く。helperの出力回収には合計1 GBの予算がある。これは入力Datasetの制限ではない。

## 今回の確認例

2026-10-06 JSTに記録。対象は `lab-runs/captcha-public-more-20261005T1327Z/` の公開画像評価。

|対象|bytes|確認できたこと|
|---|---:|---|
|`public_full.json`|7,878,203|大きなmanifestもNotebookを膨らませる入力になる。|
|同manifestのgzip＋base64|1,936,312|gzip level 9、mtime 0で圧縮すると1,452,234 bytes。base64部分だけで1 MiBガードを超える。最終Notebookのサイズではない。|
|提出した`public_models_v2.ipynb`|36,278|manifest本体を埋め込まずSHAだけを残し、version 1の提出が受理された。|
|`evaluation_bundle.zip`|539,003,432|約539.0 MB / 514.0 MiB。private datasetへ分離し、version 1がreadyになった。|

提出NotebookのSHA-256は `4999c1d4c098bb11d2a5569c3a0a26ab5bdd5206500addcb347ea9207aaeb8cf`。入力ZIPのSHA-256は `b75cd2b6fe4e367f599255608123ec3cf36b15cdce0181c3022a6b27468a14a1`。

元のサイズ超過応答は保存済みの証跡から確認できない。manifestの容量は、現在のファイルから再計測できる値として記録した。今回の539 MB入力がKaggleの容量上限で拒否されたとは扱わない。

根拠はrun内の `gpu-eval/notebook-verification.json`、`gpu-eval/dataset-upload/evaluation_bundle.verification.json`、`gpu-eval/dataset-create.json`、`gpu-eval/dataset-version.json`、`gpu-eval/submission.json` にある。

その後の稼働中ログにはReadTimeoutがあり、成果物を取得できていない。入力容量との因果関係は未確認。statusやGPU枠の消費だけでは、処理段階・停止・モデルの成功を証明できない。次回は入力探索、コピー、SHA検証、モデル取得、推論の各段階で開始と終了をflushし、ファイル数・bytes・経過時間を小さいローカル記録にも残す。
