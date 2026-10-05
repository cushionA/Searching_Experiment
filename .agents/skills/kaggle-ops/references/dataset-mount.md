# Datasetマウント失敗

`mount data: ERRORED_MOUNTING_DATASET`、`retry budget exhausted`、`dataset loading failed` がfailure_messageにある場合、Notebook開始前の入力準備段階で失敗している。モデルのOOMや推論コードの不具合として扱わず、対象ref/versionのstatusとメッセージを保存する。

## 確認と修復

1. versionを固定したstatusを取得し、ユーザーのログとAPIの観測を区別して保存する。稼働中または状態不明なら、既存ジョブを新規提出へ置き換えない。
2. 入力Datasetのversion、private設定、ready状態、実際のファイル一覧・bytesを確認する。readyは取り込み処理の状態であり、Notebookのマウント成功を証明しない。ZIPがアップロード後に大量のファイルへ自動展開されていないか確認する。
3. 元のアーカイブとmanifest、ラベル、画像SHAを保持する。マウント負荷を減らす修復候補として、同じZIP bytesを `evaluation_bundle.bin` 等の名前で新versionへ保存し、Notebook内でSHA検証後にZipFileとして展開する。拡張子変更だけで非展開になると決めつけず、Kaggle上のファイル一覧が期待する単一アーカイブと一致することを確認する。
4. 過去Dataset versionを削除しない。新versionのready、識別情報、private設定、ファイル名とbytesを保存する。読み込みコードはマウントパスの旧配置と `/kaggle/input/datasets/owner/slug` の配置を扱い、複数候補を曖昧に選ばずエラーにする。SHA・パス・symlinkの検査を維持する。
5. 既存ジョブが失敗済みで、試行がユーザーの許可とGPU予算に収まる場合に、修復した入力配置で1回だけ新しいNotebook versionを送信する。新しいjobディレクトリへ保存し、以前のjob.jsonを上書きしない。ファイル数や容量が根本原因だったという結論は、修復後の観測と分ける。
6. 開始後の入力探索、アーカイブSHA検査、画像展開、モデル開始をflushして記録する。マウント自体が失敗した場合、Notebook内のログはまだ書かれないため、status APIのfailure_messageを使う。

## 2026-10-06 JSTの事例

- Notebook: `superbigzabuton/captcha-diverse-models-20261005-1336` version 1。
- 入力Dataset: `superbigzabuton/captcha-diverse-eval-20261005-1336` version 1、private、ready。
- エラー: `ERRORED_MOUNTING_DATASET`、30回のretry後に `rpc error: code = Internal`、`dataset loading failed 2 failover attempts`。
- 元ZIP: 539,003,432 bytes、SHA-256 `b75cd2b6fe4e367f599255608123ec3cf36b15cdce0181c3022a6b27468a14a1`。参照画像15,434パスとmanifestを含む。Datasetの実ファイル一覧では `evaluation_bundle/data/...` への自動展開を確認した。
- GPU使用済み時間: 提出前と失敗確認後はいずれも580.879秒。今回の失敗についてモデルの処理時間は計測できていない。
- 証跡: `lab-runs/captcha-public-more-20261005T1327Z/gpu-eval/mount-failure-001/` のstatus、quota、Dataset status、ファイル一覧、diagnosis。

修復では元ZIPのbytesを変えず、拡張子と読み込み側の候補探索を変更する。マウントエラーの原因をファイル数や入力容量に確定する証拠はない。

Dataset version 2について、private/readyと `evaluation_bundle.bin` 1ファイル、539,003,432 bytesをAPIで確認した。Notebook version 2は修復したコードで受理され、`gpu-eval/job-002/job.json` を使って監視する。提出受理はマウント成功や評価完了を証明しない。
