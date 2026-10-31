# 2026-11-01 JST 朝：本文抽出Coreと軽量OCRの配備条件

## 結果

**独立した新候補Trafilatura Coreを選び、固定入力と隔離実行まで準備したが、ベンチファイルの権限で未開始。性能実測0、採用Aなし。** もう一つの新候補RapidOCRLiteは、配布wheelに同梱される別ライセンス重みを実物で確認できた。どちらもB（条件付き）とする。試験数の穴埋め、既済みモデル比較、止まった試験の再開はしなかった。

## 新しく確認した候補

|対象|一次情報・配備条件|判断|
|---|---|---|
|[Trafilatura Core 0.8.11](https://pypi.org/project/trafilaturacore/0.8.11/)、[repo](https://github.com/markdownee/trafilaturacore)|10/14公開、head `b8d9124f168fb39f2392c657e624cf43c8b0e1ef`。0 stars/0 forks、GitHub Releasesは空だがPyPI wheelあり。Apache-2.0、Python>=3.10。HTML入力から本文・メタデータ・診断を返し、URLは文脈として扱う。Trafilatura 2.2.0 fast系由来の別実装で、現行本家2.3.1そのものではない。実際の依存はcourlan1.4.0、htmldate1.11.0、lxml、nh3とその推移依存。モデル/GPU不要、最低RAMは未確認。|B。本文欠落・前処理時間の改善仮説を検証する価値がある。ただし「Coreだから高速・依存が2個だけ」とは言えない。新規小コミュニティで品質優位は未実証。今回の隔離起動は下記で停止。|
|[RapidOCRLite 5.0.0a15](https://github.com/RapidAI/RapidOCRLite/releases/tag/v5.0.0a15)|10/23公開のalpha、14 stars/1 fork。最新mainは10/30の`d720b197ddc30e9db343ac102e95fb6953f0438e`でAVX2ビルド修正。C++のPP-OCRv6用runtime、汎用ONNX runtimeではない。Python>=3.9、cp312 Linux x86_64 wheel 18,733,353 bytes、PyPI SHA256一致。NumPy/Pillow/PyYAML/requests/tqdm依存、CPU ISA選択あり、Linux wheelはVulkanなし。|B。画像本文の前処理をCPUで軽くする用途。READMEの速度は作者測定で、現行mainの値を10/23 wheelの結果にしない。最低RAM・日本語品質・レイテンシは未実測。実行やモデル自動取得はしていない。|

RapidOCRLiteの**コードはApache-2.0でも、同梱Indra重みは別条件**。wheel内の`models/lite/LICENSE`と`NOTICE`を読み、元revision `ed345c7f158eddbc7dee0c6a7269afb75a94f7e8`と明示されることを確認した。前年売上2千万USD超、累計資金調達2千万USD超には個人/研究例外付き制限、Datalab競合にはその例外なし、Outputにも条項が及ぶと書かれている。「非商用限定」とだけ単純化しない。標準PP-OCRv6重みとlite重みのライセンスを混同しない。これは配布条件の記録であり法務判断ではない。[wheel静的検査](../../benchmarks/daily-tech-20261101/rapid-wheel-audit.json)。

## 実試行と停止点

[コード・入力](../../experiments/trafilaturacore-cpu/README.md)、[実行記録](../../benchmarks/daily-tech-20261101/execution.json)、[stderr](../../benchmarks/daily-tech-20261101/core.stderr)。

- 比較はCore0.8.11と本家Trafilatura2.3.1 fast。日英×6配置の12合成HTML、保持対象36段落・除外対象24片を実行前に固定。各1初回＋20ウォーム呼出し、全出力、中央値/p95/RSSを計画。Coreのmetadata/sanitizeを含むAPI全体の比較であり、内部処理が同一という設計ではない。
- wheel20個・20,397,390 bytesをbinary-onlyで取得。候補importをせず静的展開し、`.pth`/path traversalがないこと、入口・抽出・metadata経路を確認。秘密・個人ファイルなし、ネットワークなしのimageへCOPYだけで配置した。
- Docker BuildKitが`/home/agent/.docker/buildx`へ書けず失敗。修復1回として既存config/socketを維持したlegacy builderを使用し、image作成は成功した。設定ディレクトリの事前確認が不足していた。
- nonroot65534/read-only/network-none/cap-drop ALL/no-new-privileges、8GiB/2CPU/pids128を指定して起動。`/app/bench.py: Permission denied`、exit2。ファイルはroot所有mode600で、Pythonがベンチを開く前に停止した。信頼済みbase imageのshellによる読取診断で確認。今回の原因はtmpfs noexecではなく、COPYされた入力の読取権限であり、準備側の不備。
- **修復1/1を消費済みのため追加chmod・再build・root起動・他環境への迂回をしない。** 比較baselineも未開始。品質・時間・RSSの数値はnullで、失敗時間を推論時間へ数えない。追加修復は承認待ち。auto-review拒否ではない。

本件はPageSiftの再試行ではなく独立候補だが、既知のDocker設置問題を事前に十分防げなかった。次に必要なのは新候補を増やすことではなく、追加修復が許可された場合に限り、このimage内の公開ファイルと依存のnonroot読取条件を直すこと。入力や採点基準の変更は不要。

## 既知対象との差分・継続作業

remote mainは`4d2984a454fc766137784fd6a51579e10b6fafd6`で、Kaggle監視・停止検証merge #29以後の新成果なし。朝263d5074、夕1321d3fの報告を読解し、PR19/21/23/25/26/28のheadを確認した。GitHub検索が空を返したためPR一覧APIでも照合し、空を「作業なし」と扱わなかった。独立朝branchは263d5074から作成。夕の正規stateと未完了9,549、SKU、既存400M索引は変更していない。

一次APIで確認した既知版はLaya0.4.19、OpenDecider0.9.0、browser-use0.13.14、Crawl4AI0.9.5、Trafilatura2.3.1、Browsertrix1.26.5、ArchiveBox0.9.77、PaddleOCR3.7.0。朝/夕の既知版から新リリースなし。Trafilatura本家とbrowser-useを新発見Aにしない。Browsertrixのprofileダイアログ修正はC（今回の研究不要）、ほかは従前Bまたは用途別判断を維持。

Laya multilingual `1720e3e`、OpenDecider nano ONNX `5137ae7`、日本語ModernBERT `07cda235`も不変。Laya fused graph/export未検証、OpenDeciderの10/30戦術結果14/36許容・悪手10/中央値2.30秒は過去値のまま。性格・履歴・戦況からの誘いの維持/変更/断念、複数許容解、期限内率・古い判断の失効は今回新測定なし。offline結果をUnity実対戦の面白さにしない。

Common Crawlの最新catalogはCC-MAIN-2026-43のまま。索引による未知サイト発見、本文保存を持たない別設計の価値は未実測。reranking単独、PR19検索追加は停止維持。Core本文抽出は検索順位変更とは別の前処理試験。

## 検証・資源・保留

[一次資料27件のmanifest](../../benchmarks/daily-tech-20261101/source-manifest.json)、[オフライン検査](../../benchmarks/daily-tech-20261101/verification.json)へbyte/hash/URLを保存。候補調査はAGENTS指定のLuna spawnで実施し、提案を一次資料と既報で再照合した。サービス内部モデルIDは独立確認していない。cloud runtime/network/docker skillとbot skill適用条件を確認し、grounding探索・botシナリオ・Kaggleは実施しなかった。

`test_lab*.py`は151件、既存robots-parser欠落で1失敗・15skip。CI保守目的の修復はしない。新しい試験基盤のsyntax・fixture件数・一次資料hash検査と`git diff --check`を実施。remote SHA/exact-SHA CIはpush後の最終応答へ記す。実用機能ができていないためdraft PRなし、merge/deployなし。

費用0円、GPU/Kaggle submit0、候補計算0（script未開始）、修復1/1。依存wheel約20.4MB＋OCR wheel約18.7MB（同梱重みを含む）、base image展開約119MB、候補image177,134,389 bytes。wire総量は未計測、Git・APIを含む保守的予約枠500MBで2GB以内、run30分枠内。既存実験は再実行していない。

保留は継続：PageSift追加修復/別環境実行は未承認、TinyJev累計3GB再取得は承認待ち、Moli/Lightpanda/Squeezの修復上限は未解決。10/30夕launch UNKNOWNは結果不確定のまま再起動なし。今回環境の既存候補container/processは開始時に見つからなかったが、別環境の未知job不存在を証明したとはしない。
