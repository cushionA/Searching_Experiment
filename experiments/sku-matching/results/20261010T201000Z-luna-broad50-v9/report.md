# V9 broad50 独立監査

V9 の50ケースは、70件のmatching chunk taskで934候補行を検査した。chunk検証は70/70、内訳はcandidate 15、exclude 34、pending 1。各ファイルのUTF-8サイズで測った未送信の全量契約案は3,409,676 bytes、compact案は2,151,946 bytes（約36.9%減）。実際に保存された70件のchunk requestは2,876,412 bytesで、同じ商品文脈がchunkごとに繰り返される。この値はprovider token usageではない。

独立の外部採点を新しいevaluation directoryで実行し、保存済みgradingと先行独立実行の `comparisons.json`、run/cumulative各summaryとcase-resultsの計5ファイルがすべてbyte一致した。grading freezeの410 hash entryも再計算して一致した。新50の既知ラベル評価は38/48（79.17%）、採用7・除外33・保留10、accepted-row precision 7/7、row-match recall 7/14。累計250ケースは213/241（88.38%）、採用71・除外147・pending31・未評価1。累計exportは71リンク、楽天SKU69、AU商品33、候補行3,606。評価対象は共有source groupに属し、独立したsource-group holdoutではない。`human_verified=false`。

候補選択・source scope・row target・signature/review証拠は凍結入力と派生結果にひも付いている。累計207楽天SKU・33 AU商品・3,606候補行はケース数やリンク数と区別して読む必要がある。dispatcher記録ではmatching 70、signature 14、review 50 taskを要求し、開始/完了時刻が記録されているが、served model versionとusageはnull。したがって実provider modelやtoken usage、provider側の経過時間を示す値ではない。

保存済みsuite結果は599 tests、1 failure・17 errors・37 skippedで、V9でも従前と同じ18 failure/error ID。focused adjustable-dimension 8件、current-row-dimension 4件はpass。監査時はgold labelsをcheckoutへ移さず、独立evaluation directory経由でgrade.pyだけを実行した。
