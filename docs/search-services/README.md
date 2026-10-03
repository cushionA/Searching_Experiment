# 検索サービスの調査対象と検証結果

[調査対象表](targets.md)は固定の50サイト。編集・処理用に[CSV](targets.csv)と[対象マスターJSON](../../experiments/bot-diagnostics/search-targets.json)を保存した。検索語・ブラウザ条件・取得結果はマスターに入れず、検証ごとに保存する。元のsearch-services.jsonは今回の実行証拠として保持している。

[2026-10-03の検証結果表](results/20261003-headful-patchright/results.md)は従来と同じ9列・50行で、表だけのファイル。同じフォルダーにCSV、詳細JSON、conditions.json、対象マスターのコピー、SHA256.jsonがある。検証条件と表内の履歴の扱いはconditions.jsonで確認できる。

表のquery・品質・画面は最新観測。robots/home/CDNの欄には旧段階の履歴を含む。10月3日は通常ブラウザで38サービスを再検証し、Wiby/YouCareのquery観測とAPI等10対象の公開トップ観測は前段階から採用した。OneSearchはYahoo Search、PeekierはKagiの別段階観測で、元サービス本体のSERPやクエリ引継ぎを実証したものではない。外部報告は詳細JSONのexternal_*で分離している。

## 2026-10-04 JST: GCP local retry

検索語は「東京都大田区 池上本門寺 松濤園 公開日」。10方式×固定50サイトを同じGCP出口IP `136.67.75.1` で確認し、検索ルートあり40件とhomepage-only 10件を分けて記録した。初回の500セルに対して、403・429・Challenge・その他4xxを除き、通信・実行エラーと一時的な5xxの90セルだけを各1回再試行した。通信失敗とadapter準備失敗は初回80件から最終66件になった。

下表は各方式の詳細JSONにある最終集計。関連結果・明示0件は検索ルート40件の値で、通信・準備失敗は全50件、再試行数は選定したセル数。過去の10月3日観測は上記の別runに保存し、この新しい条件の集計と混ぜていない。

|方式|関連結果|明示0件|通信・準備失敗（全50件）|再試行セル|
|---|---:|---:|---:|---:|
|[Camoufox](results/20261004-gcp-local-retry-camoufox/results.md)|10|2|2|3|
|[Impit](results/20261004-gcp-local-retry-impit/results.md)|4|1|1|2|
|[Obscura](results/20261004-gcp-local-retry-obscura/results.md)|5|2|18|22|
|[Obscura no-render](results/20261004-gcp-local-retry-obscura-no-render/results.md)|4|2|14|18|
|[Obscura patched](results/20261004-gcp-local-retry-obscura-patched/results.md)|5|2|3|4|
|[Obscura stealth](results/20261004-gcp-local-retry-obscura-stealth/results.md)|2|2|21|23|
|[Patchright](results/20261004-gcp-local-retry-patchright/results.md)|11|2|1|4|
|[Playwright baseline](results/20261004-gcp-local-retry-playwright-baseline/results.md)|10|2|1|4|
|[Rebrowser Lightpanda](results/20261004-gcp-local-retry-rebrowser-lightpanda/results.md)|3|2|4|8|
|[wreq-js](results/20261004-gcp-local-retry-wreq-js/results.md)|4|1|1|2|

証拠ZIP `gcp-search-20261004-local-retry-final-checkpoint.zip`のSHA256は `01915bc6847f48a0d23daa609d1567fbf7090e9d0885b24a0f3555962a1f4063`。ZIP内のrun相対パスは `external-runs/gcp-search-20261004-local-retry-new-ip-af8a1717/` から始まる。raw証拠ファイルはGitで追跡せず、`results.json`の元の実行パスは出所確認のため保持する。

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
