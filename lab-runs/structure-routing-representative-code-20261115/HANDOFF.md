# 代表HTMLからの巡回コード生成・引き継ぎ

実験は完了。ブランチは `codex-structure-routing-20261115`。取得は再実行せず、保存物を使って検証・集計できる。

## 保存物

- `targets.jsonl` / `protocol.json`: 事前選定した6サイトと条件。
- `sites/ID/bootstrap-request.json`, `sampling-answer.json`: 入口代表からのサンプル選択。
- `sites/ID/code-request.json`, `crawl.js`, `generation-answer.json`: 代表HTMLから生成したコードと根拠。
- `artifacts/`: source HTML、描画後DOM、途中状態、観測リンク、通信・文書予約の台帳。
- `results.jsonl`: 修正前12試行。`repair-run/results.jsonl`: 実行器のAPI修正後4試行。
- `summary.json` / `final-evaluation.json`: 採用した12組の結果と、元の16試行の採点。
- `model-jobs/` / `model-usage.json`: モデル要求・回答・既知ジョブ数と不明なnative使用量。
- `evaluation-policy-frozen.json`: 事前固定の型判定。正解候補と選定記録は生成・取得終了後にのみ同梱した。

## クラウドで読む

```sh
git checkout codex-structure-routing-20261115
git pull --ff-only origin codex-structure-routing-20261115
python3 -m zipfile -e lab-runs/structure-routing-representative-code-20261115-checkpoint.zip .
python3 -B lab-runs/structure-routing-representative-code-20261115/finalize.py
```

ZIPのCRCとinventory SHA256は書出し時に全件検証する。アーカイブ外の `SITEMAP-AGENT-CHECKPOINT.json` にZIPのSHA256・検証結果を記録する。ZIPはstructure-routingコードとrunを含む。jse.labのCLI変更・テストは同じGitブランチのcheckoutを使用する。

`finalize.py` は同梱の `evaluation-policy-frozen.json` を読み、元のrefに記録したSHA256と照合する。Windowsの非公開評価フォルダーがない環境でも再集計できる。元のpolicy・候補・採点を変更しない。初期選定用のevaluate.pyを再実行して候補集合を作り直す必要はない。

## 実行器を検証する

既存のプロジェクトPython環境を使用する。`dependency-locks/bot-diagnostics` のpackage.json/package-lock.jsonを `.deps/bot-diagnostics/` に置き、`npm ci --prefix .deps/bot-diagnostics` で同じNode依存を導入する。

```sh
python3 -B -m jse.lab structure-routing --method representative-code --run lab-runs/structure-routing-representative-code-20261115 --action verify
python3 -B -m jse.lab structure-routing --method representative-code --run lab-runs/structure-routing-representative-code-20261115/repair-run --action verify
```

3件のlocalhost fixture、6件のCLIテスト、生成コード6本の構文確認が成功。全lab suiteは87件中2失敗・5 skip。失敗は既存のWindows Camoufox pathとNO_PROXY期待値で、詳細は `test-lab.log`。

今回のrunに新たなexecute --liveは不要。追加実験は別runを作り、対象・条件・LinuxのChromeパスを明示する。失敗した未知のページ型をそのまま汎用対応済みとは扱わない。既存rawデータ・今回の失敗記録を削除しない。
