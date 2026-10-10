# V11 adjustable dimensions 独立監査

外部evaluation directoryでgrade.pyを再実行し、保存済みgradingおよび先行独立実行との `comparisons.json`、run/cumulative各summaryとcase-resultsの計5ファイルがbyte単位で一致した。grading freezeの267 hash entryもすべて一致。V11は39/48（81.25%）、採用8・除外34・保留8、accepted-row precision 8/8、row-match recall 8/14。累計250ケースは214/241（88.80%）、採用72・除外148・pending29・未評価1、precision 72/72、recall 72/87。累計は新50だけの成績ではなく前200ケースを含む。累計sourceは207楽天SKU、33 AU商品、3,606候補行、export 72 links/70楽天SKU。`human_verified=false`であり独立source-group holdoutでもない。

段階診断 `grade_stages.py` も独立に再実行し、保存済み・先行独立結果と完全一致した。matching段階は46/48でknown false accept 1件とunknown truthのaccepted 1件を含む。V9寸法段階は43/48（precision 10/10、recall 10/14）、V11寸法段階は46/48（precision 13/13、recall 13/14）。これらは `hypothetical_stage_only=true` / `not_exported_as_links=true` の診断値で、実際に出力されたリンクの評価ではない。最終レビュー後のexport評価は39/48である。

新規の寸法署名3件は元AU資料に限定して照合した。寝具 case `case-74502f3d28388318c4f8` は選択行と（約）150×210cmの引用に基づきconfirm。ステップ台 `case-f50e7e9ac8ebe7c49f4f` は楽天選択重量2.1kgに対しAU原文「約2.5kg」、`case-e7f3ea534fed639ff188` は楽天耐荷重300kgに対しAU原文「約100kg」でreject。AU source ZIP memberのSHAを検証し、引用は原文中に存在した。元source・signature応答は変更されず、12件は既存署名を再利用した。exportをcollector完了poll前に試みた記録はpipeline issueに残り、その後成功pollと全source hash再確認で回復。再推論は0件。

レビューdispatchは新規3件、dimension signature dispatchは3 logical task。dispatcherはgpt-6-lunaを要求したが、実served model versionとusageはnull。時刻・mtimeはdispatcher/保存metadataであり、providerの実測呼出時間や実モデルIDとはみなせない。source labelsはcheckout外のevaluation directoryに留め、独立監査でgrade.py以外から閲覧していない。

suite記録は599 tests、1 failure・17 errors・37 skippedで、V9と同じ18 failure/error ID。focused adjustable-dimension 8件とcurrent-row-dimension 4件はpass。結論はこのfreezeされたrunの検証に限られ、商品の人手確認を意味しない。
