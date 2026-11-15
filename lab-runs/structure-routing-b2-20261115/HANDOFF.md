# B2サイトマップ検証の引き継ぎ

この区切りの実測と採否判断は完了。最初に同じディレクトリのreport.md、DECISION.json、B2-CHECKPOINT.jsonを読む。作業branchはcodex-structure-routing-20261115、開始commitは8373764e70adb139a380737da36b611e8700b518。

成果物は3 runに分離してある。

- structure-routing-b2-20261115: 6サイトB1/C1/B2 cold/warm、36行。80サイト入力・B2実施台帳、総合レポート。
- structure-routing-b2-supplement-20261115: 事前固定4サイトB2のみ、8行。XML/robots原文を含む。
- structure-routing-b2-hybrid-20261115: Books/Kubernetesの保存資産併用、warmのみ2行。独立cold比較ではない。

同名のcheckpoint ZIPをlab-runs直下へ保存し、inventoryの全SHAとZIP CRCを検証した。SHA/件数はB2-CHECKPOINT.json参照。ZIPはraw/DOM・rules・inventory・logs・frozen protocol/gold・取得/採点コード・依存lockを含み、node_modulesとモデル重みは含まない。既存のCloud・local-continuationのcheckpointは保持する。

結論は任意のsitemap URL発見＋代表DOM確認＋不足分の構造リンク。TypeScriptは921 URL、別プロセスwarm3件保存、追加モデル0。全10選定サイトのstrict固定対象は11件中map4件・保存2件。アクセス条件による停止と部分的index・scope/shape実装の制約があり、汎用の完全自動巡回器としての採用は見送る。モデル追加の効果は今回検証していない。

主6サイト、補足4サイト、hybrid2サイトの結果を同一分母で合算しない。主比較の初回Python B2 coldはsource版が不明で、後続と同条件の時間比較には使わない。execution-audit.json、evaluation-audit.json、score.jsonを読む。

完了分のネットワーク再取得は不要。report.mdの採点CLIは保存物だけで動く。sitemap-run.pyは完了tupleをskipするが、hybrid runnerは新規child専用なので既存runを再実行しない。次に改善する場合は別runと事前固定protocolを作り、scope/末尾slash型推定の修正を先に測る。

ユーザーが並行変更しているexperiments/bot-diagnosticsや過去のraw・索引へ触れない。API/native会話モデル使用量はunknownを保持し、0扱いしない。
