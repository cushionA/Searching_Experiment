# 夕方の研究レーダー — 2026-10-13 JST

## 結論

**実測で大きな効果を確認した候補0、確定した深掘り推薦0。** 指定の `scripts/llm.py judge()` / `RADAR_LLM=codex` が今回も有効なJSONを返さず、A/B/Cの最終判断は未完了。サブエージェントによる一次資料照合はjudgeの代用にしていない。候補モデル推論は0件で、読解を「簡易確認」「再現成功」と呼ばない。

公開資料の収集・全件採点・最終review_pool 20件の一次照合・夕方stateの更新は完了。以前の未完了444件を全件保持し、今回の記録後未完了は**1,057件**。詳細推薦がないのでseenを増やさず、digest掲載も完了扱いにしていない。

## 概要

- 今回の一意IDは**1,679件**。内訳はHF論文1,216、MDPI論文200、追加モデル・checkpoint・repo ID263。これは独立した研究論文1,679本ではなく、同系列の形式違い・checkpointを含むartifact数。重複前1,684件から同じID5件を統合した。
- 正規stateは `research/2026-10-12-evening-radar` の `experiments/research-repro-radar/evening-state/`。開始時remote headは `f1a9a681a4f06d33a004c3ca77d7fab5c03e58c5`。
- Skillの最新mainは `cushionA/zabuton_skills@7abebd12df90dbce8eabf741384a52a171dd1799`、profile v7。指定7ファイルを再読した。保存済みSkillと同じ版。配信先だけChatGPT、条件付き無料Kaggle推論許可はユーザー指示を優先する。
- 朝のXberg/Docling調査、GLiNER SKU別タスク、停止済みPP-LCNetには介入・再実行しなかった。

## 方法

HF trending全50、新着全50に加え、分類・抽出・小型CPU・軽量画像・coding agent・要約・雑な構造化抽出・INT4推論・encoder蒸留・CPU throughputの10検索を取得した。40件に達しても打ち切っていない。HF論文検索は各応答98または120件でnext Linkなし。検索backendの上限より先まで網羅した主張はしない。

MDPI AI/InformationのRSSは各100件。OpenReviewのICLR 2026公開APIはHTTP403で未取得。6系列のHF公式publisher検索を追加し、返却249 model IDを全て正規化した（曖昧検索による旧系列混入も原本に残す）。19モデル/repoの詳細補足を統合。論文とモデルは異なるartifactとしてIDを保持し、同じ手法の独立した話題シグナルとして二重計上しない。

`rank_candidates.py --state-dir state` を最新Skill直下で使用。最終採点は当日取得の一意ID1,679＋当日未再発見carry7＝1,686入力、ranked1,056 / 自動採点対象外630 / review20 / scheduled1。**scheduledは検証実施数ではない。** 自動採点対象外はC判定や完了ではなく、全原本と正規化レコードを保存した。

最終review20は19論文＋Qwen2.5-Coderモデルカード。途中の順位も含め一次arXivページ33件、補足モデル/repo19件を読んだ。各readで目的・著者主張・公開日・artifact・license・実行資源を照合し、確認できない項目は未確認。残る候補はメタデータ/abstractレベルであり、全1,679件の本文・商用条件・実機資源を検証したわけではない。

## 結果・既存比較

### 原文照合で確認できた差分（推論結果ではない）

- [Bekko Embedding](https://arxiv.org/abs/2607.25180): 多言語retrieval用8M/25M encoder。公式 `hotchpotch/bekko-embedding-v1-a8m` / `a25m` カードはMIT。CPU等の著者評価があるが、本環境の日本語分類・構造抽出性能を示さない。後継調査でGGUF版と別用途の `bekko-system-one-v0-{17m,68m,400m}` も実在確認。embeddingと別用途モデルを同一の能力として扱わない。
- [MobileCLIP2](https://huggingface.co/apple/MobileCLIP2-S0): 旧MobileCLIPだけで判断しない。repo codeはMIT、モデルカードは `apple-amlr`。code licenseを重みの商用条件へ流用できない。[FastViT](https://github.com/apple-aiml-research/ml-fastvit) もApple独自研究licenseで、MITと誤記しない。
- [Qwen3-Coder-Next](https://huggingface.co/Qwen/Qwen3-Coder-Next): 80B総重み/3B activeであり、1.5Bの小型Coder置換として同じメモリ条件に置けない。Qwen3.5-0.8Bに加え公式Qwen3.8系列6 IDも確認。新しい大モデルの存在と、小型CPU条件に合う後継の存在は別。
- [Granite 4.2-3B](https://huggingface.co/ibm-granite/granite-4.2-3b) / 公式GGUF、[SmolLM3-3B](https://huggingface.co/HuggingFaceTB/SmolLM3-3B)、[Gemma 4 E2B QAT mobile](https://huggingface.co/google/gemma-4-E2B-it-qat-mobile-ct) の実在を確認。旧1.7B/2B/270Mと同じfootprintではない。Gemma 3のcustom termsをGemma 4のApache-2.0へ、または逆方向へ流用しない。
- I-ViTの速度評価はRTX 2080 Ti、ABQ-LLMのkernelはCUDA/Turing以降、fouroversixの高速backendはBlackwell、BEAVERの著者評価はA100 80GB。論文の量子化・edgeという語だけでCPU加速を保証しない。

上記は著者資料・配布元metadata。こちらの品質改善、CPU時間、peak RAM、throughput、1万件換算は未測定。基準の相対品質5%または資源30%改善/品質低下2%以内を満たしたと判断する材料はない。独立話題シグナル2件も詳細推薦用に確定していない。古い年代だけで候補を除外しなかった。

## 問題と未完了

1. `RADAR_LLM=codex python scripts/llm.py --model gpt-6-luna` はexit0でも `verdict:null`。有効なjudge出力0。今回のwrapperはstderrを内部で捕捉して表示しないため、同じread-only appserverエラーかは断定していない。既往の同エラーと、今回 `codex --version` のread-only PATH alias警告は別の証拠として保存。新規API、別backend、認証移設、権限制限を変える修復なし。接続確認1回、修復0。
2. 初回の作業用stateコピーが既存stateディレクトリの内側に入ったため、最初の採点はcarry欠落。正規stateに変更を加える前に検出し、4JSONを直接復元して全採点をやり直した。`ranking-initial.json` は無効な診断記録であり最終数値に使っていない。
3. 最新metadataで関心判定を失った旧carry `2610.08699` がranker出力から落ちた。未実行を完了扱いしない指示に従い、既往carry日を維持してqueueへ戻した。結果は1,056＋1＝1,057。Skillの採点式を変更していない。
4. 資源未知に `requires_gpu=false` の既定CPU加点が付くschema制約がある。20分は調査枠の上限で推論時間の推定ではない。別フィールドと実行ゲートで未確認を保持し、順位を実行許可やA判定にしていない。HF由来の `has_code` も関連repoの存在フラグであり、著者公式実装であるかは一次照合済みの候補以外未確認。
5. 初回review内の `2409.01990` と `2605.07505` はHF側題名とarXiv正式題名が相違。最終20の題名は一次資料と一致。元データを上書きせず差分を記録した。
6. Docker daemonのread-only照会はversion28.4.0を返すが、停止済みPP-LCNetのbuild/repairを再開していない。計算能力不足を観測していないためKaggle移行条件は未成立。quota/accelerator/private設定は今回未確認、ジョブ・upload・無料GPU使用時間は0。

## 導入判断・次

A/B/Cは必須judge経路が利用できるまで未確定。推論未完了を不採用Cに変えず、全ての未完了carryを保つ。全候補のmetadataと読解根拠は保存したので、判定器が利用可能になれば同じ資料から続けられる。

大量・不揃い入力では、圧縮率だけでなく必要証拠の保持とタスク品質、追加推論時間が必要。Bekko等のembeddingもそのままSKUの支持/矛盾判定器にはならない。コード観測圧縮なら固定公開ログで単純切詰め/現行要約との比較が必要だが、今回は実施していない。検索サービスの並べ替えPR、PR19再開はない。

API/GKE/AWSとの価格・break-even比較は今回未実施。profileの古い `$6.25/1万件` や時給単価を現在価格として使わない。採用・コスト優位を主張しない。実用機能は完成していないためdraft PRなし、merge/deployなし。

## 未検証ダイジェスト（最終採点上位15、推薦ではない）

1. [Qwen2.5-Coder-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct) — 1.54Bのコード生成モデル。後発Coder-Nextは80B総重みで同サイズ代替ではない。 保留: CPU品質・速度の比較未実施。指定judgeも未完了。
2. [A Survey on Inference Optimization Techniques for Mixture of Experts   Models](https://arxiv.org/abs/2412.14219) — MoE推論最適化の整理。小型化・オフロードの関心に一致。 保留: surveyであり単一の実行対象なし。指定judgeも未完了。
3. [Understanding and Improving Knowledge Distillation for   Quantization-Aware Training of Large Transformer Encoders](https://arxiv.org/abs/2211.11014) — encoderの蒸留と量子化を同時に扱う公式コード。 保留: コードのライセンス未確認、著者評価はA6000。指定judgeも未完了。
4. [I-ViT: Integer-only Quantization for Efficient Vision Transformer   Inference](https://arxiv.org/abs/2207.01405) — ViTの整数演算化、Apache-2.0の公式実装。 保留: 著者速度評価はRTX 2080 Ti、CPU効果未測定。指定judgeも未完了。
5. [Image Recognition with Online Lightweight Vision Transformer: A Survey](https://arxiv.org/abs/2505.03113) — 軽量ViTの設計・動的処理・蒸留を比較。 保留: survey、モデル検証への対象化未完了。指定judgeも未完了。
6. [VisionWeave: Weaving Elastic Visual Representations as a Native Capability of MLLMs](https://arxiv.org/abs/2610.07987) — 視覚tokenを内容に応じて割り当てる2026年10月の研究。 保留: 大規模MLLMで、重み・実行資源の上限適合未確認。指定judgeも未完了。
7. [Bekko Embedding: Parameter-Efficient Multilingual Retrieval with Ultra-Compact Encoders](https://arxiv.org/abs/2607.25180) — 多言語embeddingの8M/25M encoder、MITモデルあり。 保留: 日本語の用途別品質とCPU資源は未測定。指定judgeも未完了。
8. [Squeez: Task-Conditioned Tool-Output Pruning for Coding Agents](https://arxiv.org/abs/2604.04979) — coding agentのツール出力から必要な原文を抽出。 保留: 証拠欠落と追加CPUコストの比較待ち。指定judgeも未完了。
9. [SWE-Pruner: Self-Adaptive Context Pruning for Coding Agents](https://arxiv.org/abs/2601.16746) — 0.6B skimmerによるタスク依存の重要行抽出。 保留: CPUでの品質・速度を未検証。指定judgeも未完了。
10. [What Survives When You Compress a Recursive Reasoner for the Edge?](https://arxiv.org/abs/2606.26488) — recursive reasonerのedge圧縮時に残る能力を調査。 保留: 著者実験はHexagon HTP、CPU fallbackなし。指定judgeも未完了。
11. [ZeroQuant: Efficient and Affordable Post-Training Quantization for Large-Scale Transformers](https://arxiv.org/abs/2206.01861) — Transformerの後学習量子化、DeepSpeed系の一次論文。 保留: 現在の実装・後継・CPU性能の照合未完了。指定judgeも未完了。
12. [ABQ-LLM: Arbitrary-Bit Quantized Inference Acceleration for Large   Language Models](https://arxiv.org/abs/2408.08554) — 任意bit量子化のGPU kernel、Apache-2.0実装。 保留: CUDA/Turing以降の条件、対象環境で未測定。指定judgeも未完了。
13. [Adaptive Block-Scaled Data Types](https://arxiv.org/abs/2603.28765) — block単位の低精度表現、fouroversix公式実装。 保留: 高速backendはBlackwell依存、無料GPU互換未確認。指定judgeも未完了。
14. [BEAVER: A Training-Free Hierarchical Prompt Compression Method via Structure-Aware Page Selection](https://arxiv.org/abs/2603.19635) — 学習不要の階層的prompt圧縮。 保留: 公式ページからコード・licenseを確認できず。指定judgeも未完了。
15. [DeepCode: Open Agentic Coding](https://arxiv.org/abs/2512.07921) — 論文からコードへの情報圧縮とagent構成。 保留: 既存認可内のLLM利用・費用条件を未検証。指定judgeも未完了。

## 再現・checks・保存

- コード: `experiments/research-repro-radar/collect.py`, `collect_expanded.py`, `normalize_evening.py`, `verify_evening.py`。取得URL・時刻・原本・SHAは当日runのledgersとsources。model/repo調査は `primary-sources/manifest.json`、最終20は `review20-final.json`。
- 採点はupstream Skill直下 `scripts/rank_candidates.py <all-candidates.json> --state-dir state --output <ranking-final.json>`。記録は `scripts/record_run.py <report.json> --state-dir state`、4JSONを正規保存先へ同期。seen/mail不変、freshnessはrun_id重複なしで2件。
- Skill既存テスト **24/24成功**。state検算 **6/6成功**（旧444全保持、重複なし、seen/mail不変、freshness2件、旧carry日維持）。
- repo指定 `test_lab*.py`: **151件、1 failure、15 skip**。既存bot-diagnosticsの `robots-parser` 不足。通常CI修復を行っていない。今回の候補推論成功を示すチェックではない。
- SHA検算: `python experiments/research-repro-radar/verify_evening.py --run 2026-10-13-evening --previous-ref f1a9a681a4f06d33a004c3ca77d7fab5c03e58c5`。最終結果は当日benchmarksに保存。
- 現金0円、候補重みdownload0、GPU0秒、外部候補コード実行0。取得資料は数十MBで2GB未満（保存原本量、通信overhead未計測）。メール/SMTP/cron変更0。

収集1,679 artifact ID／最終一次読解20／候補モデル実測0／記録後未完了1,057。推奨判断は未完了だが夕方stateの継続性は検算して保存した。
