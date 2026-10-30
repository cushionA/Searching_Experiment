# 2026-10-31 JST 朝：モデル世代と実行経路の更新監査

## 結論

**即実測へ進めるA候補なし。新規推論0、候補コード実行0、費用0円。** LayaのC++/ONNX追加、PageSiftの本文欠落修正をBに残す。Jeff現行版はadapter前提の方向転換であり、旧モデルのゲーム成績や速度を現行版の根拠にしない。昨日のOpenDecider戦術48出力、Laya、日本語ModernBERT、THX-01は再実行していない。実用機能の追加がないためdraft PRなし。

一次APIのレスポンス・モデルカードと取得日時/hashを[証拠](../../benchmarks/daily-tech-20261031/source-manifest.json)へ保存。[オフライン検査](../../experiments/daily-tech-source-audit/verify.py)はファイルのbyte/hash/JSON/名前重複を検証するだけで、モデル比較ではない。[検査結果](../../benchmarks/daily-tech-20261031/verification.json)。網羅的な新技術探索や第三者による性能再現を達成したとは主張しない。

## 既存作業との分離

AGENTS.md、関連skill一覧とbot-blocking-scenariosの適用条件、cloud runtime/network skillを確認。bot検証、jse.lab grounding、Kaggleは未実施。本環境には動作中の候補process/containerなし。別環境の不明childまで不存在と確認したわけではなく、10/30夕方UNKNOWNを再起動しない。

remote main `4d2984a454fc766137784fd6a51579e10b6fafd6`、直前朝 `4ffcf459973359c20c370b57b3ff30f9d5c1bdcb`、TinyJev `ca7b14a3a1c14fb0dec20d94d82624b313194979`、夕canonical `ced6335934ed9307058efef2a734b38ad0c8b4ab` を照合。10/29・10/30朝報告とopen PR19/21/23/25/26/28を読み、朝の専用branchを直前朝から作成。mainの新成果物は既読のKaggle監視・停止検証merge #29までで、その後のmain更新なし。SKU作業、400M索引、夕state/未完了9,514に変更なし。

## 小型判断モデル・NLP（検索とは別枠）

A=今すぐ小規模実測する価値、B=条件付き、C=今回不要。コミュニティ数は取得時の観測で、性能・安全性の証明ではない。

|対象・一次資料|版・世代・条件|判断|
|---|---|---|
|[Laya v0.4.18](https://github.com/nandhakishorm/laya/releases/tag/v0.4.18)、[C++ PR1420](https://github.com/nandhakishorm/laya/pull/1420)|10/30 10:47 UTC公開、34,706 stars/3,104 forks、Apache-2.0。ORT1.30.0を使う任意C++ backend追加、既定OFF。Linux x64/macOS arm64ライブラリ取得をCMake側でhash照合。モデル `convaiinnovations/laya-multilingual` は10/3 revision `1720e3e3357cfe1e281542e223f8273b0890ca34` のまま|B（配備経路）。前報の「C++ skeleton」は現行版にはそのまま適用しない。一方full-multilingual exportはshape不整合でOpen拒否と作者記載。通常multilingualと混同しない。作者の一致試験は当方の品質/速度実測ではない。新規native実装の監査・配布graph整合確認が未完了なので実行保留。前回の日本語品質Cを更新しない|
|[Jeff現行base](https://huggingface.co/mstrasser/jeff-base)、[repo](https://github.com/firelex/jeff)|v1.3、revision `398768e9104be9894b48244f87faca4e120186f2`、10/6更新、repo1,570 stars。Qwen3.5-0.8B系、base重み1,706,027,688 bytes＋readout522,320＋tokenizer19,989,492。公式uv/source配備、CPU/CUDA/MLX・別GGUF経路。Apache-2.0 baseでもadapterごとの条件は別|B（用途別分類）、C（今回そのまま戦術置換）。現行はadapter前提。既存の戦術adapterを確認できず、追加学習は未承認。モデルカードはv1.3配備速度/GPUメモリを未測定と明記。旧0.8Bの32thread CPU463msを今回CPUやv1.3へ転用しない。GGUFがあるため重量だけで実行不能とは断定しない。sanctions/socなど非商用条件のあるadapterをbaseライセンスで上書きしない|
|[Kev0.8b](https://huggingface.co/jaredpalmer/kev-0.8b)、[0.6b](https://huggingface.co/jaredpalmer/kev-0.6b)|repo9,719 stars/666 forks、Apache-2.0。0.8bは10/1 `bf75a6a8848ea6960ff2ed108d9ed44c2941174f`、0.6bは9/20 `dece6dba8d43f0f7ded45e9f5b9df12474d90843`。現行0.8/4/9/27bと旧0.5/0.6/8bを区別。0.5bはsuperseded、0.6bは旧Qwen3の速度重視選択肢|B。小さいIDの発見を新後継と数えない。0.6bもbase別取得とhead.ptを扱う公式source/依存の監査が必要。Appleの作者速度をCPU期限内率にしない。前日0.8b予算見積りを勝手に増額せず未実行|
|[OpenDecider nano ONNX](https://huggingface.co/manjunathshiva/opendecider-nano-ONNX)|10/10 revision `5137ae7b4f1cf8c55f1d05d3f5cdd3694d92da3e`、SDK0.9.0、repo37 stars。前日と同じ。Apache-2.0|C（昨日の戦術用途）。small/medium/largeという上位群の存在だけを品質/予算改善の根拠にしない。昨日の結果を再実測として数えない|
|[日本語ModernBERT](https://huggingface.co/argos1111/modernbert-ja-310m-jev)|9/19 revision `07cda23579443e7a33c0f474114279fa032340d6`、前回と同じ|B（独立日本語NLP評価）。新重みなし。既存比較を維持し、戦術能力へ外挿しない|

ゲーム評価は性格・履歴・目的維持・誘いの継続/変更/断念を対象にする。単に明示規則を解いたり、選択肢に書かれた結果を選ぶゲーム公開成績は、この要件の実証にならない。新実測がないのでレイテンシ中央値/p95、初回、RSS、期限内率、古い状態の失効率の新数値もない。昨日の48出力を独立48問とは扱わず、自作ルール・自作採点の一般優位も主張しない。

## クロール・前処理・ブラウザ・OCR・アーカイブ

|対象|確認した更新・利用条件|判断と次の条件|
|---|---|---|
|[PageSift0.81.2](https://github.com/YOLOW92/pagesift/releases/tag/v0.81.2)|API公開10/29 20:17 UTC（JST10/30）、175 stars/19 forks。AGPL-3.0-only/商用別契約。HTTP本文が空のclient redirectでbrowserの最初の本文を取得する変更。browser依存、RAM/依存総量未検証|B。前処理時間・本文欠落の改善仮説は目的に合う。作者269ページreplayの1worker110.7→88.0秒、総文字+174は作者測定であり当方実測でない。「zero loss」を任意サイト保証にしない。新規source/dependency監査と同一入力・同一timeout・欠落判定の固定を済ませてから比較。今回ビルド/インストールは行わない|
|[Browsertrix1.26.5](https://github.com/webrecorder/browsertrix/releases/tag/v1.26.5)|10/29公開、490 stars、AGPL-3.0。profile保存後dialog再表示の修正。Helm/Kubernetes配備|C（今回）。収集網羅性や前処理速度の新機能ではなく、クラスタ導入/RAM検証なし|
|[browser-use0.13.14](https://github.com/browser-use/browser-use/releases/tag/0.13.14)|10/26公開、119,595 stars、MIT。Python/browser/model providerが必要|B。前日既知版。動的操作が既存欠落原因と分かる場合のみ。0円provider構成とRAM未検証|
|[Crawl4AI0.9.5](https://github.com/unclecode/crawl4ai/releases/tag/v0.9.5)、[Trafilatura2.3.1](https://github.com/adbar/trafilatura/releases/tag/v2.3.1)|10/14・10/6公開、86,613・7,046 stars、Apache-2.0。前者Python/browser、後者Python本文抽出|B。前日既知版。本文抽出品質/同一予算coverageの新実証なし。Crawl4AIのattribution要件も確認対象|
|[PaddleOCR3.7.0](https://github.com/PaddlePaddle/PaddleOCR/releases/tag/v3.7.0)|6/11公開、92,005 stars、Apache-2.0。前日既知PP-OCRv6、CPU/accelerator導入候補|B。日本語画像欠落の固定評価が必要。モデル別RAM・商用配布条件・品質未実測。SKU画像作業に介入しない|
|[ArchiveBox0.9.77](https://github.com/ArchiveBox/ArchiveBox/releases/tag/v0.9.77)|10/27公開、28,907 stars、MIT、browser/capture tools依存|B。前日既知版。未知サイト発見を改善したとは言わない。保存運用の必要性がある場合のみ|
|[Common Crawl index catalog](https://index.commoncrawl.org/collinfo.json)|最新CC-MAIN-2026-43、収集10/5〜10/18、次は2026-39。既存公開索引を使う代替設計候補|B。本文アーカイブの自己保存を伴わない発見候補。ただし今回catalog確認だけでCDX検索・WARC取得・新規サイト発見0。利用規約/収録原文権利の監査と独立target coverage測定が済むまで採用しない。400M索引は触らない|

MIT/Apacheのコードは商用を一律禁止するものではないが通知等の条件を保持。AGPLには公開・ネットワーク提供時の義務があり「商用不可」と単純化しない。法務判断ではなく配布元の宣言の整理。各モデル/adapter/データの条件を混同しない。

## 検証・予算・保留

保存一次資料のhash検査と `git diff --check` を実施。新しい推論/実験基盤の実装はなく、汎用lab/CI保守のための修復は行わない。push後のexact-SHA CIとremote SHAは最終応答へ記載する。

取得は公式メタデータ・README・PR差分のみ、モデル/wheel/container取得0。保存レスポンスbyte数はverification.jsonに記録（圧縮wire総量ではない）。全.git約72MiBと証拠1MiB未満にWeb取得余裕を含め、取得の保守的予算枠100MiBを予約した概算でありwire実測ではない。2GB上限を増額しない。CPUモデル実験0秒/20分、修復0/1、支出0円、Kaggle submit0。実験隔離の安全条件は維持し、未監査candidate sourceはhostでもcontainerでも実行していない。

既存保留：TinyJev累計3GB再取得は**承認待ち・未実行**、Moli/Lightpanda追加修復とSqueez修復上限は**未解決・未実行**。夕UNKNOWNは**結果不確定のまま**で、この朝から再起動していない。今回のauto-review拒否なし。PageSift/Laya新native経路は**導入監査未完了による保留**であり、実行失敗や権限拒否ではない。直ちに新たな権限・予算・追加学習を求める段階ではない。
