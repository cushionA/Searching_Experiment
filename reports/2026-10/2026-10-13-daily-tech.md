# 2026-10-13 JST 朝の日次技術調査

## 結論

**A 0 / B 5 / C 4。導入・機能PRなし。** 新しい有用な結果は次の3点。

1. **Kreuzbergの後継はXberg v1.3.8。** 旧名のGitHub APIも `xberg-io/xberg` を返す。旧v4の数字だけで最新を判断すると後継を見落とす。CPU既定の一括抽出・content-hash cache・OCR fallbackは混在文書前処理の候補だが、日本語品質・総時間・RAMは未測定。B。
2. **Docling 2.138のtemplate除去はdeclarative Shadow DOMを例外保持する。** release見出しだけから「10/11のPlaywright保存内容が消える」とは判断できない。`shadowrootmode=open/closed`（大小文字を正規化）は残し、普通のtemplate、無効な値、旧`shadowroot`属性は除去する。upstreamの追加テストにも根拠がある。新しい確認はこの互換条件の一次ソース照合であり、昨日のOCR/stream計測の再実行ではない。B。
3. **SmolLM2の小ささから日本語用途への適性を推測しない。** 後継SmolLM3-3Bまで確認したが、日本語の分類・抽出精度を裏付ける資料は得られなかった。更新順最上位のGSM8K-SFTも汎用後継ではない。今回の独立日本語NLP候補としてC。

候補コード実行・モデル推論・性能PoCは0件。Aを作るための任意のfixtureや既存比較の反復は行わなかった。PoC未実施をAの除外条件にはしていない。今回Aがない理由は、既存用途に対して直ちに追加実験すべき効果仮説を一次資料から十分絞れなかったため。

## 概要・既往作業

観測は2026-10-12 21:45–22:10頃UTC（10/13 06:45–07:10頃JST）。日付全体の更新網羅ではない。main/baseは `7460493cc3f7242748c95f711e71a59537ef34ea`。独立ブランチ `research/2026-10-13-daily-tech` をmainから作成。

- AGENTS.md、docs/cloud-lab.md、現checkoutのbot-blocking skillを読んだ。サイト診断・grounding探索は実行せず、通常の公開一次資料調査のみ。研究レーダーskillは夕方commitの内容と失敗境界を読んだが、朝の別用途に実行・適用していない。サブエージェントなし。
- 10/10 `4ad359d`（Docling OCR selector）、10/11 `02d9823`（Shadow DOM）、10/12 `2609c3a`（Docling.rs streaming、Crawl4AI Cloud、Tev1）のレポートを確認。過去の性能値を今回の測定値に混ぜない。
- 夕方 `f1a9a681a4f06d33a004c3ca77d7fab5c03e58c5` のreport/skillを参照。673候補・20一次読解・444未完了、Codex judge失敗、PP-LCNet推論0/repair1上限を維持。夕方state、judge、Docker/buildxを変更・再実行していない。
- PR19（draft/open）は検索追加中止を維持。PR23（draft/open）は既往GLiNER分類の優位なしという判断を維持。PR21はopenの既存ブラウザ作業、PR22はmerged。GitHubのopen表示と「中止」という運用判断を区別。
- PR26 head `e614a3f30bde3db7112d477b070707e2bf4a78cb` を読み取り確認。10/12のv7、ASCII大小文字・軸名affixの条件付き同値証明、sidecar共有が新成果。commit説明では数字に接する大小文字は証明せず、残差のside/spanを保持する。別担当の申告・保存成果の確認であり、独立正解評価ではない。SKUのコード、入力、ラベル、推論、収集、PR25/26には変更なし。

## 方法

GitHub公開8 repoについてmetadataと直近最大3 releaseを保存。HFは `author=HuggingFaceTB&search=SmolLM&sort=lastModified&direction=-1&limit=100&full=true` の37件を確認し、主要2 model cardを読む。release日、repo push日、model作成日、更新日を区別した。これは指定author/query内の調査であり、全公開モデルを網羅した主張ではない。

一次資料の原文15件、URL・SHA-256・bytes、失敗2件を [manifest](../../benchmarks/daily-tech-20261013/primary-manifest.json) に保存。[repo metadata/release](../../benchmarks/daily-tech-20261013/discovery.json)、[PR snapshot](../../benchmarks/daily-tech-20261013/repo-status.json) も保存した。starsは観測時点であり、品質・実利用者数・採用率ではない。

A=すぐ試す価値あり、B=条件付き、C=今回不要。各判定はPoC実施状況と導入判断から独立。商用可はライセンス条件順守を前提とし、入力データ・配布モデル・外部サービスの条件を一括許諾しない。

## 結果・候補比較

|候補・目的／新規性|公開・更新／stars・開発・コミュニティ|license・導入難易度・CPU/GPU/RAM・制約|適性・追加検証価値・判断|
|---|---|---|---|
|**Xberg（Kreuzberg後継）1.3.8**：文書形式検出、抽出、OCR、一括処理をRust coreへ集約。後継系列の発見が今回の新情報。|release 10/11 01:33 UTC、push 10/12 21:14、9,403 stars。多言語binding・継続release・Discordを提供。コミュニティの実運用品質は未確認。|MIT code、条件下商用可、OCR重み等は個別。Python≥3.10のwheel/CLI、source buildはmaturin/Rustで難度上昇。CPU既定・GPU必須でないがVLMは別条件。RAM・load/throughputは未測定。`url-ingestion`は追加feature。|**B**。混在文書のcold/warm一括抽出とcache hit/missを分離し、同一品質で総CPU秒数が減る場合に有望。READMEの「最速」は採用根拠にしない。今あるPDF 1種類だけで旧Docling比較を繰り返さない。|
|**Docling Python 2.138**：普通のtemplateを本文から除き、declarative shadow例外を保持。Numbers画像等も追加。Docling.rsとは別系列。|release 10/12 06:14 UTC、push 10/12 14:15、68,772 stars。複数contributor、追加テストあり。|MIT code、条件下商用可、各モデル別。Python導入は中程度、HTML経路にGPU/OCRモデル不要。RAM/時間未測定。DOMの可視性やslot配置すべてをブラウザ同等にする保証ではない。|**B**。10/11保存HTMLを後段へ渡す場合の互換条件を確認。普通のtemplate内偽本文を除く用途はあるが、本repoの本体統合用途・実サイト増分未確定。source確認のみ。|
|**Scrapling 0.4.15**：tab再利用、CSS指定Markdown、継続セッション。8月releaseを本日の新releaseとは数えない。|8/23公開、push10/11、87,001 stars。公開issue #491が10/11に無期限待機を報告。issueの環境依存主張は未再現。|BSD-3-Clause、条件下商用可。parserだけとbrowser/rag extrasでは導入量が違う。browser経路はCPU＋browser RAM、実測なし、LLM不要のMarkdown経路あり。|**B**。連続ページのtab作成負担を減らす仮説。ただし既存session再利用に対する差が未確定。新しい一次issueは現行0.4.15にも同じawaitがあると報告し、長時間用途の評価時には完了率も必要。サイト突破試験・通常bug修正はしない。|
|**Scrapy 2.19**：reactorなし実行のaiohttp handler、RemoteControl。|9/10公開、push10/10、64,765 stars。Zyte等の継続開発。|BSD-3-Clause、条件下商用可。Pythonパッケージ導入は中程度。CPU・GPU不要、RAM未測定。reactorなし対応外componentがあり、handlerはexperimental。|**B**。既存asyncioアプリに収集処理を埋め込む場合の接続簡略化。RemoteControlの追加自体は網羅性/速度の証拠でない。本repoを移植する理由は不足。|
|**Crawlee JS 4.0 RC1**：DomCrawler、parser交換、microdata。継続監視。|10/6公開、stable 3.18.2は9/29、push10/12、26,126 stars。Apifyとcontributorの開発。|Apache-2.0、条件下商用可。JS4系はRCでpackage/API変更、導入中～高。CPU、静的DOM経路はGPU不要、RAM未測定。|**B維持**。ブラウザ不要で同じ情報が得られる入力がある場合。新releaseなし、10/10の比較を反復しない。|
|**SmolLM2 135M / SmolLM3 3B**：小型生成による分類・抽出・要約候補。系列後継を確認。|2-135M作成2024/10/31・更新2025/9/22、439 likes/1,541,620 downloads。3-3B作成2025/7/8・更新9/10、1,057 likes/740,561 downloads。モデルのためstarsの代わりにHF値。|両card Apache-2.0、条件下商用可。Transformers等、CPU例あり/GPU任意。3B BF16なら重みだけ約6GBという算術目安で、RAM実測ではない。KV cache等別。2は英語tag。3はmetadataと本文の言語一覧も不一致、日本語の保証なし。|**C今回**。NLP分類/抽出/判定として評価し、検索制約は適用していない。日本語適性の根拠不足で既存SKU比較へ追加しない。GSM8K-SFT（2026/4/3）はmath特化、単に更新順先頭だから汎用最新とはしない。|
|**warcio.js 2.4.12**：WARC streaming reader/writer。|9/4公開・push、55 stars。Webrecorder＋複数contributor。|MIT、条件下商用可。Node/browser JS、CPU、RAMはstream/buffer利用次第で未測定。WARC自体の権利は別。|**C今回**。最新差分は多値header等の修正。archive索引を使わない新サイト発見でもなく、指定WARC処理の負担問題もない。通常保守をしない。|
|**MarkItDown 0.1.8**：文書→Markdown。|9/21公開、push10/4、189,789 stars。Microsoft、多数contributor。|MIT code、条件下商用可。Python＋format別extras、導入中。通常変換CPU、OCR/外部APIは別。RAM未測定。|**C今回**。releaseは通常入力では0.1.7とほぼ同じ挙動と説明。RSS等の修正を新方式と扱わず、再比較しない。|
|**lxml 6.1.3 / 7.0.0b1**：HTML/XML前処理。導入済み6.1.1から後継を確認。|stable 6.1.3 tag 9/2、7 beta 8/22、push10/11、3,065 stars。継続開発。|BSD-3-Clause code、条件下商用可、native依存別。wheel導入低～中、CPU/GPU不要、RAM未測定。7はpre-releaseでtree locking等の変更。|**C今回**。find/findall最適化の記載だけでは現行処理のボトルネックと結びつかない。upgrade/日常テスト修正は行わない。|

一次資料:
[Xberg版固定README](https://github.com/xberg-io/xberg/blob/v1.3.8/README.md)、[release](https://github.com/xberg-io/xberg/releases/tag/v1.3.8)、[Python metadata](https://github.com/xberg-io/xberg/blob/v1.3.8/packages/python/pyproject.toml)、
[Docling 2.138](https://github.com/docling-project/docling/releases/tag/v2.138.0)、[template変更＋テスト](https://github.com/docling-project/docling/commit/ed04377eae16c84be1aefe8b9bfe42af9ecb2a5b)、[版固定backend](https://github.com/docling-project/docling/blob/v2.138.0/docling/backend/html_backend.py)、
[Scrapling release](https://github.com/D4Vinci/Scrapling/releases/tag/v0.4.15)、[一次issue #491](https://github.com/D4Vinci/Scrapling/issues/491)、
[Scrapy 2.19](https://github.com/scrapy/scrapy/releases/tag/2.19.0)、[版固定asyncio文書](https://github.com/scrapy/scrapy/blob/2.19.0/docs/topics/asyncio.rst)、
[Crawlee RC1](https://github.com/apify/crawlee/releases/tag/v4.0.0-rc.1)、
[SmolLM3](https://huggingface.co/HuggingFaceTB/SmolLM3-3B)、[SmolLM2](https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct)、
[warcio.js](https://github.com/webrecorder/warcio.js/releases/tag/v2.4.12)、[MarkItDown](https://github.com/microsoft/markitdown/releases/tag/v0.1.8)、[lxml](https://github.com/lxml/lxml/releases)、[7beta changelog](https://github.com/lxml/lxml/blob/lxml-7.0.0b1-1/CHANGES.txt)。

## 比較・想定との差

- Doclingのrelease見出しから疑った「全templateを消してshadow保存を壊す」は、実装・追加テストにより否定された。ブラウザ描画、slot配分、実サイトの完全互換まで確認した意味ではない。
- 旧Kreuzberg名へのAPI要求は後継Xbergへ転送された。READMEは旧v4をLTSと記載するが、LTSの`releases/latest`は404。LTSの不存在や保守終了とは断定しない。
- SmolLM3のmodel cardはfront matterと本文で言語集合が違う。metadataの多言語表示だけを日本語対応や後継の優位性に読み替えない。新しいGSM8K更新も小型汎用モデルの新世代ではなかった。
- 検索候補について、既存結果の並べ替えだけに価値を置く技術は採用しない。今回、新たな日本語サイトcoverage、前処理時間短縮、WebArchive不要の発見を直接裏付ける結果は得ていない。既往Common Crawl graph/Crawl4AI Cloudを件数埋めに再掲しない。

## 問題・未確認

- Scrapy公式docs URLはHTTP 403。proxy/権限を変更せず、同じ版の公式GitHub docsを読んだ。Xberg LTS latestは404のまま保存。
- 配備済みPython Playwrightは1.62.0、Chromium executableが存在しない。path照会時のdriver終了で `Task was destroyed` / `TargetClosedError` の警告が出た。ブラウザ起動・install・repairは行っていない。従ってDoclingとPlaywrightのend-to-end確認なし。
- 新規候補の依存・binary・モデルは未配備。未知ソースのinstall/実行に進まず、downloadは資料だけ。追加認証、権限拡張、課金、Kaggle起動なし。Kaggle残量/非公開設定を未確認のまま起動していない。自動承認reviewによる拒否は発生していない。
- 速度、peak RAM、日本語OCR/NLP精度、実サイト増分、商用重み条件の網羅確認は未実施。CPU利用可能という説明を安価・高速と同一視しない。

## 導入判断・次

全候補本体導入保留。Xbergは旧Kreuzbergを比較する予定が生じた際の後継候補として保存。次の問いは「同じ混在文書の本文・表・順序を維持し、cold/cache-miss条件でも総CPU時間が下がるか」。その実験を選ぶ場合は版固定の依存・隔離条件・入力を具体化し、未承認のinstall/実行について先に確認する。

Doclingは新規採用を急ぐ理由ではなく、保存HTML→変換の用途が成立した時の互換条件として残す。実ブラウザ確認に進む場合も10/11のリンク保存比較だけを繰り返さず、ordinary templateとdeclarative shadowを区別した本文出力を測る。SKUや夕方PP-LCNet/judgeの再開案に置き換えない。

## 成果物・チェック

[再取得・検算コード](../../experiments/daily-tech-20261013/README.md)、[検算記録](../../benchmarks/daily-tech-20261013/verification.json)。Python3.12.14、入力15 source snapshotのSHA/bytes、8 repo、37 model entryを確認。`verify.py` はexit 0、0.0453秒。これは証拠検算の時間であり、候補性能ではない。原文はテキストとしてのみ読んだ。

実験基盤・本体・CI変更がないためlab全体テストやrobots-parser欠落修正は対象外。JSON読込、Python AST、report内ローカルリンク、保存ハッシュを確認した。staged diff検査の初回は取得原文の末尾空白とRSTの等号見出しを検出したため、原文を改変せず専用`.gitattributes`でraw `.txt`をbyte保存・text diff対象外とした。自作コード/報告のwhitespace検査は続ける。コードは資料取得/検算だけで、用途条件を満たす新実用機能は完成していないためdraft PRを作らない。独立ブランチへ記録をpushして遠隔SHAとcheck状態を確認し、最終応答でリンクする。merge/deployなし。
