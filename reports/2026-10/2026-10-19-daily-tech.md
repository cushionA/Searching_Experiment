# 2026-10-19 JST 朝の技術調査

## 結論

**A0 / B2 / C1。モデル推論・候補コード実行・性能PoCは0件、実測改善0件、機能PRなし。** 新しい有用な性能変化は今回の観測範囲では確認できなかった。追加読解の価値は、Moliの資源比較の母集団と、HeritrixのJSON URL抽出が適用される入力境界を明確にしたこと。条件付き候補をAへ引き上げるための比較は行わない。

- Moliの92 MiBは一般的な最小RAMでもRSSでもなく、著者の限定されたwarm-process-tree比較の**peak PSS中央値**。1,928全タスクや現行v1.1.18全体の性能値にしてはいけない。
- Heritrix 3.18.0の配列修正は**JSON resourceの抽出器**。任意のHTML内の埋込JSONを自動解析する根拠にはならない。これが該当する実サイトの取りこぼしは未確認。
- Firecrawlの最新公開release表記v2.11.0と開発tagは別。APIで返った先頭tagはv2.11.539。Research Indexは6月の既存公開機能で、10月の新公開と扱わない。現在docsの11/16予定の統合を今日の機能として先取りしない。

## 概要・進行中作業

観測日時は2026-10-18 22時台UTC（10/19 JST朝）。main `7460493cc3f7242748c95f711e71a59537ef34ea`から独立branch `research/2026-10-19-daily-tech`を作成。

`AGENTS.md`、`docs/cloud-lab.md`、関連`.agents/skills/bot-blocking-scenarios/SKILL.md`を読んだ。今回は公開一次資料の通常読解であり、jse.lab grounding・bot診断は実施せず。AGENTS規定により調査の一部をgpt-6-luna指定agentへ委譲、親が一次資料と照合して判断を補正。内部runtimeの独立確認はできず `model_runtime_verified=false`。

朝10/10〜10/18のレポート9日分で候補名を照合し、10/18朝`1c304fcdf53c132cf970fc92cfc2243f84b43afa`と夕`55bcd617ae45f5d8a36bc618e7be05154713bf46`を読解。夕方state4ファイルのbytes/SHA256を読取り保存、queueは**5,204件**。夕方branch/stateへ書込なし。朝のWebMCP/SetFit/SmolVLM2、夕のBekko System One/GraniteDocling/nanoMuse/Edge0/Unlimited OCRなどは再実験しない。キーワード照合は全歴史の意味的重複不在を保証しない。

open PR6件（19/21/23/25/26/28）、issue24（open、comments0）、remote headsを保存。PR19の中止を維持。SKUのPR25/26/28、`ca0d84a`の負荷入力は参照位置の確認だけで変更・実行・評価なし。goldなしの152商品ペア/45,454候補等を新しい精度証拠にしない。issue24の通知・停止機能も今回の研究目的にしない。

Squeez/YOLO26n/Harness²/PP-LCNet停止、BitNet保留を維持。既存隔離エラーの修復・新環境での再開なし。一時停止質問は返答待ちのまま、再質問・自動化停止なし。Kaggle未使用、認証・無料残量・アクセラレータ・非公開設定は未確認。計算不足を測っておらず移行理由もない。メール/cron/merge/deploy変更なし。

## 方法

GitHub repo metadata・直近3release・README・license、Moli固定tagの文書とlicense、著者resource card、Heritrix固定tagのJavaソース/pomとPR差分、Firecrawl公式docsと先頭5tagをテキスト取得。URL/時刻/bytes/SHA256はmanifestへ保存。検索結果の相対時刻よりAPIの`published_at`を優先した。

[コードと手順](../../experiments/daily-tech-20261019/README.md)、[監査数値](../../benchmarks/daily-tech-20261019/audit.json)、[進行中作業](../../benchmarks/daily-tech-20261019/context.json)、[既報照合](../../benchmarks/daily-tech-20261019/duplicate-check.json)。公開ソースはimport・compile・実行せず、依存install・バイナリDL・重みDLなし。過去レポートのコピーは文脈証拠であり新しい一次資料数に含めない。

## 候補・導入判断

starsは観測時点の関心指標で、品質・採用実績を保証しない。商用条件はファイル別/依存別ライセンスも必要。

|候補・目的・新規性|公開版・活動・community|権利・導入・資源・制約|判定と検証価値|
|---|---|---|---|
|**Moli**：Rust/V8の独立headless browser。DOM/Markdown/semantic treeを直接出力。1.1.17で検索可能PDF、1.1.18でnavigation/fetchなどの互換改善。|`lexmount/moli`最新release **v1.1.18、10/17 12:29 UTC**。19,467 stars、10/18 push。releaseに複数新規contributor。独立利用実績は未確認。|固定tagでApache-2.0またはMIT、条件下商用可、依存別条件。Linux x64圧縮asset **44,973,610 bytes**（未DL）。事前build導入は中程度、ソースbuildは未評価。通常CPU、GPU compositorなし。RAM最小値/展開・依存総量は未確認。既定mock layout、実layoutは`--layout`、visual resourceもopt-in。|**B**。既存方式で取りこぼす非SKUのJSページ、またはMarkdown前処理を短縮する実タスクが特定されたときだけ。同じ入力・出力品質・layout/resource条件で検討。新サイト発見や現行構成からの改善は未証明。一般ブラウザ比較や停止案件の迂回として導入しない。|
|**Heritrix**：archive crawler。3.18のJSON配列内URL抽出は不足補完の候補。|`internetarchive/heritrix3`最新release **3.18.0、10/6 03:47 UTC**。3,337 stars、10/16 push、IAと公開PRで継続開発。read-only HOME対応自体は通常運用修正。|README/licenseはApache-2.0、条件下商用可。metadataはNOASSERTION、pomはApacheとLGPLを列挙し、配布依存は別license。Java基盤・job設定が必要、導入中〜高。CPU/RAM最小値・総DL量・GPU要否の明示は今回未確認、8GB適合は断定しない。|**B**。既知の未取得URLがJSON応答内の配列にある場合、URL集合の増分確認には具体性あり。ただし対象未確定。全crawlerを入れて通常バグ修理を研究化しない。HTML埋込JSON・JSONP・root arrayの全対応とは扱わない。|
|**Firecrawl Research Index**：論文検索・本文passage・関連論文展開。一般Web順位の並替えとは別機能。|`firecrawl/firecrawl`最新release **v2.11.0、6/19 15:09 UTC**。先頭開発tag **v2.11.539**は別。192,753 stars、10/18 push、SDK/PRの活動あり。|repo AGPL-3.0、SDK等MIT。商用禁止ではないがAGPL条件とhosted利用条件は別。docsはkeyless開始可能と記すが今回の0円枠/残量/上限は未確認。self-hostで全Cloud機能を提供する保証なし。client負荷とserver CPU/GPU/RAMを分離、総資源未確認。導入はhosted中/self-host高。|**C今回**。研究論文のcoverageに仮説はあるが、現行探索で欠ける論文集合や無料で比較できる範囲が未確定。新サービスを実行しない。6月releaseを新成果物と数えず、必要な欠落集合が得られた時のみ再検討。|

一次情報：[Moli release](https://github.com/lexmount/moli/releases/tag/v1.1.18)、[固定README](https://github.com/lexmount/moli/blob/v1.1.18/README.md)、[著者resource card](https://github.com/lexmount/Lexbench-Headless-Browser/blob/main/docs/reports/resource-card-20260812.md)、[Heritrix 3.18](https://github.com/internetarchive/heritrix3/releases/tag/3.18.0)、[固定ExtractorJson](https://github.com/internetarchive/heritrix3/blob/3.18.0/modules/src/main/java/org/archive/modules/extractor/ExtractorJson.java)、[PR771](https://github.com/internetarchive/heritrix3/pull/771)、[Firecrawl release](https://github.com/firecrawl/firecrawl/releases/tag/v2.11.0)、[Research docs](https://docs.firecrawl.dev/features/research)、[self-host docs](https://docs.firecrawl.dev/contributing/self-host)。

## 結果・比較・想定との差

MoliのREADMEはmixed public web **103/192対Chrome101/192**（著者値）。2件の純差だけではURL単位の補集合や欠落原因はわからない。resource cardの母集団は**557タスク×5試行、全方式成功のintersection1,045 task-attempts**。PSS中央値92.12 MiB対697.14 MiB、CPU100.61ms対686.95msはwarm process treeの限定比較。557件全部が全方式で成功したという意味ではない。cold startは別でMoli3サンプル、Chrome1。READMEの機能比較はMoli0.1.1を明示しており、現行1.1.18の再測定ではない。著者値から8GB内で当方の実ページが成功するとは断定しない。

Heritrix固定コードはcontent-typeが`json`を含むか、成功した`.json`URLで抽出器を選ぶ。配列valueを再帰的に読む修正で、PRテストはobject property・配列内object・配列内stringの3形式。HTML内scriptをこの抽出器へ自動的に渡す証拠はない。入口`parse(rootNode)`はproperties列挙なので、root arrayは別途確認すべき境界（静的懸念で実測失敗ではない）。JSONP非対応もコメントに明記。汎用的な「全JSON対応」と読むより限定的だった。

Firecrawl docsはResearch Indexと`POST /search categories:[research]`を区別し、11/16からの統合を予定として記載。as-of時点に将来の統合を含めない。keylessと無料枠保証、ソース公開とspecialized indexのself-host可否を混同しない。最新後継の確認は取得したrelease/tag範囲に限り、別repoの後継不在は証明しない。

|監査項目|当方の結果|
|---|---:|
|一次資料取得試行 / 成功・hash照合|23 / 22|
|保存成功payload|355,398 bytes|
|取得3バッチwall合計|約8.96秒|
|オフライン監査|約0.0018秒、Python3.12.14|
|モデル推論 / 候補コード実行 / 性能PoC|0 / 0 / 0|
|モデル・依存DL / GPU使用 / 現金支出|0 bytes / 0秒 / 0円|
|候補修復|0|

監査時間はモデル/ブラウザ処理時間ではない。全wire bytes（検索・git・HTTP header含む）は未計測。OCR/画像・小型AI・NLPは既報と夕方読解との重複を除き、今回Aに相当する新成果物を確認できず。全分野の網羅や新技術不存在を主張しない。NLP分類/抽出は検索の除外条件と別枠だが、新しい正解付き課題を作る根拠も得ていない。

## 問題・次

最初のgit statusは親workspaceがrepoでなくexit128、正しいcheckoutで確認。Heritrixの最初のURLは`ExtractorJSON.java`と大文字表記を誤り404。PR差分の実パス`ExtractorJson.java`を確認して1回訂正取得し成功。これは資料パスの訂正で候補実行の修復ではない。外部コードを実行していないため新しい隔離試験・権限変更・auto-review拒否なし。

次の判断材料は、Moliで改善が期待できる既知の非SKU欠落ページ、HeritrixのJSON資源内にある未発見URLの具体例。取得条件と独立した期待出力を先に定義できた時だけPoCを検討する。対象がないまま合成fixtureでAを作らない。既存の停止・保留は解除しない。

実用機能未完成なのでdraft PRなし。push後の最終remote SHA・当該commit CIは最終返信で確認結果を返す。CI成功は候補推論・性能検証成功とは別。内部結果の通知要否は親が判断する。
