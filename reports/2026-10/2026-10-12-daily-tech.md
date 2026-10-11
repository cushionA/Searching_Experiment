# 2026-10-12 JST 日次技術調査

## 結論

本日の新発見は、Docling.rsの既存PDF streaming経路を公式CPU binaryで測った結果と、Crawl4AI Cloudの検索/SERP API提供状況。Doclingは**調査A（限定条件下の再現PoCあり）**だが、OCR品質・実スキャン資料・製品導入効果は未検証。Crawl4AI Cloudと軽量SLM候補は**調査B（一次情報から次の検証仮説）**。既存監視候補は更新だけで再実験しない。**本体導入は全候補で保留**。A/B/Cは根拠の段階を示し、導入可否とは独立である。

9候補を確認: 新発見3（Docling PoC、Crawl4AI Cloud、Tev1 SLM）と継続監視6（Playwright、Common Crawl graph、GLiNER2、Browsertrix、RapidOCR、Trafilatura）。機能PR・本体変更なし。別会話のSKU作業を読み取り参照しただけで、編集・推論・再採点はしていない。

## 方法と根拠の段階

調査時刻は2026-10-11 22:01–22:12 UTC（2026-10-12 07:01–07:12 JST）。baseは`7460493`。前日レポートと実験記録はcommit [`02d9823`](https://github.com/cushionA/Searching_Experiment/tree/02d9823b893c214913e83b752ede82279097bcae)から明示取得。GitHub release/API、Hugging Face public API/model cards、公式Cloudページを確認し、取得時刻・metadata・一部原文source snapshot・SHAを`benchmarks/daily-tech-20261012/`へ保存した。

- **調査A**: 再現可能な小規模PoCがあり、その入力・版・方法・出力・制約が保存された。
- **調査B**: 一次情報から有望な仮説は立つが、対象データで効果未検証。
- **調査C**: 現在の範囲では追試価値が乏しい、または用途を限定。
- **導入判断**: 上記とは独立。実サイト/本番データ適合、運用・license・資源を別途判断する。

公開APIのmetadata取得は実施した。検索サービス・対象サイトのクエリ、Cloud API呼び出し、モデル推論、OCRを伴う処理は行っていない。GPU/Kaggle、新規認証、課金、パッケージ依存追加もなし。作業中に環境接続の短い中断通知があったが、保存済みPoCの再起動はせず、その後の作業でソース確認と報告修正を継続した。

## 新発見と追加検証

### Docling.rs: streaming出力の限定PoC（調査A）

[v2.15.0 release](https://github.com/docling-project/docling.rs/releases/tag/v2.15.0)は2026-10-11公開。新しい差分は`--layers`/`--traverse-pictures`をstreaming Markdownと組み合わせたこと。**streaming自体はv2.14.0 CLIに既に既定として存在し、v2.15で初めて追加されたとは言えない（[v2.14 CLI source](https://github.com/docling-project/docling.rs/blob/v2.14.0/crates/docling-cli/src/main.rs)、tag snapshotも保存）。**公式タグcommit [`c3b0569`](https://github.com/docling-project/docling.rs/tree/c3b05695cda94eef81bc2ee32b0339bb12800a62)の[source](https://github.com/docling-project/docling.rs/blob/c3b05695cda94eef81bc2ee32b0339bb12800a62/crates/docling/src/stream.rs)ではPDFだけがページ単位の真の逐次経路で、深さ8の同期channelが消費側に合わせてproducerを止める。PDF変換pipelineとOCR設定の後、完了ページをMarkdown chunkで渡す。非PDF backendは全DoclingDocumentを同期的に作ってから出力するためfirst-byte短縮効果はない。CLI [source](https://github.com/docling-project/docling.rs/blob/c3b05695cda94eef81bc2ee32b0339bb12800a62/crates/docling-cli/src/main.rs)上、`--layers`とpicture traversalはexport-only。これらがOCR/入力処理を減らす根拠はない。

repoは保存時96 stars、MIT codeの小規模公開プロジェクト。公式Linux x86_64 release binary（CPU実行、archive 22,775,046 bytes、SHA-256 `9e309b913cd6a1e904eab22bf938c29ed9adea9656fa076db773ff95cb9e5185`）を使用。Rust toolchainはなく、追加installせず既成binaryでCPU実験した。自作200頁テキスト層PDF（150,147 bytes、SHA-256 `0c3475006cf8884b0cae592c1608035f7430eedc3f9b23eb96753913ba786b6e`）で`--text-layer-only`、stream既定値と`--no-stream`を交互に3回ずつ測定。**同じv2.15 binary内の出力方式比較であり、v2.14→2.15の効果比較ではない。**

|指標|stream|buffer|
|---|---:|---:|
|first byte 中央値（別途2回ずつ）|0.0122秒|0.2040秒|
|全処理時間中央値|0.2107秒|0.2069秒|
|子process peak RSS中央値|22,160 KiB (21.6 MiB)|28,340 KiB (27.7 MiB)|
|出力|485,999 bytes、6試行すべて同一SHA-256 `841284bd049de3cd279ee82df10fb8b0903a77f198ea6c6b1417039283b9d528`|同左|

この合成text-layer入力では初回出力が早く、peak RSSは約6.0 MiB低い一方、総時間は同程度でstream側がわずかに長い。小さい合成PDFでのプロセス単体値であり、実スキャン、大きな画像、OCRモデル、全system RSSの改善を示さない。今回OCRを実行しなかった理由はOCR実測の主張が目的でなく、text serializer/逐次出力の最小切り分けに限定したため。OCR効果は未確認。

**判断**: 技術的なstream出力は入力範囲内で再現した。長文PDFの後段consumerが逐次出力を使う場合にだけ検討価値あり。次の有用な追加検証は、スキャンPDFで同一モデル設定のstream/bufferを比べ、byte同等性、first-byte、total、peak RSS、OCR結果を別々に記録すること。すぐ本体導入は推奨しない。

### Crawl4AI Cloud: 新しい検索出口（調査B）

[公式API page](https://api.crawl4ai.com/)は`/search`、Google SERPの順位・blockを返すとする`/serp`（2026-10-08 beta）、`/scrape`、`/extract`、experimental `/answer` を掲示。No-card free creditsと$10からのcredit packを広告するが、soft launchで価格/上限変更あり。これは85,254 starsのself-host OSS（Apache-2.0）とは別サービスで、host版は別契約、API keyを要する。ホスト側CPU/RAM・精度・日本語coverage・provenance・latencyは未確認。

**判断**: 検索発見源を補完する仮説はあるが、まだ品質の証拠ではない。サービス問い合わせ自体はしていない。将来、固定queryでsource増分とprovenanceを比較する問いが具体化し、認証・外部送信・費用の許可が得られた場合だけ検討する。新認証・課金なし。

### 軽量SLM: Tev1候補、系列最新とは分けて評価（調査B）

[Tev1-0.8B model card](https://huggingface.co/togethercomputer/Tev1-0.8B-experimental)は0.8B級の実験的decision modelで、基盤をQwen3.5-0.8Bと記載。Hugging Face APIの観測snapshotはexact ID `togethercomputer/Tev1-0.8B-experimental`、revision `6bb2dff14b38fea90ddb14d870166ccaf77374e9`、2026-09-25更新時点のAPI値は2,202 downloads/20 likes。Q8_0 Ollama配布は約797 MB級。CPU/GPU/RAMの実測はなく、そのruntime/download経路に本タスクで導入していない。モデルカードは英語中心で、日本語のSKU/文書抽出適性は未評価。weight licenseはfinalization pendingとの記載があり商用導入適合は未確定。4B variantもAPI metadataに記録したが、この用途に優位との根拠なし。導入候補ならCPU RAM要件は未測定で、HF safetensors合計parameter countは約873M（これはbytesではない）；license上の不確実性があるため配布導入しない。

「小型用途に最適」とは言えない。HF author/search API query `author=Qwen, search=Qwen3.8, limit=100, sort=lastModified, direction=-1`で2026-10-11に6件が返り、主要公開系列候補としてQwen3.8-27B（BF16 27.78B parameters）、Qwen3.8-Flash-Next（125B total/6B activeとcard説明、FP8 variantもあり）、Qwen3.8-2.4T-A95Bを確認。これらはTev1よりはるかに大きく、今回のCPU軽量候補ではない。**このauthor/search queryで得た公開一覧の範囲であり、小型派生/第三者quantized model全体を網羅したとは主張しない。**各ID/revision/更新・safetensors parameter metadataとquery結果を`model-sources.json`に保存。parameter数をweight bytesと取り違えない。

**判断**: 0.8B級ローカル推論候補として追跡する価値はあるが、本体導入は保留（日本語/ライセンス/精度未確定）。別進行中SKU比較に混ぜず、必要が生じたら独立日本語評価で判断する。推論は未実施。

## 既存監視候補6件

GitHub stars/license/releaseは2026-10-11時点のAPI snapshotで、コミュニティ支持や保守品質を保証しない。コードlicenseとモデル/データ/host契約を分ける。

|候補|確認できた更新・資源/制約・目的|評価|
|---|---|---|
|[Playwright 1.64.0](https://github.com/microsoft/playwright/releases/tag/v1.64.0)|10-07 release、97,494 stars、Apache-2.0。Chromium CPU利用可。前日fixtureは保存HTML shadow link 2/4→4/4だがlive locatorは既に4/4。|B維持。実サイト増分未確認、同じPoCを再実行しない。|
|[Common Crawl graph embeddings](https://github.com/commoncrawl/web-graph-embeddings)|1 star、更新9-07、code Apache-2.0。degree≥8の古いsnapshotで約52.9M×128 vectors。FP16 denseだけ約13.5GB相当でindex overhead別。CC archive由来であり本文取得不要とarchive非依存は別。|B維持。日本語seedの新規host価値/coverageを未検証。|
|[GLiNER2 code 2.1.0](https://github.com/fastino-ai/GLiNER2/releases/tag/v2.1.0) と [GLiNER2.5 small model](https://huggingface.co/fastino/gliner2.5-small-v1)|pip package/releaseとmodel seriesは別物。repo v2.1.0は10-09、2,356 stars、Apache-2.0 code。HF model APIで`fastino/gliner2.5-small-v1`（74M parameter相当）、`fastino/gliner2.5-base-v1`（194M）、`fastino/gliner2.5-multi-v1`（287M）、`fastino/GLiNER2.5-Decide`（486M）を個別に確認。small/base/Decideは英語タグ、multiはmultilingualタグ。weight licenseとCPU RAMはモデルごとに別確認が要る。|B監視。系列新モデルの存在だけでSKU比較を再実行しない。複数schema用途が独立に発生した場合のみ評価。|
|[Browsertrix 1.15](https://github.com/webrecorder/browsertrix-crawler/releases/tag/v1.15.0)|10-02、1,159 stars、AGPL-3.0。Docker/browser worker、RAMは並列度次第で未測定。既存archive QA向け。|B維持。archive collectionなし、archive外探索ではない。|
|[RapidOCR 3.10.0](https://github.com/RapidAI/RapidOCR/releases/tag/v3.10.0)|10-08、8,110 stars、Apache-2.0 code。CPU ONNX可、GPUはruntime構成依存。RAM/速度未測定。|B維持。実画像の新しい時間差なし、OCR比較を繰り返さない。|
|[Trafilatura 2.3.1](https://github.com/adbar/trafilatura/releases/tag/v2.3.1)|10-06、6,957 stars、Apache-2.0、CPU。本文抽出修正は通常保守更新。|C。今回の網羅性/処理時間改善につながる新方式なし。|

## リポジトリ成果物の読み取り

main調査base `7460493`。現在の別作業は読み取り確認のみ。

- PR26 head [`4c408376`](https://github.com/cushionA/Searching_Experiment/commit/4c408376640ed4d26e6b67eb815e89c10f0aafc2), [`align_sku_conditions.py`](https://github.com/cushionA/Searching_Experiment/blob/4c408376640ed4d26e6b67eb815e89c10f0aafc2/experiments/sku-matching/align_sku_conditions.py), handoff [`claude-next-steps-and-codex-advice-20261010.txt`](https://github.com/cushionA/Searching_Experiment/blob/4c408376640ed4d26e6b67eb815e89c10f0aafc2/experiments/sku-matching/claude-next-steps-and-codex-advice-20261010.txt), test [`test_lab_sku_align.py`](https://github.com/cushionA/Searching_Experiment/blob/4c408376640ed4d26e6b67eb815e89c10f0aafc2/tests/test_lab_sku_align.py) （commit固定のリンク）。 現在の変更は共通価値条件を先にまとめ、残差条件だけ説明確認へ送る処理。337 matched/815 unmatchedを維持し、固定サイズ軸の41件は引用根拠で解決したと記録。大規模GPU実験は停止中。これらは開発途中の成果であり、独立評価ではない。
- Read-only commits [`4a9f54b`](https://github.com/cushionA/Searching_Experiment/commit/4a9f54b128b7f46720da2de2a2f87ac5a10f1d31), [`3507a936`](https://github.com/cushionA/Searching_Experiment/commit/3507a936bed75c27cd73806be3fb297c4789c7b1), [`435a1582`](https://github.com/cushionA/Searching_Experiment/commit/435a1582240058994fec14a2b39dd9d9423b7e75) 関連成果物を読み取り確認した。コードやSKUファイルは変更せず、比較も再実行していない。
- 1,383商品掲載・644 SKUの機械ラベルは独立goldではない。評価を **A: 正しい根拠を固定した判定、B: 根拠選択、C: 統合判断** に分ける。商品単位・series単位のsplitを区別し、実データと合成例も分ける。根拠内部の状態（支持・矛盾・不明）と最終判断（採用・除外）は別軸。不明は最終採用から除外する。通常運用で人手確認を必須にしない。固定商品ペア内でSKU条件の対応を先に行い、未対応条件だけAU名・説明全文で根拠を確認する。変更中のSKU調査を中断・置換・重複しない。

## 成果物と次の判断

再現コードと手順は `experiments/daily-tech-20261012/`、取得資料・実行結果・環境は `benchmarks/daily-tech-20261012/` に保存した。[ソース証拠](../../benchmarks/daily-tech-20261012/source-evidence.json)、[モデルカード証拠](../../benchmarks/daily-tech-20261012/model-card-evidence.json)、[実在ID・revision・系列一覧](../../benchmarks/daily-tech-20261012/model-sources.json)を参照。調査AはDoclingの合成テキスト層PDFに限り、実サイトやOCRの導入効果を示さない。CloudとSLMは一次情報に基づく調査Bで、導入はいずれも保留。

保存済み結果の検算で本処理6試行・first-byte測定4試行の正常終了と出力ハッシュ一致を確認した。新しい実測は再起動していない。JSON読込・manifestハッシュ・staged whitespaceチェックを実施。実験基盤や本体コードの変更がないため、既存lab全体のテストや通常CI修正は行っていない。
