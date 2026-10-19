# 2026-10-20 JST 朝の技術調査

## 結論

**A0 / B2 / C0（詳細候補2件）。候補実行・推論・性能改善の実測は0件。** 新しい有用な確認はSelectolax 1.0.1のtemplate補完APIの境界と、ppu-indra-ocrの軽量文書OCR移植における利用条件・速度分布の注意点。いずれも条件付きB、導入保留。通常の不具合修理や既存ブラウザ比較を研究成果にしない。

- **Selectolaxの`template_fragments()`は1回で入れ子全体を平坦化しない。** 上流テストでは外側の呼出しは1 fragment、内側へはそのfragmentからもう一度呼ぶ。通常のCSSで拾えないtemplate内の本文・リンクを補完する候補。ただし未表示templateの文字列を表示本文や到達可能URLとして無条件に索引へ入れない。
- **ppu-indra-ocrは小さな重みでも無条件の商用利用候補ではない。** コードApache-2.0と重みのmodified OpenRAIL-Mは別。競合利用条項には研究例外がなく、利用適合は未確認。著者のSROIE 50枚比較も、中央値188対220msだけでは優位といえず、p90は822対434ms、CERは0.156対0.123。日本語での当方実測はない。

## 概要・既存作業

2026-10-19 22時台UTC（10/20 JST朝）。main `7460493cc3f7242748c95f711e71a59537ef34ea`から独立branch `research/2026-10-20-daily-tech`。

`AGENTS.md`、`docs/cloud-lab.md`、`.agents/skills/bot-blocking-scenarios/SKILL.md`を読解。今回の作業は通常の公開資料読解で、jse.lab grounding探索・bot診断・radar実行ではない。規約に従い補助一次調査をgpt-6-luna指定agentへ委譲し、親が原文・数値を照合。内部実モデルIDの独立検証はできず`model_runtime_verified=false`。

朝10/10〜19、夕10/12〜19を候補名照合し、直近朝夕とMoli比較を読解。Trafilatura 2.3.1は10/11既報のため除外。Moli1.1.18、Heritrix3.18、WebMCP、SetFit1.2、LightOnOCR3、Crawl4AI0.9.5、NCM、DLite、Frozen語彙を新発見として再掲しない。照合は全歴史の意味的重複不在の証明ではない。

remote heads、open PR19/21/23/25/26/28、issue24とcommentsを保存。SKU/ai-task-autotunerは別担当、取得・実行・変更なし。PR19 mLateOn検索追加は停止継続。順位並べ替えのみの提案なし。

夕方canonicalは`98394155ff9767ca83c8b6ee8996c63a4d260213`。state4ファイルのbytes/hashを読み取り、queue **5,236件**を確認。state・夕方branch変更なし。10/19夕の意味監査失敗を成功や確定ABCに読み替えない。

Moli比較`a7abb9ff9396cbe2b22a04a7497a5de603bec9fa`はlibfontconfig不足、修復1回後の追加修復判断待ち、crawl測定0を維持。Squeez/Harness²/YOLO26nの修復上限停止、PP-LCNet停止、BitNet保留も維持。新環境を再開理由にしない。自動化停止質問は再質問せず、通知・メール・cron・merge/deployを変更しない。

## 方法

GitHub API metadata/releases、固定tagのREADME・実装・上流テスト・license、PyPIの配布metadataをテキスト保存。取得URL/UTC時刻/bytes/SHA256/失敗を[manifest](../../experiments/daily-tech-20261020/manifest.json)へ記録した。ソースコードをimport/compile/実行せず、依存・重み・browserを導入しない。上流テストの読解を当方のテスト成功と数えない。

[取得・監査手順](../../experiments/daily-tech-20261020/README.md)、[数値](../../benchmarks/daily-tech-20261020/audit.json)、[文脈・state hash](../../benchmarks/daily-tech-20261020/context.json)、[open PR](../../benchmarks/daily-tech-20261020/open-prs.json)。公開Web検索も実施したが、検索の相対時刻ではなく公式APIのpublished_atを使用。広範な新着巡回や夕方キューの採点は行わない。

## 候補・評価

A=現条件で直ちに試す価値、B=条件付き、C=今回は見送り。ABCと実測・採用の有無は別。starsは関心指標で品質や独立採用実績ではない。

|候補・目的と新規性|版・日付・開発/community|導入・権利・CPU/GPU/RAM・運用|判断・テスト価値|
|---|---|---|---|
|**Selectolax**：静的HTML処理。1.0.1のtemplate content APIによる本文/URL不足補完。scripting設定でnoscriptのHTML5解釈を選択。archiveに依存しないが、サイト発見アルゴリズムそのものではない。|v1.0.1 **2026-10-17 17:53:20 UTC**公開。1.0.0は10/3 15:01:11 UTC。1,693 stars、push10/17 18:42:30、公開issue2件。上流にtemplate/scriptingテストあり、独立採用規模は未確認。|MIT（商用利用は条件遵守）、Lexbor等依存の別条件も確認が必要。`pip install selectolax==1.0.1`、Python>=3.9,<3.16、wheelなし環境はCython/buildが必要。CPython3.12 Linux x64 wheel **1,563,464 bytes**、未DL。C/CythonのCPU parser、GPU必須の記載なし。RAM最小・8GB内実動作・全導入量未測定。JS実行/通信/ブラウザlayoutなし。|**B**。既存取得HTMLのtemplate内に必要な本文/URLがあると確認できた時、fragment補完前後の回収集合・誤混入・前処理時間を測る価値。対象未確定なのでA・PoCにしない。一般parser速度比較や通常移行修理だけを目的にしない。|
|**ppu-indra-ocr**：Indra文書OCRのTypeScript/ONNX移植。段階別結果、layout、Markdown、PDF native text layer利用をJS runtimeで扱う。新しい学習済みOCR方式ではない。|v0.1.1 **2026-10-17 13:00:00 UTC**初回working release、v0.1.2 **同日14:14:39 UTC**。repo作成10/14、push10/17 16:38:21、1 star/issue0。新しいcommunity portでDatalab公認ではない。作者のPython0.3.1とのgolden比較、独立利用実績未確認。|コードApache-2.0、重みmodified OpenRAIL-M。売上/調達$20M超の条項にpersonal/research例外、競合条項にはその例外なし。自身の適合未確認。`npm install ppu-indra-ocr@0.1.2 onnxruntime-node`、PDFには`@embedpdf/pdfium`。CPU既定/GPU任意。著者の重み約18MB（math込み38MB）は依存総量・RAMではない。最小RAM未確認。concurrency既定1、初回モデル取得・キャッシュ・WASMの外部取得設定に注意。|**B**。NLP/検索順位と別の文書OCR用途。JS内で処理を完結させる必要があり、重み利用条件と日本語品質を確認できる場合だけ検討。速度/品質とも移植元や既存OCRより優れているとは未証明。停止済みOCR案件の代替実行として使わない。|

一次資料：[Selectolax release](https://github.com/rushter/selectolax/releases/tag/v1.0.1)、[固定実装](https://github.com/rushter/selectolax/blob/v1.0.1/selectolax/lexbor/node.pxi)、[template tests](https://github.com/rushter/selectolax/blob/v1.0.1/tests/test_templates.py)、[PyPI metadata](https://pypi.org/pypi/selectolax/1.0.1/json)、[Indra release](https://github.com/PT-Perkasa-Pilar-Utama/ppu-indra-ocr/releases/tag/v0.1.2)、[固定README](https://github.com/PT-Perkasa-Pilar-Utama/ppu-indra-ocr/blob/v0.1.2/README.md)、[重みlicense](https://github.com/PT-Perkasa-Pilar-Utama/ppu-indra-ocr/blob/v0.1.2/models/LICENSE)、[license status](https://github.com/PT-Perkasa-Pilar-Utama/ppu-indra-ocr/blob/v0.1.2/LICENSE-STATUS.md)。

## 結果・比較

Selectolaxではtemplate contentは通常document treeに含まれず、`css()`で到達しない仕様。上流testの外側fragment経由で入れ子へ進む必要がある。SVG namespaceのtemplateは通常要素で、APIはfragmentとして返さない。これは静的読解で当方の実動作確認ではない。`scripting=True`はnoscriptをraw textとして扱うparser設定で、JSを実行する指定ではなく、既定動作も変わらない。既報Crawl4AIの壊れたnoscript救出とは目的/入力境界が異なる。1.0.0でModest/parse_fragmentが削除されているため、単なる既存importの差替えは不可。1.0.0の最大5倍/10倍という著者記載を1.0.1の実測改善にしない。

Indraの固定READMEはApple M1/Bun1.4.2での9反復と、SROIE 50枚/別process比較を記載。Indraの中央値改善とp90悪化が共存し、token-F1改善とCER悪化も共存する。著者のpeak RSS1,162MBもこの母集団の値で、Linux/日本語/入力解像度/全runtimeのメモリ保証ではない。portのモデル18MBをRAM18MBとしない。v0.1.2のstandalone PDF等の修正だけを新研究価値に数えず、文書処理の移植設計を候補としている。

小型モデルは[MiniCPM-V公式README](https://github.com/OpenBMB/MiniCPM-V)の4.7/1B系列も予備読解した。これは詳細候補/ABC数に含めず、作者の機器別claimをOCR性能として外挿しない。NLPは検索用途の制約と独立扱いであり、SetFit等既報を新規扱いせず、今回は新たな詳細候補に至っていない。小型モデル・NLP・OCR全分野の網羅や、新技術不存在を主張しない。

|監査項目|当方の結果|
|---|---:|
|保存試行 / 成功payload hash・bytes照合|18 / 17|
|保存payload|385,567 bytes|
|取得wall合計 / offline監査|12.718秒 / 約0.002秒|
|Python|3.12.14|
|候補実行 / モデル推論 / 性能trial|0 / 0 / 0|
|依存・モデルDL / GPU / 現金 / 候補修復|0 bytes / 0秒 / 0円 / 0回|

監査時間はOCR/parser処理時間ではない。Git fetch後のローカル.gitは約288MiB、転送wire量そのものは未計測。2GB制限に対して大規模配布の取得は行わず、既存index/rawも移動・削除しない。

## 問題・採用・次

README.rstへの資料取得が404、treeにあるREADME.mdへ1回訂正して取得成功。実行時の修復ではない。新候補の隔離・権限適合は検証しておらず、外部コードの実行承認や隔離成功を仮定しない。今回はBのread-only調査で終え、A PoCなし。auto-review拒否なし。staged diffの全体whitespace検査は原本licenseの末尾空白を検出したため、証拠bytesを保持して自作ファイルのみの検査を別実施した。

Kaggle未使用。local性能不足を測っておらず、既存認証・無料残量・accelerator・private scopeは未確認。追加認証・有料計算・学習・私有データuploadなし。保留の迂回に使わない。

次はSelectolaxについて、必要な実HTMLと独立した期待本文/URLが得られた時に限り、入れ子・namespace・未表示文字列を分けて補完価値を評価。Indraは重み利用適合と独立日本語文書が先で、作者比較を反復するだけのPoCはしない。採用/統合なし、実用機能を追加していないためdraft PRなし。push後のremote exact SHA・当該commit CIは最終返信で示す。CI成功と候補性能成功は別。
