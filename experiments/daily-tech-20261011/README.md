# 2026-10-11 日次調査の再現

repo rootで実行。Python3.12以上、Node24。本実験は自作ローカルfixtureのみ。

```bash
npm ci --prefix experiments/daily-tech-20261011 --cache /tmp/daily-tech-npm-cache --ignore-scripts --no-audit --no-fund
PLAYWRIGHT_BROWSERS_PATH=/tmp/daily-tech-browsers node experiments/daily-tech-20261011/node_modules/playwright/cli.js install chromium --only-shell
DAILY_TECH_PW_ROOT="$PWD/experiments/daily-tech-20261011/node_modules/playwright" python3 -B experiments/daily-tech-20261011/run_probe.py
python3 -B experiments/daily-tech-20261011/verify.py
```

再実行は保存結果を更新するため、比較時は別checkoutへコピーする。保存された今回結果の再計算だけなら最後のverifyのみ。
source metadataは`collect_sources.py`、`collect_supplement.py`、全URLは`source-urls.json`。
再取得時の内容・stars・PR HEADは変わり得る。original JSON responseのsha256と取得時刻をsnapshotへ記録した。
PoCは固定protocolとrunner hash、各mode30反復、HTML hash安定、既存HTML parser結果、live locatorを比較。
closed shadow・iframe・inert templateの制限を含む。実サイトcoverageや速度向上のbenchではない。
RUSAGE_CHILDRENは最大RSSであり、browser全process同時合算メモリではない。
モデル/GPU/Kaggleなし、既存SKU関連実装や収集への変更なし。
