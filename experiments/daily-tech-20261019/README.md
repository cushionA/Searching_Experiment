# 2026-10-19 朝調査の証拠

外部コードは実行せず、公開一次資料をテキスト取得・保存した。各planのURL、観測時刻、bytes、SHA256、取得エラーをbenchmarksのmanifestへ残す。collect.pyは前朝の自作収集器を日付変更して使用。1資料3MB上限、35秒timeout。出力先が存在すると拒否するので再取得時は新しいディレクトリを指定する。

```sh
python3 -B experiments/daily-tech-20261019/collect.py --plan experiments/daily-tech-20261019/sources-plan.json --output /tmp/daily-tech-20261019-new-sources
python3 -B experiments/daily-tech-20261019/audit.py
```

supplement-plan.json / correction-plan.jsonも同じ形式。補足のExtractorJSON.javaは404、PR771で確認したExtractorJson.javaへ訂正した。コードをimport/compile/実行する手順ではない。

取得wallはsources 4.690774093秒、supplement 3.449390659秒、correction 0.820874215秒。監査はpayload整合性とmetadataの構造読み取りだけで、モデル・ブラウザ性能試験ではない。入力は公開API/ソース/文書のみ。既報と進行中作業のsnapshotはbenchmarks/context.jsonとduplicate-check.json。夕方stateはgit showのみ、4ファイルのhashとqueue数を記録し変更しない。

判断・制約・想定差は `reports/2026-10/2026-10-19-daily-tech.md`。未知コードの実行承認をこのREADMEで代替しない。
