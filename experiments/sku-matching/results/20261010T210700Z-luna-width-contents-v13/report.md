# V13 width-contents 独立監査

外部evaluation directoryで `grade.py` を独立実行し、保存済みgradingの比較表、V13/cumulative各summaryとcase-resultsの5ファイルがすべてbyte一致した。grading freezeの985 hash entryを再計算してすべて一致。`verify_lineage_v2.py --verify-only` も再実行し、V13と親freezeを再帰的に束ねる950 unique files、7 inherited guard dependenciesのhash一致を確認した。監査コードはlabelsを読み込まず、raw labelsはcheckout外のevaluation directoryに留めた。

V13のpaired50結果は44/48（91.67%）、adopt10・exclude37・pending3、accepted-row precision 10/10、row-match recall 10/14。累計250は219/241（90.87%）、adopt74・exclude151・pending24・not-evaluated1、precision 74/74、recall 74/87。累計exportは74 links、72 distinct楽天SKU。前200組には同じ62組の最新版V10を使い、各ペアの最新結果だけを数えた。処理済み250組は楽天SKU207件・AU商品33件・候補行延べ3606であり、今回の5件は同じ集合内の再試験。`human_verified=false`であり、shared source groupを使うため独立source-group holdoutでもない。

新規matchingは元513 AU行を20 chunksでカバーし、caseごとの連続範囲にgap/overlapがないことを確認した。19/20 chunkがvalid。1 chunk（case `case-105af0d9f0b13877f3c1`、row 135–152）は生成rowsが0で、構造検証不能。失敗した生artifactは維持され、当該ケースはV12の完全request/answerをbyte一致で再利用し、新規成功として数えていない。残る4ケースの完成answerはvalid chunk rowsを入力順に連結した内容と完全一致した。したがって50ケースのmatchingは45 unchanged V12 reuse + 4 whole-case fresh + 1 failed attempt後のV12 fallbackの構成となる。

寸法署名はV12から15 raw reuseし、新規2件を加え17件中15件pass。失敗した既存2件は椅子の矛盾判定が維持された。レビューは45件をV12からbyte一致で再利用し、新規5件はreject 3、unresolved 2。未確認のメーカー型番・原産国を推測で埋めず保留にした。既存の梯子case `case-615b639a7601d767385e` も防滴/防水情報がAU原文にないためpendingのまま。

レースなしの原文proof sidecarは最新50組の採用10リンク中3件を収録し、export hash、case/row対応を検証した。V12で採用した2件のbounded contents row・次のカラー区切り・幅区分とcomponent linesを限定source ZIPのmember SHAおよびJSON field spanに照合し、現行rowの色/サイズ条件もSKU gridのrow/column indexで確認した。V13で追加した採用リンクは0件。proofはローカル復元資料であり、provider出力そのものではない。「レースなし」は完結した内容表の読み取りに依存するsemantic review判断である。

V13の論理dispatchはmatching 20、signature 2、review 5の計27 task。開始/完了から計算したcoordinator intervalは順に273.843秒、20.693秒、85.632秒。これらはprovider実行時間ではない。全dispatchでactual model version、usage、actual provider call countはnull/不明。失敗chunk後のsave-only follow-upはworker記録上再推論ではないが、実provider呼出回数は確定できない。選定条件にはV12のpending状態を使っており、goldは使っていない。初期 `verify_lineage.py` はtransitive freeze読込不足で書込・推論前に失敗し記録を保持、修正でなく追加した `verify_lineage_v2.py` がreview後・grade前に全親hashを確認した。これはinference前 gateだったとは扱わない。

最新suite結果は599 tests、1 failure・17 errors・37 skippedで、V11から同じ18 failure/error ID。focused current-row 4件とadjustable-dimension 8件は成功。V13の新規結果はmachine-label agreementであり、人手による商品確認を示さない。

元のVertex固定6件はgroundingsearch/global・gemini-3.5-flash-liteで各1回完了済み。HTTP200/STOPと構造検証6/6、意味レビュー採用2・除外4・未評価0、modelVersionは全件gemini-3.5-flash-lite。usageはprompt15473/candidates3502/total18975 tokens、推論run12.930秒。今回のLuna改善では追加Vertex推論や認証値の利用・保存はしていない。
