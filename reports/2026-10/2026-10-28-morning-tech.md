# 2026-10-28 JST 朝：Layaの配布版と公開decision応答を監査

## 結論・概要

**LayaはB（条件付き）。中国語の業務判定では、公開保存応答の独立再集計で誤った行動割当が多く、確率0.8の絞込みでも解消しなかった。** 公式の多言語対応・校正の説明だけでは、日本語の判断部品への採用根拠に足りない。一方、単一multilingual checkpointの必要ファイルは約678MBで、小型CPU比較の有力候補として残る。

今回は**新規推論0、候補コード実行0、公開保存応答768件と位置変更200予測の自作監査**。実測したのは監査処理だけ。TinyJevの48問を流用・再開せず、モデル性能や速度を当方の実測と呼ばない。日本語候補 `argos1111/modernbert-ja-310m-jev` もBとして配布・適用範囲を確認した。

## 進捗・重複確認

main `4d2984a454fc766137784fd6a51579e10b6fafd6`、前朝 `43d1c7b9f3384481033f4fa90ff36e65ada52cf0`、夕canonical `398863ba6611ec4c2c502c9cd5533a0922528108`、TinyJev保存 `1e114a06aacafd2779b53d010920e5d9f368f36f` をremoteから照合。研究branchの報告135コピー・内容43種でLaya/modernbert-ja-310m-jevの名称記載なし。名称一致の検査であり全成果物の意味的重複不在は保証しない。open PRも確認し、PR19検索追加停止・PR23既報NLP・SKU対象外を維持した。

AGENTS.mdとbot-blocking skillの適用範囲を確認。今回は通常の一次資料読解で、jse.lab groundingやbotシナリオ実行ではない。独立branch `research/2026-10-28-morning`、朝の指定3ディレクトリだけへ保存。夕方state/pending9467、TinyJev、Moli/Lightpanda、SKUを変更しない。[照合記録](../../benchmarks/daily-tech-20261028/progress-review.json)。

## 候補・目的・配布条件

### Laya — B：日本語NLPと行動選択の比較前に現行版の小試験が必要

[公式repo](https://github.com/nandhakishorm/laya)、[最新release v0.4.15](https://github.com/nandhakishorm/laya/releases/tag/v0.4.15)（2026-10-24 19:17 UTC）。取得時34,480 stars / 3,091 forks、contributorsは先頭100件まで確認し総人数としない。repo作成9/18、最終push10/26。コミュニティの大きさは精度証明ではない。

- **新規性と目的**：生成せず、choice・真偽・段階評価を一つのencoderで返す。非英語をmultilingualへ振り分ける。検索rerankingやSKU照合とは分離した、本文の意図・関係・行動判断用候補。自由文抽出や画像OCRをできるとはしない。
- **版の不一致**：HFモデルカードは0.3.20の説明、PyPI/最新releaseは0.4.15、取得したmain `b9fe351ce81e2d974bca2bf219c1e4030c0a05ea` のpyprojectは0.4.16。mainを公開stableと混同しない。0.4.15は言語検出、SDK、評価gate等の更新で、重みの新モデルではない。
- **正確なモデル**：`convaiinnovations/laya` rootは英語421M、`convaiinnovations/laya-multilingual` は322M、`convaiinnovations/laya-typed-decisions` は421M。root repoのsubfolderと独立repoは同じmultilingual重みのLFS SHA `9d628fd9…8f204`。作者のモデル一覧でも別の新しいLaya後継は確認できず。別repo/ONNX変換を新モデル数に数えない。
- **固定revision**：root `7b928d828b7b0e022f929d9bd2e44165aa270148`、multi `1720e3e3357cfe1e281542e223f8273b0890ca34`、typed `e929ae5cf69bc34259cd2f95c9e91145b818b1f0`。現行HF更新日は10/3、モデル作成は9/18〜19。
- **導入・資源**：Python>=3.10、候補手順は `pip install laya==0.4.15`（今回は未実行）。CPU経路あり、GPU必須ではない。multilingual本体643,835,514 bytes、tokenizer/config込み5ファイル678,201,636 bytes。3モデル重み合計2,329,053,944 bytesで**preload全3種はDL2GB枠外**。候補依存込みDL、CPU時間、peak RAM8GB適合は未測定。公称33msをCPU速度として使わない。
- **利用条件**：コード・モデルカードはApache-2.0表記、商用利用を想定するが通知等の条件を保持する。今回利用する中国語benchmark contributionはMIT、元出典とLICENSEを保存。契約判断や商用導入は行わない。
- **検証価値**：日本語の否定・担当者・取消し・条件分岐と、明示ルールからのgame actionを別集計し、単純ルール/小型モデルと比較できる。現在の固定試験は中国語NLPのみで、ゲーム/日本語性能は未確認。

### modernbert-ja-310m-jev — B：日本語関係判断の対照候補

[公式モデルカード](https://huggingface.co/argos1111/modernbert-ja-310m-jev)、revision `07cda23579443e7a33c0f474114279fa032340d6`。9/19作成・更新、15 likes / 510 downloads（期間依存）、stars・開発者総数は未確認。作者model一覧で同名後継を確認せず。

質問＋状況と各候補のcross-encoder。Jev本体の再現ではなく、日本語のNLI・意図分類等を共同学習した約310Mモデル。CPUのTransformers `AutoModelForSequenceClassification` 経路、F32本体1,260,829,436 bytes。正しい日本語prompt形式・512 tokenの末尾切捨てを守る必要があり、RAM/CPU時間/全依存込みDLは未測定。GPU必須とはされない。導入版を今回固定実行していない。

重みはCC BY-SA 4.0表記、baseはMIT、学習データはCC BY-SA/CC BY/MIT。商用禁止表記ではないが出典・継承条件を確認する。作者の学習済みJGLUE test成績をzero-shot一般モデルより強い証拠と混同しない。作者の未知タスクではlivedoor35.6%、JMMLU34.8%で比較LFMより低い。これらは作者値。業務16例の高成績は小規模で、game action・自由抽出・日本語一般性能を保証しない。

## 方法・結果：公開応答を独立再計算

[コード・手順](../../experiments/laya-source-audit/README.md)、[数値](../../benchmarks/daily-tech-20261028/audit-results.json)、[出典manifest](../../benchmarks/daily-tech-20261028/source-manifest.json)。Python3.12.14標準ライブラリのみ、候補のコードはテキストとして保存しimport/実行しない。

[公式repo内の中国語benchmark](https://github.com/nandhakishorm/laya/tree/b9fe351ce81e2d974bca2bf219c1e4030c0a05ea/research/benchmarks/feishu_zh)は9/23追加、**実応答の収録は9/21**。64合成例・8系列・4ラベル各16件。3repeat×2方式×2backend=768応答のID・正解・全応答復号・重複欠落を検査し、384組のrequest hash一致を確認。品質は元プロトコルのrepeat=0を使った。ラベルはAI補助合成で独立人手goldではなく、事前pilot12例がprompt設計へ影響している。

|公開保存応答|正答/64|不要な行動割当/32|必要行動の見落し/32|
|---|---:|---:|---:|
|Laya multi / choice|20|22|6|
|Laya multi / four_noul|18|30|0|
|Jev 1.13.0 / choice|64|0|0|
|Jev 1.13.0 / four_noul|63|0|1|

four_noulは4つの真偽を公開固定閾値で組み合わせる別方式。Layaでは60/64をurgentにしていた。各方式の数字を混ぜない。Layaのchoiceはnoiseを1件も返さず、繰返し3回とも20正解だった。反復の一致は正しさではない。

**今回追加した事後診断**：Laya choiceの最大確率>=0.8は12件中5正解、不要行動5/8。返却confidence>=0.8は4件中1正解。両欄は同じ意味の数ではない。最大確率での閾値を上げれば信頼できるとは言えず、当該入力への記述統計に限定する。閾値最適化・校正・新規推論はしていない。

保存時root revision `1c5edc17…c982` と現在root/独立multi repoのLFS hashを照合し、3重みは同一。**旧結果を無関係な古い重みとして捨てる根拠はないが、runtime修正で現在の応答が変わらないとも断定できない。** 重み本体は未取得で、API metadataの一致である。

長文位置の[別保存予測](https://github.com/nandhakishorm/laya/blob/b9fe351ce81e2d974bca2bf219c1e4030c0a05ea/research/results/position_sensitivity_multilingual.json)200件も再集計。4k paddingでは同20問の正答が位置により11〜19、7kでは8〜17。「8k対応」を位置に依存しない判断品質と扱わない。この英語部署選択はgameAIではなく、同20問の反復を200独立課題とも呼ばない。

## 比較・問題・制限

- モデル推論0・候補install0・候補CPU0・修復0・支出0・学習0。監査のみ0.0169秒、peak RSS15,352KiB。資料取得2,283,487 bytes。監査速度/RAMをモデル推論へ転用しない。
- 中国語archiveのLayaはM4 MPS・旧runtime、JevはネットワークAPI。速度/費用/ハードウェア優劣を比較しない。Jevの有料APIは呼ばない。
- 自作監査の初回はmanifestの拡張子不備、次に確率和許容差の差を検出。Jevの後続repeat1の1応答は確率和0.99だった。公開仕様の0.01許容差へ合わせ、厳しい0.001超の逸脱を別欄へ残した。確率・正解・予測を修正せず、first-repeat品質に影響なし。[エラー記録](../../benchmarks/daily-tech-20261028/errors.json)。
- 外部候補コードの実行は既存の未解決承認を引き継ぎ、**Laya/日本語モデルのinstall・import・推論は未着手/pending**。今回の自動承認拒否ではない。TinyJev第3追加修復、Moli/Lightpanda追加依存も未着手。停止点の別環境迂回、BUILDX_CONFIG変更なし。計算不足による停止ではないためKaggleへ迂回しない。
- ローカル既存labテスト151件は1失敗/15skip。既存`.deps/bot-diagnostics`の`robots-parser`不足で、監査や候補の失敗ではない。依存修復は対象外。[ログ](../../benchmarks/daily-tech-20261028/local-lab-tests.log)。新しい基盤機能は実装していない。
- Playwright/Crawlee/Crawl4AI/Trafilatura/PaddleOCR/Browsertrixの公式release先頭2件も保存。Browsertrixは今回APIでv1.26.3（10/22）を確認し、前朝記載の1.15.1を最新値として継承しない。新たな検索coverageや前処理短縮を実証するものは今回選定せず、検索機能PRを作らない。OCR/vision・archiveの紹介数を埋める追加試験なし。

## 採用・次

採用B、現行版の実推論を条件にする。小さな日本語NLPとgame actionを別々に固定し、選択肢順序・否定・取消し・構造化/自然文の差を検証する価値がある。Layaはmultilingualだけ取得し、Router全preloadを避ける。ただし今回新ジョブや重複TinyJev試験を準備・投入しない。外部コード実行の承認と隔離条件が解決した後の候補として残す。

実用機能なし、draft PRなし、merge/deployなし。研究成果を独立branchへ公開し、remote SHAとexact-SHA CIは最終返答へ記す。
