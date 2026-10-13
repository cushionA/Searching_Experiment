# 2026-10-14 朝：一次資料による適用境界の確認

報告: [2026-10-14-daily-tech](../../reports/2026-10/2026-10-14-daily-tech.md)

保存済み証拠の検算（ネットワーク・候補コード実行・推論なし）:

```bash
python3 -B experiments/daily-tech-20261014/audit.py
```

Python 3.12.14、PyYAML（版はaudit.jsonに記録）を使用。スナップショットSHA/bytes、RapidOCRの日本語alias×size×taskの24組、ライセンス記載hash2個、Playwrightのsnapshot/filter順を検算する。自作コードがYAMLを読む静的確認であり、RapidOCR resolverや付属テストを実行した意味ではない。

再取得する場合は新しいcheckout/出力先を用意して次の順で実行する。GitHub公開APIには既存gh認証、HF/PyPI/npmは公開GETだけを使用する。新しい観測は元証拠を上書きしない別commitに保存する。

```bash
python3 -B experiments/daily-tech-20261014/collect.py
python3 -B experiments/daily-tech-20261014/collect_models.py
python3 -B experiments/daily-tech-20261014/collect_watch.py
python3 -B experiments/daily-tech-20261014/audit.py
```

タグ/API metadataの更新があるため、再取得結果が同一とは限らない。モデルカードはAPIで取得したrevisionに固定。Playwright sourceはnpmのgitHeadをたどる。manifestは原文hashとURL・取得時間を含む。今回の資料取得は失敗0。モデル重み・第三者実行コードのインストールなし。

推論PoC前の隔離probeは `audit.json` のcommandでexit 1 (`bwrap: setting up uid map: Read-only file system`)。設定を緩めた再試行や別実行環境への切替はしていない。推論・GPU・Kaggle実行0。
