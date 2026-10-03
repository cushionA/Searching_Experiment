# 検索サービスの調査対象と検証結果

[調査対象表](targets.md)は固定の50サイト。編集・処理用に[CSV](targets.csv)と[対象マスターJSON](../../experiments/bot-diagnostics/search-targets.json)を保存した。検索語・ブラウザ条件・取得結果はマスターに入れず、検証ごとに保存する。元のsearch-services.jsonは今回の実行証拠として保持している。

[今回の検証結果表](results/20261003-headful-patchright/results.md)は従来と同じ9列・50行で、表だけのファイル。同じフォルダーにCSV、詳細JSON、conditions.json、対象マスターのコピー、SHA256.jsonがある。検証条件と表内の履歴の扱いはconditions.jsonで確認できる。

表のquery・品質・画面は最新観測。robots/home/CDNの欄には旧段階の履歴を含む。今回は通常ブラウザで38サービスを再検証し、Wiby/YouCareのquery観測とAPI等10対象の公開トップ観測は前段階から採用した。OneSearchはYahoo Search、PeekierはKagiの別段階観測で、元サービス本体のSERPやクエリ引継ぎを実証したものではない。外部報告は詳細JSONのexternal_*で分離している。

## 次の検証を保存する

新しい条件での取得は、新しい証拠runへ保存する。既存runを別条件でresumeしない。対象JSONを参照してその検証専用の検索語・ブラウザ・待ち時間・操作条件を設定し、取得後に結果JSONと9列の個別結果表を用意する。今回専用のsearch-summary.pyが出す固定の説明文を、別条件の説明として流用しない。

[条件テンプレート](conditions-template.json)に実際に使った検索語・条件を記録して、次のコマンドで保存する。--run-idを省略するとJSTの保存日時を含む新しいフォルダー名を作る。既存フォルダーがあればエラーで止まり、上書きしない。この保存コマンドは通信や再検証を行わない。

```bash
python3 -B experiments/bot-diagnostics/save-search-tables.py snapshot \
  --summary lab-runs/NEW_RUN/search-services-summary.json \
  --report lab-runs/NEW_RUN/report.md \
  --conditions lab-runs/NEW_RUN/conditions.json
```

任意の--run-idで条件名を付けられる。保存先はdocs/search-services/results/RUN_ID。毎回の結果表には、その時点の対象マスターのコピーとhashを含めるので、あとから対象リストが変わっても過去の対象を確認できる。条件の検索語と結果JSONの検索語が一致せず、または全対象IDがそろわない場合も保存を止める。表とJSONの検索状態・候補/公式件数・品質ラベル・URL/DOM証拠も照合し、別検証の表の取り違えを防ぐ。未実測サイトは結果JSON・表に未実測と残す。

今回の原HTML・台帳・全履歴を含む証拠は、別のsearch-services-20261003-browser-observation.zipに保持している。表の保存bundleは対象・結果・条件・保存コードと、表から参照する画面を含む。
