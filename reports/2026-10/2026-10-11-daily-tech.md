# 2026-10-11 JST 日次技術調査

## 結論

**Aなし。主候補8件を B 6件 / C 2件とした。新機能のdraft PRは作らない。**
新しい知見は (1) Playwright 1.64のShadow DOM保存の限定効用、(2) 本文取得不要のホスト発見候補となるCommon Crawl graph embeddingsの収録制限、(3) SKU別作業の最新v5が確認工数と取りこぼしの交換条件を持つこと。
Doclingの昨日のOCR選択実験、HOME’S sitemap宣言、PR23分類、PR19検索は再実行していない。

## 概要

観測は **2026-10-10 21:57〜22:02頃UTC / 2026-10-11 06:57〜07:02頃JST**。
当日全体の更新網羅ではない。前回保存 `4ad359dfe4bbda3bf9b76426cf0e183bf31a6856` を取得して基点とした。
mainは `7460493cc3f7242748c95f711e71a59537ef34ea` のまま。
GitHub公開16 repoのmetadataと直近最大3 release、追加公式資料、Common Crawl公式indexを確認した。
最初のONNX Runtime repo指定1件は誤りで404、正しい `microsoft/onnxruntime` のreleaseへ訂正。
従って初回scanの成功repoは15であり「16製品を完全評価」とはしない。

AGENTS.md、docs/cloud-lab.md、.agents/skills/bot-blocking-scenarios/SKILL.mdを確認。
今回は公開技術の資料確認とローカル保存形式の実験で、jse.labのgrounding探索、サイト別block診断は実行しない。
Personal Contextは利用不能。別会話の判断はrepoに保存された資料の範囲で確認し、会話全体の最新合意と同一とはみなさない。
サブエージェント・生成モデル推論・GPU・Kaggle・新規認証・有料サービスは使っていない。

## 候補評価

A=すぐ試す価値、B=条件が整えば価値、C=今回不要。starsは取得時点のGitHub API値。
release日とpush日は区別し、旧releaseを「昨日からの新技術」と呼ばない。
RAMは実測・公式値・未確認を分ける。OSSコードの条件とデータ・重みの条件も別扱い。

|候補・目的／新規性|公開・更新、stars／開発・コミュニティ|license／商用・導入・CPU/GPU/RAM・制約|用途適性・検証価値・判定|
|---|---|---|---|
|**Playwright 1.64**：open Shadow DOMをdeclarative templateとして保存。WebMCPも追加。前回候補表にはない。|10-07 20:40 UTC release、97,442 stars、push10-10。Microsoftの継続開発。|Apache-2.0。条件下で商用可。`playwright@1.64.0`と対応browser。本PoCはCPU、Node+子processの最大RSS約120.3MiB（合算ではない）。closed shadow対象外、WebMCPはサイト側登録と実験機能に依存。|**B・限定PoC済み**。保存HTMLからopen/nestedリンク2件を追加保持。ただしlive locatorを超える正しいリンクは0。実サイトでの追加発見・時間削減未確認。WebMCPは未実行。|
|**Docling.rs 2.11→2.13**：2.11の構造化文書PII redaction、2.12の重複除去・逐次動画フレーム処理、2.13の画像source policy。Python版とは別release系列。|10-10 10:13 / 11:16 / 12:40 UTC公開。95 stars、push10-10。小規模だが継続release、コミュニティの実運用品質は未確認。|コードMIT、商用可（条件順守）、重み別。`pip install docling-rs` / `cargo install docling-cli`、CPU ONNX／任意GPU。画像sourceはembedded/local/remote、embeddedは外部取得不要。videoにはffmpeg、RAM削減量は未実測。NER追加は公式約430MB重みでRAM値ではない。|**B**。画像取得量の制御や動画OCR前処理削減に具体性あり。ただしrepoの優先入力に動画がなく未実行。日本語NER精度・完全変換互換は未確認。昨日のPython Docling選択改善の再検証とは別。|
|**Common Crawl Web Graph Embeddings**：文字本文なしでリンク構造から類似host候補を生成。順位付けではなく既知seedからのサイト発見補完が仮説。|09-07公開、code **1 star**、push同日。公式研究成果、HF datasetの継続版は現cardで予定なし。前日以降の公開ではなく今回の新規候補。|code Apache-2.0、datasetはcommoncrawl-touで独自条件。商用用途の個別適合は未確認。Parquet/NumPy/FAISS等。52,913,544×128、FP16約13.5GBはベクトル規模でRAMではない。全FP32行列だけで約27.1GB、今回16GiB環境には全載せ不可。CPU分割処理の見込み、学習再現は公式4×H100。|**B**。本文WARCのdownloadは不要だが元データはarchive由来。2025-11〜2026-01、degree≥8でhostの約19%に限定。新設・低リンクhostを新規生成できない。日本語seed別の増分host数・有効率は未検証。大容量downloadはしない。|
|**GLiNER2 library 2.1.0（NLP別枠）**：schema JSON整合、encoder states再利用、span/decode改善。|10-09 17:53 UTC公開、2,354 stars、push同時。前回観測以前のreleaseだが前回候補表で未評価。重み変更なし。|コードApache-2.0、モデル別。`pip install gliner2==2.1.0`。CPUの通常経路あり、DisentangledFlashはCUDA FP16/BF16向けでH200条件の閾値、CPUへの高速化を外挿しない。RAM未測定。hidden statesはtokens/mask一致・メモリ保持が必要。|**B**。同一文書・複数schema処理には再利用価値があり得るが、PR23の分類精度改善を意味しない。既存SKU CPU比較も進行済みなのでモデル再実行せず。|
|**Trafilatura 2.3.1**：本文の重複段落・画像段落・inline code後の重複修正。|10-06 19:31 UTC公開、6,950 stars、push同日。複数contributorの修正。|Apache-2.0、条件下商用可。`pip install trafilatura==2.3.1`、CPU、GPU不要。RAM/速度未測定。静的抽出で動的DOMには別取得が必要。|**C（今回）**。本文前処理候補ではあるが通常の重複修正で、具体的な新しい時間削減仮説なし。ユーザー指定の「通常修正・重複比較」を増やさない。|
|**Browsertrix Crawler 1.15**：archiveのauto-QA拡張、random sampling、skipped pages報告。|10-02 19:52 UTC公開、1,158 stars、push10-08。Webrecorderと複数contributor、継続開発。|AGPL-3.0。商用禁止ではないが同licenseの条件あり。公式Docker、browser/Redis等。CPU、browser並列数と記録量に応じたRAM/保存領域、今回未測定。|**B（archive専用）**。記録失敗の把握とQAには適性。新しいサイト発見ではなく、対象の既存WACZ集合も指定されていないため再クロール/PoCなし。|
|**RapidOCR 3.10**：前回候補の監視。今回注目はraw image buffer/stride経路とlazy loadだが、新releaseではない。|10-08 15:21 UTC、8,105 stars、push10-10。継続開発、複数contributor。|Apache-2.0コード、各重み別確認。`pip install rapidocr onnxruntime`。CPU可能／GPU任意、RAM未測定。raw buffer lifetime/strideを呼出側で整合させる必要。|**B維持**。日本語PP-OCRv6追加と誤認しない。実画像・OCR全体時間の入力が揃わず再測定しない。|
|**llms.txtを発見入口とする案**：公式Common Crawlの内容調査を確認。HOME’S sitemap宣言の再確認とは別。|公式調査08-31公開、7月crawlの584,107非空text。stars対象外、技術仕様は提案段階。|サイトごとのデータ権利で一括商用許諾なし。HTTP+Markdown CPU処理、RAM/運用速度未測定。本文が許可や実行指示になるわけではない。|**C（一律導入）**。公式母集団で22.56%はリンクなし、text bodyを返す割合とサイト網羅率は別。今回新しい有効サイトを具体的に示せず実装しない。|

一次資料（取得metadata・release本文・READMEは `benchmarks/daily-tech-20261011/{sources,supplement,details}.json`）:
[Playwright release](https://github.com/microsoft/playwright/releases/tag/v1.64.0)、[content仕様](https://playwright.dev/docs/api/class-page#page-content)、
[Docling.rs 2.13](https://github.com/docling-project/docling.rs/releases/tag/v2.13.0)、[2.12](https://github.com/docling-project/docling.rs/releases/tag/v2.12.0)、[2.11](https://github.com/docling-project/docling.rs/releases/tag/v2.11.0)、[版固定README](https://github.com/docling-project/docling.rs/blob/v2.13.0/README.md)、
[graph発表](https://commoncrawl.org/blog/web-graph-embeddings-an-experimental-dataset-release)、[dataset](https://huggingface.co/datasets/commoncrawl/web-graph-embeddings)、[code](https://github.com/commoncrawl/web-graph-embeddings)、
[GLiNER2](https://github.com/fastino-ai/GLiNER2/releases/tag/v2.1.0)、[Trafilatura](https://github.com/adbar/trafilatura/releases/tag/v2.3.1)、
[Browsertrix](https://github.com/webrecorder/browsertrix-crawler/releases/tag/v1.15.0)、[RapidOCR](https://github.com/RapidAI/RapidOCR/releases/tag/v3.10.0)、
[llms.txt公式分析](https://commoncrawl.org/blog/a-content-analysis-of-llms-txt-files-from-the-july-2026-crawl-archive)。

### 既知候補の監視

- Docling Python stableは2.137のまま（68,646 stars）。昨日の39→21条件を再実行しない。日本語OCR/全体時間の証明は増えていない。
- Crawlee JSは4.0.0-rc.1のまま（26,105）、Crawl4AIは0.9.4（85,156）、Lightpandaは1.0.0（36,214）。push時刻だけを新能力として数えない。
- PaddleOCRは3.7.0のまま。Transformers5.19のNeoMME等も確認したが、単なるretrieval追加とモデル横比較は始めない。
- Common Crawl公式`collinfo.json`の先頭はCC-MAIN-2026-39、収集09-04〜09-17。10月indexがないことをWeb全体の更新停止とは解釈しない。
- HOME’Sは前回の69宣言と権利/coverage未確認を引き継ぐだけ。再取得なし。

## repo新成果・進行中PR

以下は**保存資料のレビューで、独立再実測やgold監査ではない**。SKU ZIP・生データ・採点用ラベルは開かず、別会話の収集/実装を開始・停止・置換しない。
指定の `codex/sku-matching-cloud-20261009` は今回のremote branch一覧には出ない。ローカル別環境での収集状態は未確認で、終了したとは判断しない。

- [PR19](https://github.com/cushionA/Searching_Experiment/pull/19) `a60be17` draft、[PR23](https://github.com/cushionA/Searching_Experiment/pull/23) `d918fda` draft、[PR21](https://github.com/cushionA/Searching_Experiment/pull/21) `4805842` open。前回HEADから変更なし、表示checkは成功。検索開発・販売状態再比較・通常修正なし。
- [PR25](https://github.com/cushionA/Searching_Experiment/pull/25) `62daa3e` open、[PR26](https://github.com/cushionA/Searching_Experiment/pull/26) `63980b2` open。validate失敗、optional-tools成功を観測。失敗を今回修正しない。両PRのbaseは `codex-sku-model-comparison-20261009`。PR26はPR25の変更を含む積み重ねで、main向け独立PRとは違う。
- **PR26本文はv1中心で古い。版固定引継ぎ§13-8〜13-11にv3→v5がある。** v3の機械参照でモデル対応115件中same35/partial20/別物60と判明し、v4以降はモデル対応を確認済みにせず説明確認へ回す。v5は軸対応にだけモデルを戻し、直接採用をv4と同じ21/143/417件に保って、29ペア/family/候補のhandoff行を11,975→1,317、873→791、52,843→13,327に削減。ただし候補5,471ケースが説明確認待ちから未解決除外へ移り、誤軸対応による取りこぼし量は未測定。候補1,160組は商品対応未確認を含み、固定された確認済み商品ペアの精度へ一般化しない。方式はlabel-informed、参照は機械・人手未確定。研究価値は**説明確認工数削減と取りこぼしの測定**であり、ここで同じモデル比較を立ち上げない。
- [CPU比較commit 1e370473](https://github.com/cushionA/Searching_Experiment/commit/1e370473e41372131b2ece1fce1a285762571ae3) の報告はGLiNER/JEV/NLI/rerankerを既に比較済み。追加32条件のtop5＋GLiNERは支持4/9、予測支持5中誤支持1。機械ラベルの開発値で運用SKU精度ではない。入力512tokens判定の訂正も保存済み。新しいGLiNER library releaseだけを理由に重複実行しない。
- [別系統commit 7146ddca](https://github.com/cushionA/Searching_Experiment/commit/7146ddca557b786fa153f96d2b78331c89233ea6) のLuna継続メモは250組・楽天207SKU・AU33商品、219/241という自己報告。元1439機械ラベル、未使用商品/原文groupなし、provider modelVersion未確認。**今回許可された非生成モデル調査の比較対象には採用しない**。1383レコード/644SKUの集合と1439ラベル集合の同一性・対応は未確認。数を置き換えず、人手未確定・独立holdoutなしを維持する。

資料:
[PR26最新引継ぎ](https://github.com/cushionA/Searching_Experiment/blob/63980b21e67bb58c13586c64f150a58d7b33e419/experiments/sku-matching/claude-next-steps-and-codex-advice-20261010.txt)、
[CPU比較報告](https://github.com/cushionA/Searching_Experiment/blob/1e370473e41372131b2ece1fce1a285762571ae3/experiments/sku-matching/claude-pr26-task-improvement-20261011.txt)、
[Luna継続メモ](https://github.com/cushionA/Searching_Experiment/blob/7146ddca557b786fa153f96d2b78331c89233ea6/experiments/sku-matching/luna-sku-task-continuation-20261010.txt)。

## 方法：Shadow DOM保存のCPU限定PoC

1. 自作の日本語HTMLとprotocolを実行前に固定。通常リンク、slot、open/nested/closed shadow、iframe、未使用templateの7種類。外部サイト本文や独立goldの代わりではない。
2. Playwright1.64.0、対応Chromium headless shell156.0.8078.4（build1248）、Node24.19.0、Python3.12.14。package-lock保存。browserは/tmpに隔離導入し、既存環境を更新しない。
3. offline contextにsetContent/evaluateでDOMを構築。外部navigation、リンク追跡、検索、WebMCP呼出はゼロ。closed shadowをopenへ変える等の改変なし。
4. 同一pageから`content()`既定相当と`content({includeShadow:true})`を各30回交互順で取得。全タイミングとHTML保存。新旧Playwright版比較ではなく**同一1.64版のoption比較**。
5. repoの変更していない`jse.lab.fetch.HTML`で保存HTMLを解析。対照にlive `page.locator('a')`、iframe単体content。正しい追加リンクとinert templateの偽候補を分ける。
6. protocol/input/code/HTML hash、30回の出力安定、期待集合一致、外部requestとpage errorゼロを別Pythonコードで検査。cgroup CPU4相当、可視5CPU、memory max16GiB。

## 結果・既存比較

|指標|default保存HTML|includeShadow保存HTML|live locator|
|---|---:|---:|---:|
|同じpageの公開DOM内の対象リンク保持|2/4|4/4|4/4|
|未使用template内の余分な候補|1|1|0|
|保存HTML bytes|577|837|該当なし|
|30回中央値 ms（初回込み）|0.584|0.588|時間未測定|
|初回 ms|1.029|0.857|時間未測定|

- open/nestedの2リンクが保存経路で増えるが、live locatorより多く正しいリンクを発見したわけではない。
- closed shadowは両保存・locatorとも取得しない。iframeは親HTMLのhrefとしては出ず、子frameのcontentを別に読むと1リンク取得。
- 30回とも各modeのHTML hash一致、期待4集合が一致、page error0、外部request0。
- Node probe内部0.937秒、wrapper全体1.207秒。RUSAGE_CHILDRENの最大RSS123,180KiB=120.3MiB、Node自身も別記録。process tree同時合算RAMではない。
- 既存 `jse/lab/adaptive.py` は引数なしpage.contentを使用するため保存経路の仮説と接点がある。ただしPython版・Crawlee接続・実サイトでの動作は未実施。runtimeは一切変更していない。
- この小fixtureは網羅率の評価ではなくAPI境界の再現。全体前処理速度改善は証明していない。577→837 bytesの増加も予算への影響として考慮が必要。

## 問題・想定との差・チェック

- 想定した保存漏れの回復は確認したが、live locatorを既に使う用途には新しい発見量がない。全templateの一括採用はinertリンクを混ぜる。通常の穴埋め実装へ発展させない。
- Docling.rs/GLiNER2/Browsertrix/RapidOCRの推論・実変換、Common Crawl類似host検索、llms.txt実取得、日本語OCR・実サイトcoverageは未検証。
- Common Crawl manifestで52,913,544行・128次元・14 shardを確認しただけ。vector/ANN全体をdownloadせず、新サイト発見数は0件を測ったのでなく**未測定**。
- `verify.py` 成功。151件のlabテストは135成功・15skip・1失敗。`test_offline_diagnostics_guards`が既存bot-diagnosticsの`robots-parser`なしで失敗。前回同様、修正・依存補充なし。
- 初回npm cache pathが書込不可で失敗し、/tmp cache指定で成功。`/usr/bin/time`不在のためprobe起動前に失敗し、Python resource wrapperで計測。初回source URL 404も含めログ保存。費用や認証追加を要するblockはなし。
- GitHub PR checkは観測済みのremote結果。今回branchのCI完了を意味しない。`git diff --check`と保存manifestを別途検査してからcommit/pushする。

## 導入判断・次

**調査成果のみ保存し、機能統合・draft PR・merge・deployは行わない。**
Playwrightは実際の対象でopen shadow由来の増分発見を示せるとき、Docling.rsは画像/動画前処理の対象入力があるときだけ次段階。
Common Crawl graphは既存seedとの重複を除いた有効host数と鮮度を、明確な小範囲・CPU分割処理で評価できる場合に価値がある。本文アーカイブ不要と元archive非依存は区別する。
NLPは同一文書の複数schemaという独立用途が成立した場合だけencoder再利用を検討し、SKU側には重複介入しない。
SKU関連は最新担当にv5の取りこぼし課題が残っていることを研究判断として記録した。別の採点・収集・運用ラベル変更は行わない。

保存先:
- `experiments/daily-tech-20261011/`: 自作fixture、事前protocol、PoC/再集計/source収集コード、npm lock、手順。
- `benchmarks/daily-tech-20261011/`: 元HTML3種、timing、期待集合照合、環境、一次資料snapshot、PR状態、導入/テスト/errors/manifest。
- 本報告。公開commitリンクは完了応答に記載。
