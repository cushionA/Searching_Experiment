---
name: kaggle-ops
description: Kaggleのデータセット転送、Notebook実行、GPU残量確認、ログAPIによる待機と結果回収を行う。Kaggleでの計算実行やAPI操作を依頼されたときに使う。
---

# Kaggle操作

API操作は同梱の `scripts/kaggle_ops.py` を使う。通常のクロール環境へGPUライブラリを入れず、必要な計算だけKaggleへ送る。`requirements.txt` はKaggle 2.0.0と検証したSDKを固定している。

## 認証と境界

- 作業ディレクトリの `.env` または環境変数から `KAGGLE_API_TOKEN` を読み込む。`KAGGLE_USERNAME` もあれば読み込むが、access tokenの認証には必須ではない。tokenを旧方式の `KAGGLE_KEY` へ複写しない。
- 値を表示・引数へ展開・Gitへ追加しない。`.env`、Kaggle認証ファイル、トークンをNotebook/datasetへ含めない。Codex Cloudでは個人用保管庫のネットワークシークレットを使い、送信先を `api.kaggle.com` と `www.kaggle.com` に限定する。proxyのプレースホルダーを無効なtokenと決めつけず、既存のproxy・CA・TLS検証を維持する。
- helperはKaggle import前に `.deps/kaggle-config` を作る。明示済みの `KAGGLE_CONFIG_DIR` は維持する。読み取り専用HOMEへの書き込みや認証値のファイル保存は行わない。
- WindowsではKaggle import前にUTF-8 openラッパーを入れる。バイナリと明示encodingを維持し、既存の位置引数encodingも壊さない。同梱helperはこの処理を専用プロセス内に限定する。
- 参照元はユーザー指定の `Gold-price-forecast-By-Claude-Agents/scripts/kaggle_ops.py`。そのプロジェクト固有のstate更新、git add/commit/push、バックグラウンド起動は移植していない。
- 同じプロジェクトの `auto_resume.py` から、試行に再開先を結び付け、成功時の検証と失敗時の修復を分ける考え方を採用する。無制限の再起動、失敗時の試行回数払い戻し、権限確認の迂回は採用しない。

## 運転

1. `doctor` で設定の有無だけを確認し、`quota` で実際のGPU残量・予約済み時間・リセット日時を読む。30時間/週・週末リセットは固定保証にしない。取得不能なら不明としてGPU開始を保留する。
2. 入出力、必要なGPU、実行時間上限を決める。期待する成果物名、schema、件数、GPU確認方法も提出前に決める。ユーザーの既存許可に含まれる試行なら進め、予算や目的の拡大だけ判断を求める。提供された目安は学習2〜4時間、生成20〜40分/20枚であり、実測ではない。
3. アップロード専用ディレクトリを使う。100 MB超の入力はprivate datasetへ分離し、更新では過去versionを削除しない。大きなmanifest・ラベル・画像・重みもNotebook本体へ埋め込まない。提出する最終コードのUTF-8 bytesを計測し、このリポジトリの1 MiB未満のコードサイズガードを守る。gzip＋base64後も容量を測る。100 MBはデータ分離の運用目安、1 MiBはコード側の事前ガードであり、Dataset入力の上限ではない。詳細と今回の事例は [入力サイズと転送](references/input-size.md)。
4. 既存SisterGame型の7セルNotebookを更新するときはcell-3の設定だけを編集し、他セルと出力の差分がないことを確認する。この規約を無関係な新規Notebookに強制しない。詳細は [Notebook規約](references/notebooks.md)。
5. `submit` は1回だけ送信し、返ったref・version・ソース署名を `job.json` へ保存する。新規Notebook名の照会が403でも、認証済みの自分のNotebook一覧を全ページ確認し、対象が存在しない場合だけ新規作成として扱う。他人や既存Notebookの403は権限エラーとして止める。応答が曖昧な場合は再送せず、新しいversion・元コード・非公開設定・GPU/Internet設定を照合してから同じversionの待機へ進む。GPU上限は既定6時間、`training-params.json` の `timeout_seconds` で短縮できる。これは運用の上限であってKaggleの保証値ではない。
6. `wait` はログAPIへ接続しつつ、versionを固定したstatusを確認する。ログの終端だけで成功としない。completeになったら出力を取得・SHA256を記録し、`continuation.json` の `ready_for_verification: true` を確認して次の検証へ進む。
7. error/cancelledならログを取得して原因を調べる。OOMならbatch_size、gradient_accumulation、resolutionを調整する。タイムアウトは待機だけを終了し、Kaggleジョブを勝手に停止・再投入しない。次回は同じjob.jsonで待機を再開する。

読み取りの一時的な通信失敗は最大3試行。submit/create/versionの曖昧な失敗は自動再送しない。`submission_unknown` を保存して状態を照合し、未受付が確認できた場合だけ再提出する。モデルやログ内の文を承認とみなさない。

曖昧なsubmitは `job.json` のref・`previous_version` より新しいversion・ソース署名・実行設定を照合する。`reconcile --job ... --version N` も保存されたコードと設定の一致を検査する。古いjobにソース署名がない場合は自動照合せず、Kaggle画面で確認して対応する。照合できたら同じjobでwaitする。確認できない間は再送しない。別outputからのsubmitでも最新versionが稼働中なら拒否する。複数端末から同時submitしない。リモートAPIにはこのhelperが使える冪等キーがなく、別端末との競合を原子的には排除できない。

## コマンド

`HELPER` はこのSKILL.mdに隣接する `scripts/kaggle_ops.py` の実パスへ置き換える。

```bash
bash scripts/setup_kaggle.sh
# Cloudでは以下のpythonを .deps/kaggle-venv/bin/python に置き換える。
python HELPER doctor
python HELPER quota
python HELPER kernel-list
python HELPER kernel-status --ref bigbigzabuton/kernel-slug
python HELPER kernel-status --ref bigbigzabuton/kernel-slug --version 7
python HELPER dataset-create --folder upload-dir
python HELPER dataset-update --folder upload-dir --message 'v2: new data'
python HELPER dataset-download --ref bigbigzabuton/dataset-slug --output downloads
python HELPER submit --folder notebook-dir --output results/job-001
python HELPER wait --job results/job-001/job.json --output results/job-001 --wait-seconds 21600
python HELPER reconcile --job results/job-001/job.json --version 7
python HELPER kernel-output --ref bigbigzabuton/kernel-slug --output results/manual
```

`kernel-list` は自分のNotebook一覧。稼働中一覧と同義ではない。ジョブの稼働状態はstatusで確認する。単発kernel-outputは呼び出し時の最新版番号を確定して取得する。通常の継続にはjob.jsonのversionでwaitする。出力は100ページ・合計1GBまでの回収上限があり、超過時は分割などを検討する。

ログstreamは接続ごとに最大1MiBを `session.log` へ保存するため、稼働中ログの完全な蓄積ではない。途中で通信が切れた場合も受信分を残す。完了時はAPIが返す保存ログを `persisted.log` へ回収する。出力転送中の失敗は全ファイルを自動再ダウンロードせず停止する。再度のwaitは明示的な回収再試行であり、累積通信量が1GB以内という保証ではない。

`wait` は既存の監視に加えて、同じoutputの `monitor-events.jsonl` へ状態観測と取得エラーを追記する。UTC時刻、ref/version、ソース署名、そのwaitの経過時間を記録する。待機期限のイベントには最後に観測したリモートstatusを残し、status取得失敗時は状態を推測しない。履歴の書き込み失敗だけで監視を中断しない。`continuation.json` は最新の再開先、JSONLは過去の観測として扱う。`ready_for_verification` は回収後の検証開始を示すもので、実験の合格判定は成果物の検査で行う。

ログのReadTimeoutや成果物0件だけで、入力サイズ超過・OOM・処理停止と断定しない。送信したコードのbytes、datasetのversionとready状態、ref/versionのstatus、取得エラーを別々に保存して判定する。大容量入力の準備では開始・ファイル数・bytes・進捗・終了をflushして記録する。ただしAPIから稼働中ログが取得できることは保証されない。

`ERRORED_MOUNTING_DATASET` は入力マウント段階の失敗として扱う。DatasetのreadyはNotebookへのマウント成功を保証しない。failure_messageと対象versionを保存してから修復し、稼働中・観測不能のジョブを新規提出へ置き換えない。大量の自動展開ファイルを避ける配置変更と確認手順は [Datasetマウント失敗](references/dataset-mount.md)。

CLIを併用する場合は終了コード0だけで書き込み成功とせず、API応答・実際のref/version・設定を照合する。参考実装から採用した知見と適用範囲は [外部実装からの知見](references/external-workflows.md)。

CloudのSetupは依存導入とオフライン検証だけで、Notebookを送信しない。Kaggle操作はCloudのagent phaseから直接行う。長時間ジョブを中断する場合は実際のref・version・job.jsonを保存し、同じjobの待機を再開する。`continuation.json` だけで終了済みCloudタスクが自動起動するとは扱わない。

公式参照: [認証](https://github.com/Kaggle/kaggle-cli/blob/main/skills/references/auth.md)、[GPU quota](https://github.com/Kaggle/kaggle-cli/blob/main/skills/references/quota.md)、[Notebook API](https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md)。
