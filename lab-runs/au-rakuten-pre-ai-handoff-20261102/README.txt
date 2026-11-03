最初に IMPLEMENTATION_HANDOFF.html または IMPLEMENTATION_HANDOFF.txt を読んでください。
Gitブランチ: handoff/au-rakuten-pre-ai-20261103
rule-contract.json は機械可読の判定条件です。
fixtures/fixtures.json は13個の実装動作確認例で、独立した商品同一性の正解データではありません。
実データfixtureの comparison_pair で比較対象を選択してください。cross-pair例には参照用の4レコードが含まれます。
fields.path は商品オブジェクトからの相対位置、comparison_mode は原本との照合方法です。
source.export_file はこのディレクトリからの相対パスです。
evidence/products のJSONはセッション等のキーを除いた配布版です。原本hashと配布版hashはevidence/manifest.jsonで区別しています。
evidence/reports の出典パスは元の調査環境の表記も含みます。原本ファイル名はmanifestで配布ファイルへ対応付けられます。
validation.json は資料・抜粋の検証結果です。本番組み込みや判定精度のテスト結果ではありません。

ブランチ取得後、リポジトリのルートから次を実行すると、同梱ファイルだけで検証できます。
  python3 -B lab-runs/au-rakuten-pre-ai-handoff-20261102/verify_handoff.py
58商品の配布ファイルhashと、fixtureの実データ抜粋59箇所を確認します。外部通信や元の未配布調査ファイルは不要です。
