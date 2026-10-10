# V12 closed-review 独立監査

新しい外部evaluation directoryでgrade.pyを再実行した。保存済みgradingの `comparisons.json`、V12と累計250ケースのsummary/case-results、計5ファイルがすべてbyte一致し、freezeに登録された525ファイルのSHAも一致した。監査スクリプトはlabelsを読まず、採点はcheckout外のevaluation directory内で実行した。

新50ケースは41/48（85.42%）、採用10・除外34・保留6、accepted-row precision 10/10、row-match recall 10/14。累計250ケースは216/241（89.63%）、採用74・除外148・pending27・未評価1、precision 74/74、recall 74/87。累計exportは74リンク・72楽天SKU。累計値は前200ケースを含み、新50の成績と区別が必要。人手検証済みではなく、source groupも共有されているため独立source-group holdoutとは呼べない。

lineage記録とsource manifestは、V11の50 matching回答・15 signature回答をraw再利用し、新規matching/signature taskは0、fresh reviewは2、同一内容でのreview再利用は48と記録している。source hashは不変。bounded contents proofは新規10リンクのうち3件にあり、リンクSHAとproofの対応、case/rowが新規および累計exportに載ることを確認した。根拠復元はローカルsidecarで、モデル回答本文を再解釈したものではない。残る7件に同じsidecar証明があるとは主張しない。

新規reviewのdispatch metadataは2 taskを記録し、requested modelはgpt-6-luna。actual modelVersionとusageはnullであり、dispatch時刻はcoordinator記録でprovider側実行時間の測定値ではない。prepare auditは回答ファイル生成後に更新したが、2件の回答本文は開かず、その内容・判断は監査していない。今回の独立gradeは凍結済みV12の採点再現だけを対象とし、別作業中のpending6件source-only調査は含めない。
