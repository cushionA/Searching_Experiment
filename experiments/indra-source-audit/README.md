# Indra 配布構成の静的監査

Python 3.12以上、標準ライブラリのみ。repository rootで実行:

```sh
python3 -B experiments/indra-source-audit/audit.py
```

保存済み29一次資料のSHA256を確認し、Python loaderの定数はASTのリテラルとして読む。候補のimport・関数呼出・重み取得・推論はしない。Python loaderのallow/ignore方針とTypeScript catalogueから配布ファイルのメタデータ上の合計を算出する。結果は `benchmarks/daily-tech-20261026/static-audit.json`。実行時間は毎回変わる。

`source-manifest.json`に取得URL、固定revision、日時、bytes、hash、エラー、取得時間を保存。mutable APIの再取得では値が変わりうる。第三者source snapshotはそれぞれ同梱のApache-2.0 license、モデル関連資料は同梱のmodified OpenRAIL-Mを参照。スナップショットは実行対象ではない。

初期化時ファイル集合やCJK追加分は静的下限で、全network通信、依存込みインストール容量、RSS、認識精度の測定ではない。候補実行の隔離承認保留を解消する仕組みではない。
