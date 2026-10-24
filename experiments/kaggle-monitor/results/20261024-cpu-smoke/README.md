# CPU実行・ChatGPT通知の実測

2026-10-24、ユーザーの「cpuでkaggle使ってテストしてみて」により、private・CPUのみ・Internet無効・上限180秒で1件を投入した。新規投入は1回だけ。

- 実ジョブ: `superbigzabuton/issue24-cpu-notification-smoke-test` version 1、観測状態 `complete`。
- リモートmetadataでGPU/TPU無効、private、Internet無効を確認し、ソースSHA-256も投入物と一致。
- 回収した `issue24_cpu_result.json` は343 bytes。manifestとSHA-256を照合した。合成3件がすべてreview、score=nullでもterminal eventを保存できた。
- これは実行・通知の配線確認。SKUモデルや実データの精度評価ではない。
- Kaggleがタイトル由来の別slugを返したため、最初のsubmitは `submission_unknown` を保持した。保存済み応答ref/version、ソース、設定、statusを照合する `reconcile --ref` を追加し、同じ実行を追跡した。再投入はしていない。
- 初回のローカルコマンドはinline JSON引数にファイル名を渡したため、API呼び出し前に失敗した。この引数を修正後に上記1件を投入した。

通知用snapshotは専用の[状態branch](https://github.com/cushionA/Searching_Experiment/blob/codex-issue24-cpu-smoke-state-20261024/kaggle-monitor.json)へ保存し、GitHubコネクターから固定event/ref/versionを照合した。ChatGPT Automation登録後、予定された実行指示が同会話へ到着した。即時実行APIは404 Action not foundのため、同じタスクの初回予定時刻を調整した。

Automationのpeekはlast_run/latest_resultがnull、delivery_statusがunknownだった。登録やこの会話への実行指示到着を、端末への受信receiptと扱わない。`job/job.json` には履歴未確認としてretry_pendingを保存した。通知本文はterminal event時点の検証状態を伝えるため、成果物を実験合格と表現しない。常設GitHub Actionsの実運用は未有効化。

停止APIの実行テストはしていない。固定SDKと保存ログのどちらからもsession IDを解決できず、要求を送れない制限が残る。

`manifest.json` は生ログ・job・成果物・tool応答のSHA-256を持つ。実装はPR #29、条件と確認項目は `summary.json` / `verification.json` に保存した。
