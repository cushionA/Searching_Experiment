# KaggleジョブのChatGPT通知

この仕組みは、固定したKaggle ref/versionをGitHub Actionsで定期観測し、ChatGPTの時間単位Automationへ通知内容を渡す。ActionsはKaggle APIの状態を保存するだけで、issue commentや通知を直接送らない。

## 前提

- リポジトリのActions secret `KAGGLE_API_TOKEN` を登録する。Cloud環境の個人用secretとは別の保管場所である。
- リポジトリ変数 `KAGGLE_MONITOR_ENABLED` を文字列 `true` にするまでworkflowは動かない。
- workflow実行コードは既定branchからcheckoutする。`kaggle-monitor-state` がなければ空のorphan branchを作り、以後はこのbranchの `monitor-state/` 全体をrunnerへ復元・更新し、rootの `kaggle-monitor.json` にChatGPT向けsnapshotも保存する。既存ファイルを削除せず、通常のnon-force pushを使う。
- `.github/kaggle-jobs/` は初期状態では空なので、登録するまでKaggleへの問い合わせも通知も発生しない。監視対象のregistry JSONは秘密を含めず、既定branchへcommitする。

## 登録とAutomation

提出時に通知先targetを指定できる。これはChatGPT通知の設定をjobへ保存するだけで、通知登録やKaggleへの追加操作ではない。

```bash
python -B .agents/skills/kaggle-ops/scripts/kaggle_ops.py submit \
  --folder notebook-dir --output results/job-001 \
  --notification-config '{"provider":"chatgpt","repository":"OWNER/REPOSITORY","snapshot_ref":"kaggle-monitor-state","snapshot_path":"kaggle-monitor.json"}'
```

`job.json` の提出状態をreconcileした後、registryへ登録する。`register` は固定ref/versionのメタデータを書くだけで、ChatGPT Automationを登録しない。生成されたregistry JSONを `.github/kaggle-jobs/` に置き、既定branchへcommitする。

```bash
python -B .agents/skills/kaggle-ops/scripts/kaggle_monitor.py register \
  --job results/job-001/job.json \
  --registry .github/kaggle-jobs \
  --repository OWNER/REPOSITORY
```

Automationを作る前に、そのbranch/pathをGitHub connectorで実際に読み、対象の `event_id` と固定ref/versionを含む `kaggle-monitor.json` が取得できることを確認する。Actionsを有効にしただけではこの読み取り確認にならない。

```bash
python -B .agents/skills/kaggle-ops/scripts/kaggle_monitor.py automation-plan --job results/job-001/job.json
```

planのtitle、prompt、schedule、timing modeをChatGPT Automationsの作成toolへ渡す。確認済みのcreate結果を構造化JSONとして保存し、`record-automation --job ... --response ...` に渡す。次回実行を調べるときも、前回結果の確認後に結果JSONを `record-run --job ... --response ...` へ渡せる。登録記録はAutomation作成確認、run記録はtoolが返す完了結果の確認であり、どちらもユーザー端末への配送receiptではない。`delivery_status` はunknownのまま扱う。

## 実行と保証範囲

Actions workflowは毎時17分のscheduleと手動dispatchで動き、同時実行を直列化する。`poll --registry ... --output ...` は固定versionを問い合わせ、`.lab-output/kaggle-monitor/` にジョブ別の `observation.json`、追記型 `events.jsonl`、統合 `kaggle-monitor.json` を保存する。status取得失敗時は失敗を推測せず、以前に確認したterminal eventを保持してsnapshotと監査artifactを出し、workflow自体は失敗として記録する。専用branchの既存ファイルは削除せず、force pushもしない。

ChatGPT Automationの最短間隔は1時間で、Actionsの実行遅延も加わる。promptは前回の成功結果を照合して重複通知を抑え、次回の確認後にAutomationを無効化する。それでも実行失敗や取得不能時の再試行があるため、これはat-least-once相当で、利用者へのexactly-once配送を保証しない。run完了結果も端末での受信を証明しない。キャンセル照合では公開session情報が見つからない場合に失敗閉鎖し、取消し成功とは扱わない。

`record-automation` / `record-run` は、呼び出し元が渡したtool応答の整合性を検査して記録する。応答の取得元を暗号学的に認証する機能ではない。手書きのJSONを実行証拠として渡さない。後から手動停止した場合は、同じjobで `register` を再実行して更新したregistryを保存しない限り、定期監視側に停止操作の記録は渡らない。

2026-10-24の確認では、1回限りのChatGPT確認用タスクを登録し、予定時刻にこの会話へ実行指示が到着した。確認文を返したが、tool上の実行履歴は未取得・配送状態はunknownのため、端末への受信確認済みとはしない。Kaggleの実ジョブを含むend-to-end監視は未確認。この文書とworkflowだけで本番通知の有効化や実配送を主張しない。
