# 2026-10-24 朝: ReScraper 配布・実装の静的監査

Python 3.12標準ライブラリだけを使う。候補のimport、生成、renderer起動、インストールはしない。

```sh
python3 -B experiments/daily-tech-20261024/audit.py --scratch /tmp/daily-tech-20261024-sources --output /tmp/daily-tech-20261024-audit
python3 -B experiments/daily-tech-20261024/audit.py --scratch /tmp/daily-tech-20261024-sources --output /tmp/daily-tech-20261024-audit --offline
```

固定コード/モデルrevisionと取得URLはスクリプト、取得時刻・bytes・SHA256・時間・エラーは `benchmarks/daily-tech-20261024/manifest.json`。公開repo人気のmetadataと版なし論文absは変動する。本文・コードはscratchだけに置き、実行しない。再取得後は固定版のhashを保存manifestと照合する。ネットワーク障害時は失敗を保存して停止し、自動修復しない。

監査のassertは固定版で観測したAST・配布配置を確かめるもの。候補機能の品質テストでも推論の代用品でもない。`--offline`は同じ取得物からの再計算確認。GPU、Kaggle、学習、モデル重み取得なし。夕方stateは読取hashをcontextへ保存しただけ。

外部候補実行の隔離保留は継続。実用機能ではないためPR対象外。
