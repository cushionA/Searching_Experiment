# 2026-10-31 JST 夕方 Research Repro Radar

## 結果

**朝より新しいLaya 0.4.19とPageSift 0.81.5を確認。PageSiftの配布hash・ELF互換条件は検査できたが、隔離環境のnoexecで起動できず、実行成功・モデル推論は0。** 採用Aや「大きな効果」は認定しない。試験数を埋めるための既済み比較は行わなかった。

新規収集は1,687一意レコード（HF等1,684＋監査対象の新版/配布形式3）。carryを加えた全9,651入力を採点し、8,184件がranked、review_pool20件を全件読解。追加5テーマの導入監査も実施（quantcheckはpoolと重複）。元の未完了9,514件を全件保持し、record_run後は**9,549件**。推薦・完了IDはともに空で、保留を完了へ移していない。

## 新版と実行可能性の監査

|対象|確認できたこと|判断・残る条件|
|---|---|---|
|[Laya 0.4.19](https://github.com/NandhaKishorM/laya/releases/tag/v0.4.19)|10/31 07:43 UTC公開。routing診断、hooksなし時のcontext/OS entropy省略、AMP入力検査等。0.4.18で追加されたC++17/ORT1.30のOpen/Run/Closeとキャンセル監視のソースを確認。READMEの「skeleton only」はコードと矛盾する古い説明。|B・配備経路。通常multilingualの重みrevisionは10/3の1720e3e3357cfe1e281542e223f8273b0890ca34で不変。baseリポジトリにはfused laya.onnxがなく、checkpointだけでC++推論できるとは言えない。export/graph整合・実CPU/RSSは未検証。Apache-2.0の宣言を確認。旧日本語品質を新版の速度修正で改善したとは扱わない。|
|[PageSift 0.81.5](https://github.com/YOLOW92/pagesift/releases/tag/v0.81.5)|10/31 05:46 UTC公開。Linux CLI/Node配布をglibc2.28ビルドへ変更。x86_64 CLI archive 9,668,925 bytes、SHA256 sidecar一致、展開binary23,621,368 bytes。ELF最大要求glibc2.28を当方で確認。|B・配備確認のみ。起動は下記の環境制約で未達。AGPL-3.0-only、商用別ライセンス選択肢。AGPLを一律商用不可とはしない。旧glibc上の動作、抽出品質、CPU/RSSは未測定。0.81.2のredirect修正の作者269頁replay値を今回の実測として転用しない。|
|[StartLux v1.1 0.8B Instant GGUF](https://huggingface.co/startlux-models/StartLux-Decision-v1.1-0.8B-Instant-GGUF)|現行repo commit a99977d9332decc9fb27a2488b4a83b533b6cd5f。モデル本体6496ae641cc71b149ce9dad69bd97f2a44e7bf11、GGUF529edd1b9dae38fda3ba6a5ab0cbc785c91e8359、10/14更新。Q4_K_M529,296,992 bytes、Q8_0811,843,168 bytes。0.8B/2B Instantと4B/9B/27B Instant・AutoThinkを区別。|B・研究候補。コードApache-2.0、重みCC-BY-NC4.0。商用ゲームにそのまま配備する条件ではない。GGUFなら重量だけで2GB超とは言えないが、tokenizer/config、non-causal attention、1microbatchとserver設定の整合確認が必要。通常の因果推論で代用すると別モデルになる。CPU実行監査・日本語戦術品質は未確認。|
|[TokenEconomizer](https://github.com/microsoft/TokenEconomizer)|10/15論文、HF trending3位。反復作業のmicro-skills、未使用toolの省略、失敗ループ介入。作者は277 SWE-Benchタスクで平均18.4–30.9%コスト低下を報告。Python3.10+、Apache-2.0。|B・AI開発別枠。子CLI/モデル呼出しと設定ファイルへのhook導入を伴うため今回のnative-only経路では導入しない。作者値の独立再現、ホスト互換、節約量は未測定。無料と書かれた別サービスへ接続していない。|
|[quantcheck v3.4](https://github.com/aditi-p31/quantcheck)|10/29 release、論文v3も10/29。GGUFのhash一致と機能正常を分ける監査。v3では一部の原因をbackend差からchat template欠落へ訂正。コードMIT、監査データCC-BY4.0、Ollama/EvalPlus依存。|B・量子化配布監査。15コード課題のscreenは日本語分類や戦術の能力試験ではない。既存48出力試験を再実行する理由にしない。配布モデル取得や生成コード実行は未実施。独立した複数の話題性根拠は今回確定できず、詳細推薦カテゴリにはしない。|

Layaの2msポーリングという実装値は、2msで停止できた測定結果ではない。StartLuxの25–52msは作者のH200測定で、CPUやゲームの期限内率ではない。27Bのゲーム動画を0.8Bの能力へ外挿しない。今回もゲーム評価は性格・戦況・履歴に基づく誘いの維持/変更/断念、複数許容解、レイテンシ/失効を対象とし、明示条件分岐の置換とは分ける。

大量・雑なデータではPageSiftの本文欠落低減、Layaの短い判断に対する呼出し負担削減が仮説になる。ただし今回throughput/1万件換算は測れていない。APIやGKE/AWSより安いとは認定せず、profileの過去価格を現行価格として引用しない。

## PageSiftで止まった操作

対象は上記配布CLIの`--help`起動。nonroot65534、read-only、network-none、cap-drop ALL、no-new-privileges、RAM8GB、2CPU、pids128を維持した。

1. 読取り専用bindでの起動はOCI runtimeの`stat /input/pagesift: permission denied`で未開始。
2. 修復1回として公開binaryだけをtar/stdinでtmpfsへ搬入。再起動は`/tmp/input/pagesift: Permission denied`で未開始。
3. 読取り診断でmode755・所有者65534と、`/tmp`の`rw,nosuid,nodev,noexec`を確認。noexec解除、root起動、特権化、追加修復へ進まなかった。

これはauto-reviewによる拒否ではなく、実行環境の実行権限制約。本文抽出も速度試験も未実行であり、静的なGLIBC symbol検査だけでは旧glibc環境での起動成功を示さない。候補はpendingのまま。[静的検査と診断](../../benchmarks/research-repro-radar/2026-10-31/pagesift-compatibility.json)。Lightpandaの追加修復には触れていない。

## 探索・判定の範囲と限界

HF trending全50、新着50、10クエリの全返却結果から994一意。MDPI2誌とCrossref5クエリを追加し、40件で打ち切っていない。OpenReview5要求は403で未取得、迂回なし。APIの結果上限と返却されたページの範囲であり、Web全体の網羅性ではない。Crossrefの非ゼロ埋め日付を正規化し、年月/年だけの情報は原表記を保持して日付不明にした。初回採点206 invalidを最終0へ解消。原レスポンスと初回順位を保存している。

最初からRADAR_LLM=native。ホストのgpt-6-luna指定spawn→終了→request ID/input hash/schema/期待値照合exit0のprobe成功後だけ探索。サービス実モデルIDはunknown。CLI fallback、APIキー追加・移設なし。

20件の判定はschemaを通ったが、初回答の関心範囲の狭めすぎと言語を1回修復した後も、5件に引用の厳密一致違反が残った。追加修復を重ねず、この5件のnative判定を確定推薦や完了根拠へ採用していない。別5テーマ監査では、Layaの古いREADMEを優先した誤読を1回修正し、修正版の引用は原文と一致。schema成功は意味内容の正しさやモデル推論成功を保証しない。[照合記録](../../experiments/research-repro-radar/runs/2026-10-31-evening/quote-check.json)。

quantcheck/Yama/Bekkoにnativeのtest案はあったが、quantcheckは適用先の生成artifact未選定、YamaはIndic中心で今回日本語戦術への適合が不明、Bekkoは既存の検索順位用途見送りを覆す独立した前処理評価設計がないため実験しなかった。穴埋め実験やPR19検索追加は行っていない。

## 保存・資源・保留

正規remote ced6335934ed9307058efef2a734b38ad0c8b4abから独立worktreeへ復元。上流Skill最新fe6925cd75838a9b52130f284cb3b931bfaf36dbの指定8ファイルを全取得・読解し、補助4ファイルとともにGit blob SHA/SHA256一致を記録。正規evening-state4JSONをSkillのstate直下へhash一致で復元し、最終rankとrecord_runはSkill直下の`--state-dir state`を使用。元carryと最初のcarry日を保持し、record_run前後を検算した。

費用0円、モデル重み取得0、モデル推論CPU0、候補修復1/1。配布archive約9.7MB、Python image展開119,470,714 bytes、残りはGit・一次資料の取得。wire総量は未計測であり、総取得の保守的予約枠を500MBとして2GB上限内に限定した。candidate peak RSS/推論時間は未測定。Kaggle必要候補を選定せずsubmit0、GPUなし。

指定labチェック151件は既存robots-parser依存欠落で1失敗・15skip（同系統の既知環境失敗）。通常CIの修復は行っていない。remote exact-SHAとCI確認は最終応答に記す。実用機能を追加していないためdraft PRなし。merge/deploy/email/cron変更なし。

10/30夕方UNKNOWNは結果不確定のまま再起動していない。別環境の不明child不存在を証明したとはしない。今回環境では既存候補process/containerを確認できず、10/31専用runへ記録し、正規remoteの競合を確認して保存する。今朝の263d5074報告も読解し、OpenDecider48出力、THX-01、Laya、日本語ModernBERTの既済み比較を繰り返していない。

TinyJev累計3GB追加取得は承認待ち、Moli/Lightpanda追加修復とSqueez修復上限は未解決で再開なし。SKU・朝タスク・400M索引へ変更なし。

## 未検証ダイジェスト

1. [LiquidAI/d1-omni-600M](https://huggingface.co/LiquidAI/d1-omni-600M) — 関心・話題性による全件採点の上位。一次資料取得済み。 保留: 引用の厳密照合に不一致が残り、判定根拠を確定できず保留。

2. [Qwen2.5-Coder-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct) — 関心・話題性による全件採点の上位。一次資料取得済み。 保留: コーディングエージェントという明確な関心に合い、1.5Bの指示モデルとして小規模評価の価値はある。スクレイピング処理とは別の適用先なので、CPUでの実行可否とリポジトリ作業での品質を確認してから優先度を決める。

3. [A Survey on Inference Optimization Techniques for Mixture of Experts Models](https://arxiv.org/abs/2412.14219) — 関心・話題性による全件採点の上位。一次資料取得済み。 保留: MoE推論の圧縮・ルーティング・スケジューリングを広く扱い、処理単価とスループットの関心には合う。ただし調査論文であり、今すぐ試せる単一のCPU手法やスクレイピング向け結果は特定されていない。

4. [AfriNLLB: Efficient Translation Models for African Languages](https://arxiv.org/abs/2602.09373) — 関心・話題性による全件採点の上位。一次資料取得済み。 保留: 軽量翻訳モデルと枝刈り・量子化は小型モデルの関心に沿うが、主題はアフリカ諸語の翻訳で、分類・構造化抽出とは用途が異なる。対象言語に該当するデータがある場合に再評価する。

5. [Deploying a BERT-based Query-Title Relevance Classifier in a Production System: a View from the Trenches](https://arxiv.org/abs/2108.10197v1) — 関心・話題性による全件採点の上位。一次資料取得済み。 保留: BERTを蒸留してCPUで低遅延・高スループットを得た実運用報告は、低コスト分類の参考になる。特定の検索クエリと商品タイトル用なので、スクレイピング文書への転用性と再現可能な実装を確認してから試す。

6. [Gated Relational Alignment via Confidence-based Distillation for Efficient VLMs](https://arxiv.org/abs/2601.22709) — 関心・話題性による全件採点の上位。一次資料取得済み。 保留: 引用の厳密照合に不一致が残り、判定根拠を確定できず保留。

7. [Understanding and Improving Knowledge Distillation for Quantization-Aware Training of Large Transformer Encoders](https://arxiv.org/abs/2211.11014) — 関心・話題性による全件採点の上位。一次資料取得済み。 保留: 引用の厳密照合に不一致が残り、判定根拠を確定できず保留。

8. [I-ViT: Integer-only Quantization for Efficient Vision Transformer Inference](https://arxiv.org/abs/2207.01405) — 関心・話題性による全件採点の上位。一次資料取得済み。 保留: Vision Transformerを整数演算で量子化する方法は、画像認識と効率化の関心に合う。記載された速度向上はGPU上の整数演算ユニットでの評価なので、CPU限定環境で同様の価値が出るかは未確認。

9. [Lightweight Vision Transformer Compression for On-Device Plant Disease Detection in Resource-Constrained Agricultural Field Conditions](https://arxiv.org/abs/2609.05334v1) — 関心・話題性による全件採点の上位。一次資料取得済み。 保留: 制約のある端末での画像分類モデル圧縮は、軽量ビジョンの関心と合致する。対象は唐辛子の病害検出で、統合圧縮手法のCPU処理量や他の画像分類への一般化はまだ確認できない。

10. [AnkitAI/TinyJev-0.6B](https://huggingface.co/AnkitAI/TinyJev-0.6B) — 関心・話題性による全件採点の上位。一次資料取得済み。 保留: 短い型付き判断を一回の推論で返す設計は分類・抽出に合う。ただし追加3 GBの承認待ちなので再試行しない。記載済みのゲーム試験をスクレイピングや戦術判断の能力に外挿しない。

11. [MOCHA: Multi-modal Objects-aware Cross-arcHitecture Alignment](https://arxiv.org/abs/2509.14001v5) — 関心・話題性による全件採点の上位。一次資料取得済み。 保留: 視覚言語教師の領域表現を軽量検出器へ移す研究で、画像認識・モデル軽量化の関心に合う。数例から個別物体を検出する用途は限定的で、学生モデルのCPU実行条件と導入可能な重みを確認する必要がある。

12. [Task-Specific Zero-shot Quantization-Aware Training for Object Detection](https://arxiv.org/abs/2507.16782) — 関心・話題性による全件採点の上位。一次資料取得済み。 保留: 引用の厳密照合に不一致が残り、判定根拠を確定できず保留。

13. [ThinkBit: Search Codewords, Recalibrate Scales, Select by Reasoning](https://arxiv.org/abs/2610.34886) — 関心・話題性による全件採点の上位。一次資料取得済み。 保留: 引用の厳密照合に不一致が残り、判定根拠を確定できず保留。

14. [Broken on Arrival: Non-Functional Quantized LLM Artifacts in Public Model Registries and How to Catch Them](https://arxiv.org/abs/2609.05881) — 関心・話題性による全件採点の上位。一次資料取得済み。 保留: 量子化済みモデルの動作不良を短いスクリーニングで見つける手順は、低コストなローカルモデル選定に実用価値がある。まず利用候補1点を小さな機能テストに通し、合格後にスクレイピング品質とCPU処理速度を別途測る。

15. [Image Recognition with Online Lightweight Vision Transformer: A Survey](https://arxiv.org/abs/2505.03113) — 関心・話題性による全件採点の上位。一次資料取得済み。 保留: 軽量Vision Transformerの構成、動的ネットワーク、蒸留を画像認識の観点から整理しており、ビジョンの関心に合う。一方、調査論文なので、具体的な小型モデルや再現可能なCPU結果を選んでから評価する。

収集1,687件・候補実行成功0件（起動試行1候補、静的配布検査1件）・モデル推論0件・記録後未完了9,549件。
