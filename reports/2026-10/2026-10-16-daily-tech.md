# 2026-10-16 JST 朝の実用技術調査

## 結論

**新規に深掘りした系列はLightOnOCR-3、判定B（条件付き）。A 0 / B 1。モデル推論0、形式検証17入力、機能PRなし。**

小型0.8Bでも本文に領域座標・種別・図表データを付けられる新世代を確認した。検索順位付けではなく、画像文書を構造化する独立OCR用途の候補。ただし日本語精度・CPU速度・RAMは未測定。

公式パーサーの通常仕様6例は6/6一致。一方、こちらの採用条件5例（座標検証2、原文保持3）は0/5。実在model IDのprofileは5/5一致し、未知server名を拒否する採用条件は0/1だった。**モデルの失敗率ではなく、構造化JSONだけを後段へ渡す場合の制約**を特定した。raw出力の保存、座標の検証、served model名の明示が必要。公式pipeline自身はraw Markdownも保存しており、pipeline全体で原文が消えると主張しない。

## 概要・着手前の確認

観測は2026-10-15 22時台UTC＝**10/16 07時台JST**。独立branch `research/2026-10-16-daily-tech`、base/main `7460493cc3f7242748c95f711e71a59537ef34ea`。

- remote branch一覧、open PR6件、open issue #24、最近のissue comments、PR28のcomments/reviews、最新mainとSKU引継ぎを確認。PR28 head `ae63d65` は軸ラベル対応の別担当作業。PR26 `e614a3f`、PR25 `62daa3e`。この研究に必要な未承認の新しい外部操作は見つけていない。全会話の承認履歴を取得した意味ではない。
- 10/10〜15朝報告を取得して対象・既往PoC・候補を照合。10/15朝 `42d625af16fc46c6876febbfa2c53d96cd5ae765` と夕 `bfd6667bb210195df7ec061b66861306dcb80311` の実報告全文を確認した。LightOnの記載は確認した朝報告6本・夕報告1本にない。全過去データの網羅検索を意味しない。[照合manifest](../../benchmarks/daily-tech-20261016/prior-artifacts.json)。
- 最新SKU final handoffは固定resume-s000 v1の正常完了・15,616records回収・採点を記録。古い承認待ちやProxyErrorを現状としない。Kaggle APIでの再照会、job操作、SKU推論、枝の変更は一切なし。
- PR19 mLateOnは停止継続。Squeezの修復超過による停止、BitNetの採用保留も継続。夕方state 5,133件（旧1,072保持）に書込みなし。Work Assistant draftも適用しない。
- `AGENTS.md`、`docs/cloud-lab.md`、checkoutのbot-blocking skillを確認。今回そのサイト診断手順やjse.lab grounding探索は実行しない。personal-context skillは適用条件を読み、ユーザーの明示履歴＋repoで必要文脈が揃うため追加検索しない。
- 独立調査を規約どおり`gpt-6-luna`指定で1agentへ委譲。実サービスmodel IDの独立確認はできず `model_runtime_verified=false`。最初のScrapling A提案は10/13既報を見落としていたため親が棄却し、agentも撤回。重複を新規成果に数えない。その後、LightOnの形式契約を独立読解させた。

[open work snapshot](../../benchmarks/daily-tech-20261016/open-work.json)。メール・merge・deploy・Actions cron変更なし。

## 方法

一般web検索で候補を見つけ、公式GitHub API・固定commitソース・Hugging Face APIとrevision固定model card/configを確認。24一次資料、128,113bytesをURL・取得時刻・SHA256付きで保存した。codeとmodelの更新日を区別し、HF公式authorのLightOnOCR系列最大50件を確認。全分野の網羅巡回や夕方radarの再実行はしていない。

[取得コード](../../experiments/daily-tech-20261016/collect.py)、[source manifest](../../benchmarks/daily-tech-20261016/manifest.json)、[実行方法](../../experiments/daily-tech-20261016/README.md)。モデル重みは取得せず、file sizeはAPI metadataで確認した値。

## 候補・比較

**LightOnOCR-3：B。** 公式発表は[10/8](https://huggingface.co/blog/lightonai/lightonocr-3)。従来LightOnOCR-2-1Bから、本文だけでなくラベル付きbox・画像説明・chartのtable化を扱う。0.8B/4BはQwen3.5系列、1Bは従来architecture。`grounding` promptを使う。著者のH100/vLLM速度とbench値を今回の測定に転記・外挿しない。OCR＋layoutの一体化には価値があるが、取得網羅性や検索前処理時間の改善は未証明。

実在IDとmetadata（10/15 UTC取得）:

|ID（すべてlightonai/）|revision|作成UTC / 最終更新UTC|BF16重みbytes / parameters|likes / downloads|
|---|---|---|---:|---:|
|LightOnOCR-3-0.8B|`36d8636925327c671bd8e479c517a004f7566bd9`|10/07 / 10/09|1,706,030,528 / 852,985,920|66 / 3,463|
|LightOnOCR-3-1B|`23d544085c886bbdace647bcfab91203299417e2`|10/07 / 10/09|2,011,367,464 / 1,005,647,872|24 / 2,614|
|LightOnOCR-3-4B|`b71010f095dc3735de7ee6b9969bff8f8f50b39a`|10/06 / 10/09|9,078,620,536 / 4,539,265,536|95 / 9,012|
|LightOnOCR-2-1B（比較用旧世代）|`be7c41313c30e940fe5571de59e5fabc779e00c8`|01/16 / 10/08|2,011,367,489 / 1,005,647,872|839 / 444,861|

旧2のcard更新日を新世代公開日と混同しない。公式系列APIでは3より新しい世代は未確認。likes/downloadsは人気の観測値で品質・実採用数ではない。

- **開発・community:** [新client repo](https://github.com/lightonai/LightOnOCR)は44 stars、9/30作成、10/8 push。保存されたmain履歴は1commit・contributors APIも1人。release一覧は空。`pyproject.toml`の0.1.0はsource内versionで、PyPI公開版とは確認していない。モデル系列の普及と、この新clientの成熟度を別に扱う。
- **license/商用:** codeのApache-2.0本文を保存。各3系model cardもApache-2.0と商用利用を明記。今回APIのファイル一覧に独立LICENSEファイルは見当たらず、model側はcardの宣言を根拠とする。入力文書や外部依存の権利は別。
- **導入:** clientはPython>=3.10、OpenAI client/Pillow/pypdfium2。ローカルserver用extraはvLLM 0.30.0＋Transformers 5.16.1を固定する。repoはvLLM 0.27.1のloop、Transformers 5.17と1B系の互換問題を記載するが、今回は未再現。model cardのTransformers直接経路とvLLM extraの条件を混ぜない。全体導入は中〜高。
- **資源:** CPUで実行したのは標準Python parserだけ（GPU不要）。モデルのCPU動作・実RAM・GPU最小VRAMは未確認。0.8Bの全listed files合計1,737,771,835bytes（依存なし）で、2GB予算へruntimeまで収まる保証はない。1B重みだけでdecimal 2GB超、4Bは約9.08GB。file sizeをRAM値にしない。今回はGPUを必要とする判断に進まずKaggle設定/残quota/accelerator/privateを確認していないため、GPU実行の可否も未確認。
- **運用制約:** fixed clientのpage長辺は0.8B/4B=2048px、1B=1540px。`render_pdf`は指定ページ画像をlistで保持し、`ocr_pages`もfutureを全ページ分作る。concurrencyだけで文書全体のメモリが有界になるわけではない（静的確認、OOM未測定）。小分けページ指定が必要。公開例のmax token設定をCPU実行時間の保証としない。

一次資料：[0.8B card](https://huggingface.co/lightonai/LightOnOCR-3-0.8B/blob/36d8636925327c671bd8e479c517a004f7566bd9/README.md)、[1B](https://huggingface.co/lightonai/LightOnOCR-3-1B)、[4B](https://huggingface.co/lightonai/LightOnOCR-3-4B)、[固定client](https://github.com/lightonai/LightOnOCR/tree/36755d461be079737860a5f03ae0c803501269e9)、[依存](https://github.com/lightonai/LightOnOCR/blob/36755d461be079737860a5f03ae0c803501269e9/pyproject.toml)。

## 限定PoC・結果

公式`grounding.py`と`models.py`を改変なしで実行。全ソースを事前確認し、合成入力・期待値を[protocol](../../experiments/daily-tech-20261016/protocol.json)に固定した（SHA256 `1625e73e2ab21afe2627fc15cf966021fdedca81e9b96c13007aa4c3dae58a85`）。

|群|期待一致|観測と解釈|
|---|---:|---|
|通常仕様6例|6/6|boxのpixel換算、継続段落、複数行table、未知label、既定で脚注保持、明示filter時の脚注除去|
|採用条件：不正座標|0/2|1001座標、逆順boxとも受理。構文parserであり座標validatorではない|
|採用条件：本文保持|0/3|無marker本文→空、最初のmarkerより前の本文→欠落、code fence内marker風文字列→別block化|
|実在model名profile|5/5|1/2世代grounding拒否、3系の0.8B/1B/4Bのmode・長辺選択|
|採用条件：未知server名|0/1|custom名でもgrounding許可。upstreamが文書化したfallbackでありbug認定ではない|

**通常仕様と採用条件を合計して「精度11/17」と呼ばない。** 後者の期待値は原文を失わず不正座標や未知modelを黙認しないというこちらのconsumer要件で、upstreamがその保証を宣言したわけではない。モデルが実際にこのような出力を生成する割合は測っていない。

`HEADER_FOOTER`はfootnoteも落とす。明示指定が必要で既定は保持するため、通常使用が無条件に脚注を消すとはしない。規約や注記を必要とする文書抽出では、このoptionの採用条件に注意する。

[全入出力・時間・hash](../../benchmarks/daily-tech-20261016/probe.json)、[実行条件](../../benchmarks/daily-tech-20261016/execution.json)。Python3.12.15、1CPU、RAM/swap256MiB、networkなし、nonroot1000、read-only、cap-drop ALL、no-new-privileges、pids32、tmpfs16MiB、host mountなし。Docker公式imageをdigest固定。モデル・認証・個人ファイルを渡していない。timeout30秒、exit0、stderr空、修復0。

probe内wall **0.003496秒**、peak RSS **23,312KiB**、container込みwall **0.703662秒**。これは17個の小さい形式入力の合計で、OCR throughputや旧版との高速化ではない。モデルweight download0、推論0、pip install0。image pullの転送byte量は未計測。repo履歴fetchにも既存データが含まれ、全network転送を2GB未満と断定しない。候補の重み・依存はdownloadしていない。

## 問題・想定との差

- 想定した「構造化結果だけで原文と座標を安全に後段利用」は成立しなかった。raw Markdownを残す公式pipelineの挙動は妥当だが、JSONだけの消費者には追加検証が必要。
- helperや合成入力が正常に動いても、実画像の日本語認識、図表数値の忠実性、領域の正しさ、hallucination率は分からない。Doclingのconfidence、RapidOCRの言語設定監査ともタスクが違い横並びにしない。
- 新しい検索改善候補としてScraplingを数える提案は、過去レポート照合で却下。前提改善の根拠なしにAを増やさない。archive-free discoveryの新しい有意な改善は今回確認できなかった。
- Web toolでHF blobs APIをopenした1件はアクセス不可。通常の公開API収集では成功しており、model非公開や認証障害を意味しない。隔離実行エラー・auto-review拒否はなし。

## 導入判断・次

**B維持、統合は保留。** 次に価値があるのは、用途に合う権利確認済みの実文書で、plainとgroundingの文字・脚注・図表値保持を独立正解と照合すること。まず0.8Bだけについて依存込みdownload/RAMを見積もり、CPU又は既存無料GPUの条件を確認してから推論を計画する。今回のモデル系列新規性だけで大きなdownloadやKaggleジョブを始めない。

今回得たものは実用機能ではなく採用判断のための観測なのでdraft PRなし。検索coverage・前処理時間削減を達成したとも主張しない。モデル本体をA評価して未実行にしたのではなく、全体は初めからB、安価な形式契約確認だけを行った。

## 保存・検証

[専用audit](../../benchmarks/daily-tech-20261016/audit.json)で24sourceのbytes/SHA、固定入力・コードhash、17入力の期待/結果整合を確認。自作Python ASTと`git diff --check`も確認。本体・実験基盤・既存CI変更なしのためlab全体テストは対象外。push後のexact remote commitと関連CI結果は最終応答で報告する。
