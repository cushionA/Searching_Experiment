# 2026-10-10 JST 日次技術調査

## 結論

**Aなし。DoclingはBを維持し、有用な追加検証だけを実行した。新しい検索機能・draft PRは作らない。**
昨日の候補v2.135.0に対し、v2.136/2.137の更新を確認。不要OCR回避のCPU限定再現で、
旧版39/39条件だったOCR対象が新版21/39条件になり、必要とされる21条件を維持した。
ただしupstream自身の5PDF・13領域を3経路で再現した結果であり、本番OCR精度や速度向上の実証ではない。
実際、領域選択のwarm中央値は21.76→24.43msで高速化していない。

HOME’Sの69件のsitemap宣言は公式robots.txtで再確認したが、網羅性・増分発見・商用再利用は未確認。
Docling v2.137のPP-OCRv6変更は既存52言語のAPI互換修正であり、日本語OCRの新規追加とは数えない。
新しいAを作るためのモデル比較や、単なる順位付けのPR化は行わなかった。

## 概要・既存成果との照合

- 観測は2026-10-09 22時台UTC＝**2026-10-10 07時台JST**。日全体の更新を網羅した意味ではなく、この時点のスナップショット。
- 基点main `7460493cc3f7242748c95f711e71a59537ef34ea`。最新mainはJoshin local→proxy実験TODOの記録。mainの過去索引・生データは変更していない。
- [PR #23](https://github.com/cushionA/Searching_Experiment/pull/23) はdraft、HEAD `d918fda`、更新10-07 23:07 UTC。GLiNER状態分類32/48、TF-IDF34/48、誤available 8対2。導入優位なしという既存結論を維持。報告本文・ファイル一覧を確認。
- [PR #19](https://github.com/cushionA/Searching_Experiment/pull/19) はdraft、HEAD `a60be17`、更新10-07 00:25 UTC。CPU mLateOn実験完了。検索追加開発は停止したまま。
- [PR #21](https://github.com/cushionA/Searching_Experiment/pull/21) はopen、HEAD `4805842`、更新10-07 15:00 UTC。同一セッション再訪・ブラウザ比較は進行中の既存成果として確認し、通常修正・追加実測をしなかった。
- PR #22の日本語条件・paginationはmainに統合済み。今回のブランチへ未マージPRのコードを取り込んでいない。
- 昨日のHOME’S B / Docling v2.135 Bはユーザーの引継ぎ情報。mainと確認した保存ブランチには10-09の報告を見つけられず、Personal Contextも利用不能。昨日の実測・コミットが存在すると仮定しない。
- 別会話のau PAY/楽天SKU照合・小型モデル比較は開始していない。問題報告済みの評価ZIPを開かず、機械ラベルや参照JSONPathを正解に流用していない。

## 候補評価

A＝直ちに比較検証・導入検討へ進める強い根拠、B＝条件付き、C＝今回追加作業をしない。
starsは今回のGitHub API取得値で、品質や最近の開発量を表す数値ではない。
日時は以下ではUTC。`pushed_at`はrepoへのpush時刻であり、stable版の公開日とは別。
根拠は `benchmarks/daily-tech-20261010/{sources,supplement}.json` に取得時刻・URL・ハッシュ付きで保存。

| 候補・目的／新規性 | 公開・更新、stars・開発状況 | ライセンス・商用、導入・資源・運用制約 | 適性・検証価値・判定 |
|---|---|---|---|
| **Docling v2.135→2.137**：PDF/HTML等を構造化。2.136のnative text＋図形のOCR選択改善は前処理負担に具体的関係。2.135のHTML/Markdown保持修正は昨日からの継続候補。 | 2.135:10-07 10:42、2.136:10-09 12:08、2.137:10-09 14:12。**68,604 stars**、push10-09。継続リリース。 | コードMIT、商用可（条件順守）。今回 `docling-slim[convert-core,format-pdf]==2.137.0`。CPUのみ、PoC RSS149.4MiB、モデルなし。通常PDF pipelineのモデル・RAM/GPUは別。モデルごとにライセンス確認が必要。 | **B維持、限定再現済み**。不要なOCR対象を除外したが、実文書・全体処理の効用未確認。標準OCR modeのDEFAULTも同じPDF-aware選択へ分岐する。 |
| **RapidOCR v3.10.0 / PP-OCRv6連携**：CPU向けOCR、モデル遅延初期化・公開言語一覧・経路/ライセンスmetadata。 | 10-08 15:21公開、**8,090 stars**、push10-09。Docling 2.137はprivate定数削除への追従。 | コード・列挙されたPaddleOCR由来変換重みはApache-2.0、商用可（条件順守）。他の重みは別。`pip install rapidocr onnxruntime`、CPU/GPU選択。日本語はv6 small/medium対応、tinyは対象外。正しいモデル選択と初回downloadが必要。実測RAM/速度未確認。 | **B**。日本語のスキャン入力がある場合に限定検証価値。日本語対応自体を今回の新規性としない。モデル推論は未実施。 |
| **Crawlee JS v4.0.0-rc.1**：DomCrawler＋交換可能なDOM parser、microdata抽出。ブラウザ不要の静的HTML処理候補。 | 10-06 13:48公開、**26,087 stars**、push10-09。安定版は3.18.2、4系はRC。 | Apache-2.0、商用可（条件順守）。`npm install crawlee@4.0.0-rc.1`と用途別parser package。DOM経路はCPUのみ、RAMはparser/並列数次第で未測定。package境界・RequestQueue等の破壊的変更あり。 | **B**。同一HTMLで抽出保持とbrowser比RSS/時間に差がある場合だけ検証。既存Python Crawlee環境の単純upgradeとは扱わない。 |
| **Lightpanda 1.0.0**：軽量JS実行、WebDriver・Web API対応拡大。 | 10-02 10:12公開、**36,168 stars**、push10-09。既にこのrepoの10-02/03ブラウザ実験と重なる。 | AGPL-3.0、商用利用は禁止されないが配布・ネットワーク提供等の条件あり。binary/Docker、Linux/macOS、WindowsはWSL。CPU、GPU不要。vendorの低RAM値は独立実測でないため採用しない。現在の追加条件でのRAM未確認。 | **C（今回）**。昨日以降の新releaseなし。既存結果を超える対象・改善仮説がないため再試験しない。完全なブラウザ互換とは限らない。 |
| **Marker 2.0**：小型layoutとtext layer中心のfast mode、選択的VLM修復。 | 07-20 19:41公開、**40,330 stars**、push10-02。10月の新releaseではない。 | コードApache-2.0。重みは修正OpenRAIL-Mで用途・商用規模に制約、広い商用利用は別契約。`pip install marker-pdf`。`--disable_ocr`はCPU向け、VLM使用時は実装/加速条件が別。実測RAM未確認。 | **B（保留）**。権利とPDF用途が合う場合だけ候補。今回Doclingとの差の追加根拠なし。重量級導入・契約は行わない。 |
| **Crawl4AI v0.9.4**：browser crawlと抽出統合。 | 09-23 12:14公開、**85,092 stars**、push10-05。 | Apache-2.0、コード商用可。`pip install crawl4ai==0.9.4`＋`crawl4ai-setup`。Chromium/Playwright、CPUと並列browser RAM、LLM利用時は別の費用・権利。RAM未測定。 | **C**。今回確認した更新から、既存構成を超える新しい発見方式・削減効果の具体的根拠は得られず。 |
| **NuExtract3**：文書/画像＋templateからJSONを抽出する4Bモデル。分類・検索とは別枠。 | 発表05-19、HF重み更新08-20、GGUF08-25。code repo **263 stars**、push08-17。今週の新規更新なし。 | モデルApache-2.0、code MIT、各条件下で商用可。Transformers/vLLM等。GGUF Q4_K_M約2.78GB＋projector約0.68GBは重みのfile sizeで、RAMではない。CPU手順・peak RAM未確認、公式vLLM例はGPU。 | **B（既知候補の監視のみ）**。構造化文書抽出には適する可能性。新しい小型モデル比較を開始する理由にならず、未実行。非公開vendor benchmarkを独立精度としない。 |
| **HOME’S公式sitemap**：検索結果順位に依存せずURL候補を得る入口。現在掲載系と自社archive系を区別できる可能性。 | robots.txtに**69宣言**を今回確認。初出・更新日未確認、starsは対象外。昨日Bから新技術としての差はない。 | OSSではなくサイトデータ。robotsの宣言は再利用許諾ではない。公式規約に権利留保と営利利用の制限があり、商用再利用許諾は未確認。CPUのXML処理で足りる見込みだが階層・サイズ・RAMは未測定。外部API keyは不要。 | **B維持**。2件のXML入口はWeb toolがtext/xml非対応で本文未取得。網羅性・更新追従・既存発見との差は未測定。公式`/archive/`をWebArchiveと混同せず、検索機能へ組み込まない。 |

一次情報:
[Docling 2.135](https://github.com/docling-project/docling/releases/tag/v2.135.0)、
[2.136](https://github.com/docling-project/docling/releases/tag/v2.136.0)、
[2.137](https://github.com/docling-project/docling/releases/tag/v2.137.0)、
[不要OCR回避の変更](https://github.com/docling-project/docling/commit/c6ef3c413b5bdb98bd2ae81a0a3d62ce711f58ec)、
[PP-OCRv6互換変更](https://github.com/docling-project/docling/commit/0e1567bbb8a543b364aec234fc1abe2ff9aff7ba)、
[RapidOCR 3.10](https://github.com/RapidAI/RapidOCR/releases/tag/v3.10.0)、
[RapidOCR重みライセンス](https://github.com/RapidAI/RapidOCR/blob/v3.10.0/python/MODEL_LICENSES.md)、
[PaddleOCR言語](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PP-OCRv6/PP-OCRv6.en.md)、
[Crawlee RC](https://github.com/apify/crawlee/releases/tag/v4.0.0-rc.1)、
[Lightpanda 1.0](https://github.com/lightpanda-io/browser/releases/tag/1.0.0)、
[Marker](https://github.com/datalab-to/marker)、
[Crawl4AI](https://github.com/unclecode/crawl4ai/releases/tag/v0.9.4)、
[NuExtract3発表](https://about.nuextract.ai/blog/nuextract-3-release)、
[モデル](https://huggingface.co/numind/NuExtract3)、[GGUF](https://huggingface.co/numind/NuExtract3-GGUF)、
[HOME’S robots](https://www.homes.co.jp/robots.txt)、[公式規約](https://www.homes.co.jp/kiyaku/)。

## 方法

検証仮説は「図形が重なるだけのnative textを除外し、文字レイヤーで説明できないベクトル図形は残す」。
ネット上の新たなページ発見runではなく、公開ライブラリのCPU領域選択アルゴリズムの再現とした。
`AGENTS.md`、`docs/cloud-lab.md`、関連 `.agents/skills/bot-blocking-scenarios/SKILL.md` を確認。
サイト別browser scenarioやgrounding探索は実行していない。
一次情報の候補調査はAGENTS指定のLunaへnative spawnで委譲。要求モデル指定は記録したが、実モデルIDの独立検証はできず `model_runtime_verified=false`。

1. Doclingのimmutable revision `d0f55469c56d38d93ed47049b8c9d17f6785e94b` からMITのPDF5個、upstream test、LICENSEを取得。計21,837 bytes。
2. upstreamの13領域・期待eligibility・20反復を、実行前に`protocol.json`へ固定（SHA256 `4a86d010f4422c5198d621c8bbc6fca5b1cff902c1fc2186c47523bef50510df`）。ラベルや境界を結果に合わせて変えていない。
3. 同一CPU・共通依存でDocling-slim 2.135→2.137を別プロセス実行。旧版packageのみ`PYTHONPATH`で差し替え。Python3.12.14、docling-core2.101.1、docling-parse7.22.2、pypdfium2 5.14.0、rtree1.4.1、numpy2.5.3、scipy1.18.1。旧版当時の全環境再現ではなく、共通依存下の版比較。
4. threaded、pypdfium2、threadedのnative queryを無効にしたspatial fallbackの3経路。各条件を別page/backendで初期化。layout予測は固定のTEXT領域を注入し、`_find_pdf_aware_layout_ocr_rects`だけを呼ぶ。直接呼ぶprivate methodなので将来のAPI互換は保証しない。
5. 初回選択、warm20回、全矩形、native text、errors、RSS、import時間、process内経過時間を保存。39条件×2版＝78結果。繰返し矩形は全件一致。
6. 入力hash、protocol、共通依存、case key、native text一致を別スクリプトで再検査して数値を再計算。GPU、OCR/layoutモデル、API推論、Kaggleは利用しない。

環境はAMD EPYC 9V74、可視/affinity5CPU、cgroup CPU quota4相当、memory max16GiB。
今回のselectorには約149MiBで足りたが、Doclingの完全なOCR pipelineのRAM要件とは異なる。
導入の経過時間は独立計測していない（未確認）。pipの全ログとlockを保存した。

## 結果・既存比較

| 指標 | 2.135.0 | 2.137.0 |
|---|---:|---:|
| OCR対象の条件数 | 39/39 | 21/39 |
| upstream期待値と一致 | 21/39 | 39/39 |
| 不要とされる領域をOCR対象にした条件 | 18 | 0 |
| 必要とされる領域を除外した条件 | 0 | 0 |
| 全warm選択の中央値（ms） | 21.763 | 24.434 |
| import（秒） | 0.552 | 0.783 |
| process内経過（秒、import・780 warm選択等を含む） | 31.648 | 34.539 |
| peak RSS（MiB） | 147.42 | 149.37 |
| 実行エラー | 0 | 0 |

各経路で13→7領域。除外された6種類は、罫線付き本文、native textのhighlight、filled rule、
panel端の交差、shaded band、十分な本文を含むsidebar。
残った7種類は、空領域、native＋vector、vectorのみ、stroked glyph、rule＋glyph、
panelと結合したglyph、native captionだけのvector figure。
**空領域もupstream仕様上OCR対象**であり、「残ったすべてに実際の認識価値がある」とは言わない。

既存repoにDocling実測baselineは見つからず、昨日候補の版を対照にした。
PR19のretrieval、PR23のstate classificationとの数値横比較はタスクが違うため行わない。
upstreamの修正挙動を再現できたことが今回の追加知見であり、未知の文書への一般化や新しい独自発見ではない。

## 問題・想定との差・未実施

- OCR対象18条件の減少は確認できたが、領域選択自体は約12%遅い中央値となった。1巡・固定順・小fixtureなので性能差を一般化しない。全体時間の改善には回避できるOCR推論コストとの比較が必要。
- PDFはupstreamが修正用に用意した英語・合成fixture。39条件は13領域の経路違いで独立39文書ではなく、期待値一致率を実OCR精度と呼ばない。
- layout推論、rasterization、OCR文字認識、画像/bitmapとの混在、日本語、ページ全体、HTML/Markdown変換、独立実文書での内容保持は未実施。v2.135のHTML保持改善を今回検証したとは言わない。
- v2.135→2.137には他の変更も含まれる。出典commitとの整合と3経路の再現は確認したが、単一commit ablationではない。
- HOME’S XML2件はWeb toolが`Unsupported content-type: text/xml`。サイトの拒否・空のsitemapとは判断しない。宣言以外のURL数/網羅率/lastmod鮮度は未確認。
- 公式規約の可視版は2021-09-06第20版。商用再利用の積極的な許諾は確認できなかった。規約確認をサイトデータのライセンス取得とは扱わない。
- source file名の初回照会404、default mode調査時の`lang`未指定ValidationError、pip cache書込不可警告を`investigation-errors.json`へ保存。実験runnerの78結果には影響なし。
- 指定のlabテストは151件中135成功・15skip・1失敗。`test_offline_diagnostics_guards`で既存 `.deps/bot-diagnostics` に`robots-parser`なし。通常CI・依存修正は行わず、失敗ログを保存。新旧PoC環境の`pip check`は成功。
- 新版/旧版の標準依存はlock、PyPI wheel hash、実行したOCR実装source hashで特定可能。巨大モデル重み・認証情報は保存しない。

## 導入判断・次

**採用・機能統合は保留。今回の保存は調査コミットのみで、draft PRを増やさない。**
Doclingには前処理削減の具体的な可能性が残るが、現状の結果だけでは検索前処理を速くしたとは言えない。
次に価値が出る条件は、権利を確認した独立PDF集合で、実際のOCR呼出数・全体時間・日本語/図中文字の保持を同時に比較できること。
該当入力がない間はモデルdownloadやKaggleを始めない。NuExtractを理由に別会話の比較も重複開始しない。

HOME’Sは公式sitemapから得られる新規URL集合を既存発見法と比較でき、対象の利用条件と取得範囲が明確になる場合だけ次候補。
単なる順位付け、PR19の検索追加、通常CI修正、merge、deployは今回実施していない。

保存物:

- `experiments/daily-tech-20261010/`: 実行・集計・source取得コード、固定protocol、5PDFとMIT license、依存lock、再現手順。
- `benchmarks/daily-tech-20261010/`: 78結果、全timing、summary、一次ソースmetadata、環境、導入・検証・エラーログ。
- この報告。公開ブランチ/commitのリンクは作業完了時の応答に記載する。
