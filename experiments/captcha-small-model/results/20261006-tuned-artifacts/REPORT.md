# 学習済みモデルと評価記録の永続保存

[非公開Kaggle Dataset](https://www.kaggle.com/datasets/superbigzabuton/captcha-tuned-results-20261006) version 1へ保存した。`private=true`・`ready`・version 1・ファイル名と116,371,209 bytesをAPIで照合し、version 1の個別ファイルを実際に読み戻してアップロード前のSHA-256と一致した。一括ZIPダウンロードAPIは404だったため、個別の`captcha-tuned-results.bin`取得を使った。再アップロードはしていない。

- Bundle SHA-256: `dcb5153663328b4bbe721a6e45d5633c880e5c5a7402ee148c302a94346d655d`
- 展開後328ファイル（約150.86 MB）。自身以外の全ファイルのSHAを列挙するmanifestを含み、アップロード前に全archive memberのSHA/bytes/CRCを照合した。
- 選択CRNN重みと再開状態、約14.3 MB ONNXとTorch不要CLI、S3/S4の小さいhead、S3の固定特徴、全候補の評価記録・固定分割・出典・コードを含む。
- 元画像と初期モデルは入力用private Dataset version 1に保持。S3エンコーダー約997 MBは同梱せず、公式revision/weight SHAを記録している。
- 認証ファイル・認証値は含めない。許可したファイル集合と環境内の認証値との照合をアップロード前に実施した。

Kaggleのファイル一覧から`captcha-tuned-results.bin`を取得する。`.bin`は通常のZIPなので、`python -m zipfile -e captcha-tuned-results.bin restored-results`で展開できる。Dataset全体ZIPを使う場合は、その中から`.bin`を取り出す。

CRNNとMobileCLIP2-S3/S4だけを学習した。英数字OCRは201/214（93.93%）、S3のボード完全一致は135/203（66.50%）。数字は未調整PP-OCRv6を維持する。候補・checkpointはvalidationで選び、testを選択に使わない。画像側はencoder固定のlinear head学習で、両headはmax1000 epochsに到達した。公開素材は先行比較でも使った内部holdoutで、未使用外部testやライブ認証の通過率ではない。

必須suiteは149件中147件成功、2件skip。CRNNのCPU/GPU/ONNX回答は全614件一致、画像headはcached test全3,132行・203ボードの保存予測と一致した。各詳細は同日のCRNNとimage-eval-finetuneの不変記録に保存した。
