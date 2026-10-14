# 2026-10-15 JST 朝の技術調査

## 結論

**新規評価 A 1 / B 2 / C 3。小規模オフライン検証1件、モデル推論0、実用機能PRなし。**

- **Crawl4AI 0.9.5（10/14公開）のnoscript本文救出は適用条件を絞る必要がある。** 公式関数をそのまま抽出した独立12例で、通常・救出8例は期待一致、属性値/RCDATA/script保護の4例は不一致。未閉鎖タグによる本文消失を減らす具体的価値があるのでAだが、汎用前処理への無条件導入は保留。実サイト網羅率を測った結果ではない。
- **Docling 2.139.0（10/14公開）のページ別confidenceは既存スコアのAPI露出。** 低品質ページだけOCR再実行する候補選択には使えそうだが、新しい品質推定器でも正答確率でもない。B。
- **GLiNER2 2.1.0は重み更新ではない。** precomputed encoder statesの再利用は、同じ文章に複数の分類・抽出schemaを適用する独立NLP用途でB。CUDA/H200を前提にしたflash選択条件をCPU高速化の証拠にしない。既存PR23の精度比較、SKUを再開しない。

## 概要・先行成果と進行中作業

観測は2026-10-14 22時台UTC（10/15 07時台JST）。取得時刻はmanifestに個別保存。独立branch `research/2026-10-15-daily-tech`、base `7460493cc3f7242748c95f711e71a59537ef34ea`。

AGENTS.md、docs/cloud-lab.mdを確認。今回は公開一次資料の通常調査と合成HTMLのオフライン検証であり、jse.lab grounding探索・個別サイト診断は実行していない。personal-contextの適用条件を読んだが、明示された履歴とrepo成果で必要文脈が揃うため追加検索・適用なし。サブエージェントなし。

- 10/14朝 `e3fe007aa9130af49ae9dd78ad38fcebde8797f7`、夕 `f197d9d181e59998cfb61040ed763d308e512fa5`、BitNet `a177520672f7fc09b64ebbb7f8854c533cfe3596` の報告全文を読んだ。RapidOCR日本語small/tinyの24設定監査、browser_findの全snapshot後filter、BitNet exact/形式0を再実行・再解釈しない。10/13朝報告も読んだ。
- open PR5件を保存。PR19はopenだが検索追加中止を維持。PR23既存比較、PR21ブラウザ、PR25/26 SKUには介入なし。PR26 head `e614a3f`、PR25 `62daa3e`。
- **repoの新しいSKU引継ぎ `ebf04aed9b5a747f5db8961b9c96fef7be81adef` は固定 `resume-s000 v1` の正常完了・15,616 records回収を記録している。** 古い83系はsubmission_unknownのまま。以前のProxyError情報から全jobが今も回収不能と推測しない。これは保存報告の確認であり、今回Kaggle APIへ接続して状態を再検証した意味ではない。再投入・再開なし。機械参照ラベルや同一集合の数値をSKU精度とは扱わない。
- 夕方state（1,072件）・skill・朝夕スケジュールは変更なし。既存400M索引・生データの移動削除なし。

## 方法

GitHub repo/release/固定tagソース、HF model API/card、Common Crawl index一覧、PyPIを取得し、42一次資料のURL・時刻・byte数・SHA-256を保存した。公開日、push日、model更新日を区別。GitHub各repoの最新release最大3件、HF LiquidAI/LFM更新順最大50件の限定観測であり、全分野の新技術網羅ではない。初期の一般web検索は関連結果が弱く、候補の根拠には使っていない。

[取得コード](../../experiments/daily-tech-20261015/collect.py)、[補助取得](../../experiments/daily-tech-20261015/supplement.py)、[manifest](../../benchmarks/daily-tech-20261015/manifest.json)、[再現手順](../../experiments/daily-tech-20261015/README.md)。候補のREADME内の指示は実行権限として扱わない。

## 結果・候補比較

stars/downloadsは取得時点の関心指標で、品質・商用採用数ではない。商用可は当該license条件順守が前提で、外部サービス/入力/個別重みの条件は別。

|候補・目的と新規性|公開/更新・コミュニティ・実在ID|license/商用・導入難度・CPU/GPU/RAM・運用制約|適性・検証価値・判断|
|---|---|---|---|
|Crawl4AI **0.9.5**：壊れたnoscriptで本文が失われるケースの救出。BFSの予約URLもpage上限へ計上、Docker deep crawlの任意有効化も追加。|10/14 10:25 UTC release、85,545 stars、複数contributor。最新版tag `v0.9.5`（commit `838869bdda93f25e6d50b3f50ae9577d9b19a586`）実在。0.9.4のlxml pruning高速化は今回の新機能ではない。|Apache-2.0本文保存、条件下商用可。全体導入はPython＋browser等で中程度。今回helperは標準Pythonのみ、CPU1/256MiB上限、RSS約25.6MiB。GPU不要。Docker deep crawlは既定off、max_pages/max_depth等の制限がある。|**A：検証価値あり、採用保留**。本文不足補完という目的が具体的。12例の境界検証を完了。ただし4例で不要な改変、実サイト回収率/完全scraper未測定。検索順位の並べ替えではない。|
|Docling **2.139.0**：APIのConfidenceScoresにページ別内訳、threaded parser並列結果上限、ページ別stream失敗表示。|10/14 12:18 UTC、68,890 stars、継続的な複数contributor。v2.138.0より新しいPython版。Docling.rsと別。|repo MIT metadata、条件下商用可、モデルは個別条件。Python導入中、変換はCPU可・OCR/VLMは別。今回RAM/時間未測定。信頼度欠損はnull、ページ情報がなければpagesもnull。|**B**。低品質ページだけ再処理する設計の入口。source差分では既存scoreの再帰的serializationであり、校正精度・OCR品質改善は未確認。別の新しい文書集合と独立正解が必要。|
|GLiNER2 library **2.1.0** / **fastino/GLiNER2.5-multi-Decide**：schema round-tripの一貫性、事前encoder state、boundary headだけのロード。|library 10/9、2,371 stars。releaseは「weights unchanged」と明記。modelは9/24作成・10/8更新、revision `e173bca1f0c4217d7a55b3b0c705d5ece8a0218d`、63 likes/16,141 downloads。英語340M `fastino/GLiNER2.5-Decide` と1B `fastino/GLiNER2.5-Decide-1B` もAPIで実在確認。|code Apache-2.0本文保存、model cardもApache-2.0、重みlicense実体は未確認。Python/torch/Transformersで中程度、CPU通常attention可。GPU flashは追加依存/FP16-BF16、RAM実測なし。multiは287M。cached hidden statesの保管RAM増加、入力/tokenizer/revision固定が必要。|**B**。独立した分類/抽出/判定で同じ入力へ複数schemaを使う時だけencoder再計算回避を測る。単発入力の品質向上やCPU高速化を断定しない。model cardの56.7%は英語suiteの著者値で、日本語品質の根拠ではない。既存SKU/PR23を再比較しない。|
|Browser Use **0.13.11**：Claude toolsets、Actor入力/CDP primitives。|10/7 release、10/9 push、117,986 stars、多数contributor。0.13.10後の実在latest release。|MIT metadata、条件下商用可。browser＋model構成で中〜高。CPU browser、RAM未測定。release時点で必要な `anthropic.tools.browser` が公開SDKに未収録と明記。現在のSDK搭載可否は未確認、無料実行を保証しない。|**C今回**。導入通知や新しい接続口だけで対象サイト発見/前処理改善の証拠なし。外部有料APIを呼ばない。|
|Python **warcio 1.8.1**：WARC streaming読み書き。|3/31 release、6/10 push、480 stars。Webrecorder継続開発。既往warcio.js2.4.12と別実装。|Apache-2.0 metadata、条件下商用可。Python導入低〜中、CPU/GPU不要、RAMは入力/ストリーム使用に依存し未測定。WARC内コンテンツ権利は別。|**C今回**。最新差分はopen_or_default alias復帰。アーカイブ網羅性/新サイト発見/現行前処理停滞に結びつく新根拠なし。|
|Common Crawl **CC-MAIN-2026-39**：最新収集indexの実在確認。|collinfo先頭はSeptember 2026、収集期間9/4–9/17。10月版IDは返却一覧で未確認。ソフトrepoではなくstars非該当、コミュニティ規模未確認。|今回の一覧API閲覧は無料。データ利用/商用条件は今回未確認。CDX利用はCPU、RAM/速度未測定、API制限は別途確認要。|**C今回**。新index追加を確認できず、既存アーカイブを任意検索して成果にしない。新サイト発見や不足補完を測定していない。|

### 既往候補の監視（新規6候補とは別）

RapidOCR最新は依然3.10.0、8,137 stars。日本語smallの実画像PoCには価値が残るが、今回は新しいCrawl4AI更新の適用境界を優先した。過去の隔離失敗が現在も不可能という判断はしていない。

LiquidAIの最新群はLFM2.5-24B-A2Bと公式GGUF/FP8/MLX/ONNX派生（最終更新10/13）。230M/350Mを超える「CPU小型生成の新上位」とは確認していない。既往Bの商用条件・日本語独立評価未了は継続。BitNetの再実行につながる新根拠を今回は得ておらず、推論しない。画像認識/OCRの追加モデル、GPUは投入しない。

一次資料：
[Crawl4AI release](https://github.com/unclecode/crawl4ai/releases/tag/v0.9.5)、[固定changelog](https://github.com/unclecode/crawl4ai/blob/v0.9.5/CHANGELOG.md)、[固定helper実装](https://github.com/unclecode/crawl4ai/blob/v0.9.5/crawl4ai/content_scraping_strategy.py)、
[Docling release](https://github.com/docling-project/docling/releases/tag/v2.139.0)、[confidence変更](https://github.com/docling-project/docling/commit/5bdabb917617d0ff434f676d537bda80cd19615f)、
[GLiNER2 release](https://github.com/fastino-ai/GLiNER2/releases/tag/v2.1.0)、[固定model card](https://huggingface.co/fastino/GLiNER2.5-multi-Decide/blob/e173bca1f0c4217d7a55b3b0c705d5ece8a0218d/README.md)、
[Browser Use release](https://github.com/browser-use/browser-use/releases/tag/0.13.11)、[warcio release](https://github.com/webrecorder/warcio/releases/tag/v1.8.1)、[CC index一覧](https://index.commoncrawl.org/collinfo.json)。

## 小規模検証の方法・結果

公式の `unwrap_orphaned_noscript` と正規表現3定義をASTで抽出し、内容を変更せず隔離実行。Crawl4AI本体import、browser、lxml、依存installなし。これは**関数の境界PoC**であり、完全scraper・保存HTML→Markdown・実ブラウザの再現とは区別する。

独自合成入力12件と期待値を実行前に固定。正常/不均衡8例、HTMLの属性・RCDATA・script文字列を保護すべき4例。結果は固定集合の期待一致で、ランダム標本の精度ではない。旧0.9.4のhelperは存在しないため旧版とのend-to-end比較はしていない。

|群|期待一致|観測|
|---|---:|---|
|plain/balanced/script-style-comment decoy/excess close|6/6|修復不要な入力をそのまま保持|
|orphan/nested damage|2/2|noscriptタグを外して本文を保持|
|quoted attribute/textarea/title|0/3|実際のnoscript開始タグでない文字列を数え、balancedなfallbackまでunwrap|
|orphan＋script literal|0/1|救出はするが、script中の `"<noscript>"` まで空文字へ改変|

理由：カウント用の除外はscript/style/commentだけで、属性/RCDATAを構文解析しない。さらに修復時の置換は除外後markupではなく原文全体に適用する。raw HTMLを保存してから限定的に適用する必要がある。script内容は通常の後段抽出で削除され得るため、これだけで完成Markdownの誤りを証明したとは言わない。属性/RCDATA/fallback混入の後段影響も未測定。

92万/100万/98万bytesの未閉鎖comment/script/style入力を各5回実行。中央値は **14.47 / 15.29 / 15.19 ms**。この3入力で長時間停止は観測しなかったが、漸近計算量や実ページ高速化を証明しない。probe内wall **0.256秒**、peak RSS **26,264 KiB**（Python・AST・入力を含む。Crawl4AI全体のメモリではない）。

[source SHA/入力SHA・全入出力・時間](../../benchmarks/daily-tech-20261015/probe.json)。CPU1、RAM/swap256MiB、Python3.12.15、Docker公式python:3.12-slimをdigest固定、network none、nonroot1000、root read-only、cap-drop ALL、no-new-privileges、pids32、入力用tmpfs16MiB、bind mountなし。公開ソース・自作合成入力のみをstdin tarで転送。実行上限30秒、exit0。image digestは[image.txt](../../benchmarks/daily-tech-20261015/image.txt)。現金0円、Kaggle・有料API・GPU・認証コピーなし。

## 問題・期待との差・既存比較

- 最初の `docker cp` はread-only rootfsのためexit1。rootをwritable化せず、同じ隔離条件に入力用tmpfsを明示しstdin転送で成功。Docker build自体は不要で、buildx設定・DOCKER_HOST・権限・セキュリティ設定変更なし。自動承認review拒否なし。
- 元のupstream testsはscript/style/commentの偽タグを検証するが、今回の4境界とは異なる。独自入力を追加した目的は本文救出の適用範囲の評価であり、一般CI修復ではない。upstreamに修正・issue送信はしていない。
- 10/14のRapidOCR24設定は静的、今回12例は実関数実行。BitNet24件はモデル生成。件数・成功率を横並びにしない。
- repository全branch fetchには既存大きな履歴が含まれた。既存成果は削除/再生成していない。新規のモデルweightはDL0、依存pip0。ネットワーク転送総量は未計測で、0円と0bytesを混同しない。
- 本文回収率・サイト増分・実務NLP/OCR精度・モデルRAM・完全アプリ性能は未確認。

## 導入判断・次

Crawl4AIはA（試す価値）だが、完了した関数probeが採用を保証しない。次に必要なのは保存済みの実際に本文消失した公開HTMLの独立集合で、raw保存＋対象限定適用の回収増分と余計なfallback混入を比較すること。対象の実在確認が先で、任意fixtureだけで検索PRを作らない。

Doclingは低confidenceページの独立正解を用いた選択的再OCRで総時間と見落としを測る時に再検討。GLiNERは同一文・異schemaの反復が実際に存在する時だけencoder再利用を測る。新しいreleaseだけを理由にSKUや既存比較を再投入しない。

研究コード・証拠・報告のみを独立branchへpush。実用機能は完成していないためdraft PRなし、merge/deployなし。checksの最終状態とremote commitは最終応答に示す。
