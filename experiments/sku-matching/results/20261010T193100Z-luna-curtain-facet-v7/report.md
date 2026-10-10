# V7 curtain facet 監査結果

独立再採点を外部評価ディレクトリで実行し、run内の`grading/`と照合した。評価ラベルはcheckoutへ配置していない。独立側の`v7_62`・`cumulative_200_v7`および比較結果のsummary、case-results、comparisonsはrun内のものと完全一致した。source manifestの`createdUTC`だけは再実行時刻に応じて異なる。固定対象295ファイルのSHA-256はすべてgrading freezeと一致した。

V7は既存の同一62ケースを再利用した開発比較であり、新規の独立ケースは0件。4件の共有lace facet回答を固定済みmatching回答へ合成し、新規の全体matching呼出しは行っていない。facet回答は計4論理タスク、signatureは14件中13件を同一行のV6署名回答から再利用し、幅・丈ルールを含む1件を追加。署名は14/14通過。62件の単一caseレビュー回答はdispatch記録・request/answer hash・case IDと一致し、全62ケースを一度ずつ覆う。最終レビューは採用14、除外15、保留33。採用14件は現在の候補行とレビュー行、dimension pass、同じ候補回答hashに束縛された署名、レビュー証拠IDが整合し、export link 14件も原レビュー・署名・matching answerへhashで結び付く。

採点はV7 29/62（46.77%）、採用行precision 14/14（100%）、row-match recall 14/30（46.67%）。累計は142/193（73.58%）、accepted-row precision 48/48（100%）、row-match recall 48/73（65.75%）。累計の既知正解coverageは143/193で、142/143の既知判定が正解。これらはmachine labelsによる採点であり、人手確認済みではない。累計のunknown truthは7件、採用は0件。独立holdout性能や日本市場全体への一般化を示す値ではない。

記録上のdispatchはfacet 4論理タスクが19:40:38–19:41:20 UTC、追加signature 1件が19:44:34–19:44:56 UTC、review 62件が19:46:13–19:59:54 UTC。すべてrequested modelは`gpt-6-luna`だがactual modelVersionとusageはnullで、実際の提供モデルやトークン量は検証できない。facet metadataもprovider call数を独立検証していないため、論理タスク数を実provider call数として扱わない。

以前のprep auditはレビュー回答の有無を確認する前に作られているため、今回の全62回答確認の根拠には使っていない。以前問題となったcase `case-08a49ff8b3253e3dcc82`は今回のレビューでは現在候補行・dimension passを確認して採用されており、旧レビューのpending文言は再利用されていない。run記録の全suiteは574 run、37 skipped、17 errors、1 failureで、監査ではテストsuiteを再実行していない。

監査詳細は[`independent-audit.json`](independent-audit.json)。
