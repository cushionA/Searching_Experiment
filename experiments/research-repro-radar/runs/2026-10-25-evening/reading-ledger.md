# 一次資料読解台帳

候補実行0。A/testは試す価値であり実行完了ではない。追加判定を初回より優先。

## Qwen2.5-Coder-1.5B-Instruct
https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct

defer / reading
小型CPUモデルとAI駆動開発の双方に関係し、著者資料はCode Agent用途を挙げる。ただし昨日詳細説明済みで今回有意な新証拠がなく、再推薦は抑制する。1.54Bモデルの実行可否は未測定で、外部コードの隔離確認後に限り試行を提案する。

確認・未確認: 公表更新日: 保存資料のAPIではlastModified 2025-01-12。最新状態は未確認。 / starsと活動状況は未確認。 / 商用利用: Apache-2.0と記載。実際の利用条件の確認は未実施。 / 導入: READMEの例はTransformersでのロード。環境での導入・実行は未確認。 / 資源: 1.54B parameters、32,768 contextの記載あり。実測RAM、CPU速度、重み容量は未確認。 / 制約: READMEはTransformers最新版を推奨し、4.37.0未満でqwen2のKeyErrorを記載。実行時の隔離は未確認。 / 後継: READMEにQwen2.5-Coder各サイズの記載はあるが、より新しい実在後継IDは保存資料から確認できない。
次: None

## A Survey on Inference Optimization Techniques for Mixture of Experts
  Models
https://arxiv.org/abs/2412.14219

defer / reading
MoE推論の圧縮・スケジューリングを横断整理する調査論文で、蒸留や量子化は小型CPUモデルの検討に関係する。ただし対象はMoE全スタックで、手元の具体的モデルやCPU・8GB RAM・20分枠に直結する手順や効果は資料から確認できず、今すぐの採用価値は未確定。

確認・未確認: 公表更新日: 保存資料では2024-12-18提出、2025-01-22改訂。最新状態は未確認。 / starsと活動状況は未確認。 / 商用利用ライセンスは未確認。 / 導入: 論文に更新用repositoryへの言及はあるが、実在ID・導入方法は未確認。 / 資源: 保存資料に具体的なCPU/RAM/ダウンロード量はない。 / 制約: MoEのモデル・システム・ハードウェア最適化を扱うが、現行候補への適用可能性は未確認。 / 後継: 最新の実在後継IDは保存資料から確認できない。
次: None

## AfriNLLB: Efficient Translation Models for African Languages
https://arxiv.org/abs/2602.09373

test / reading
アフリカ諸語向けの翻訳という具体的なNLP用途に加え、著者は圧縮モデルの効率的な展開を報告し、Transformers版とCTranslate2版を公開するとしている。翻訳または制約環境向け小型モデルの独立した採用経路として、候補artifactのサイズとCPU導入性を確認する価値がある。性能値は著者報告であり、こちらの実測ではない。実行提案は外部コードの隔離確認後に限る。

確認・未確認: 公表更新日: 保存資料では2026-02-10提出、2026-08-07改訂v2。最新状態は未確認。 / starsと活動状況は未確認。 / 商用利用ライセンスは未確認。 / 導入: Transformers版とCTranslate2版の公開を論文が記載。実在する配布先ID・手順は未確認。 / 資源: NLLB-200 600Mを基にしたモデルとの記載。各配布weightサイズ、CPU実行時RAM、所要時間は未確認。 / 制約: 英語と13言語、仏語とLingala/Wolofの並行データで学習との記載。評価条件・各言語の性能詳細は未確認。 / 後継: 保存資料はv2を示す。より新しい実在後継IDは未確認。
次: 隔離環境を確認した上で、公開モデルの実在ID・量子化weight容量が2GB以内かとCPUロード可否を調べる。

## Deploying a BERT-based Query-Title Relevance Classifier in a Production System: a View from the Trenches
https://arxiv.org/abs/2108.10197v1

defer / reading
著者はQuery-Title Relevance分類器のCPU推論速度と蒸留手法を報告しており、テキスト分類・小型CPUモデルの参考になる。一方で検索用途では関連度判定の改善が主眼で、網羅性補完・前処理・新サイト発見への進展は資料に示されない。また提供資料から再利用可能なモデルartifactを確認できず、今すぐの試行価値は不足。0.2msは著者報告で、こちらの実測ではない。

確認・未確認: 公表更新日: 保存資料では2021-08-23提出のv1のみ。最新状態は未確認。 / starsと活動状況は未確認。 / 商用利用ライセンスは未確認。 / 導入: 再利用可能なモデルやコードの配布先は保存資料から確認できない。 / 資源: CPU推論時間0.2ms以下は著者報告。RAM・モデル容量・独立再測定は未確認。 / 制約: 独自item-title corpusでの事前学習とQTR向けfine-tuning、蒸留を記載。既存モデルのまま利用できるかは不明。 / 後継: 最新の実在後継IDは保存資料から確認できない。
次: None

## Gated Relational Alignment via Confidence-based Distillation for Efficient VLMs
https://arxiv.org/abs/2601.22709

defer / reading
著者論文はINT4 VLMの量子化・蒸留と資源制約環境を扱い、INT4カーネルの比較結果も報告している。しかしGRACEは昨日詳細説明済みで、今回の資料に有意な新証拠がないため再推薦を抑制する。記載の性能・速度・メモリ値は著者報告であり、こちらの比較実測ではない。

確認・未確認: 公表更新日: 保存資料では2026-01-30提出、2026-06-26改訂v5。最新状態は未確認。 / starsと活動状況は未確認。 / 商用利用ライセンスは未確認。 / 導入: 論文はcode and data availableとするが、実在配布先ID・導入手順は保存資料にない。 / 資源: 著者はINT4 kernelで54% memory reductionと報告。絶対RAM、CPU適合性、DL容量は未確認。 / 制約: 評価対象はLLaVA/Qwen VLM。GPUまたは学習の要否と現行予算での導入可否は未確認。 / 後継: 保存資料はv5。最新の実在後継IDは未確認。
次: None

## SmartBench: Is Your LLM Truly a Good Chinese Smartphone Assistant?
https://arxiv.org/abs/2503.06029

test / reading
目的は中国語スマートフォン上の要約・情報抽出など日常タスクを評価すること。英語中心の既存評価の穴を埋め、当方の要約/抽出とオンデバイス関心に複数の具体的な評価用途を与える。20タスクのデータセットと量子化後の実機NPU評価が報告され、まず読む価値がある。ただしコードとデータは論文時点で公開予定とされるだけで、実測や手元実装確認はない。外部コードの隔離確認後に限り次段階へ進む。

確認・未確認: 更新日: 保存資料では2025-08-26改訂(v2)を確認。これ以降の更新は未確認。 / stars数・直近活動: 未確認。 / 商用利用可能なライセンス: 未確認。 / 導入手順とコード/データ公開状況: 論文は公開予定と記載、現時点での取得可否は未確認。 / CPU実行資源、RAM/DL容量、推論時間: 未確認。実機NPU評価は論文記載の著者評価であり当方実測ではない。 / 制約: 中国語モバイル用途向けベンチマークであり、一般用途への適合は未確認。 / 最新後継の実在ID: 保存資料だけでは断定できず未確認。
次: 隔離確認後、公開データの有無と20タスクのうち要約/抽出データをCPU20分内で読める形に取得できるか確認する。

## Understanding and Improving Knowledge Distillation for
  Quantization-Aware Training of Large Transformer Encoders
https://arxiv.org/abs/2211.11014

defer / reading
目的はTransformer encoderの量子化考慮学習で蒸留損失を比較すること。attention-map/output損失とsub-2-bit QATは軽量モデル研究には関係するが、具体的価値は学習を伴う手法の再現に偏る。当方は学習禁止であり、CPU推論だけで得られる利益も資料から確認できないため、現予算での試行は保留する。

確認・未確認: 更新日: 保存資料では2022-11-20投稿を確認。後続更新の有無は未確認。 / stars数・直近活動: 未確認。 / 商用利用可能なライセンス: 未確認。 / 導入手順と公開コード/モデルの有無: 未確認。 / CPU推論資源、RAM/DL容量、推論時間: 未確認。 / 制約: 論文の中心はQAT/蒸留の学習。学習禁止の当方環境で実行可能な代替利用は未確認。 / 最新後継の実在ID: 保存資料だけでは断定できず未確認。
次: 学習を使わず既存の量子化済みencoderへ適用できる公開artifactがあるか、隔離確認後に確認する。

## I-ViT: Integer-only Quantization for Efficient Vision Transformer
  Inference
https://arxiv.org/abs/2207.01405

defer / reading
目的はVision Transformerを浮動小数点演算なしで量子化推論させ、軽量画像認識のエッジ実装を可能にすること。INT8精度とGPU整数演算での速度向上は論文の著者報告だが、CPUでの性能・適合性は示されず、当方のCPU限定予算に直接つながる価値がまだ確認できない。GPU専用とは断定せず、CPU適用可能性が分かるまで保留する。

確認・未確認: 更新日: 保存資料では2023-08-07改訂(v4)を確認。これ以降の更新は未確認。 / stars数・直近活動: 未確認。 / 商用利用可能なライセンス: 未確認。 / 導入手順と公開コードの現状: 論文にPyTorch/TVMコードの記載はあるが、取得・導入可否は未確認。 / CPU推論資源、RAM/DL容量、CPU速度: 未確認。速度向上値はGPU整数演算についての著者報告。 / 制約: 保存資料で示される実機展開先はGPUの整数演算ユニット。CPUでの有効性は未確認。 / 最新後継の実在ID: 保存資料だけでは断定できず未確認。
次: 隔離確認後、公開実装にCPU backendがあり、学習なしでサンプル推論できるかを確認する。

## Lightweight Vision Transformer Compression for On-Device Plant Disease Detection in Resource-Constrained Agricultural Field Conditions
https://arxiv.org/abs/2609.05334v1

test / reading
目的は農業現場の唐辛子病害画像分類を小型オンデバイスViTで行うこと。剪定・量子化・蒸留の統合と村/端末をまたぐ評価は軽量画像認識の具体例として有用で、論文は6.01 MB INT8モデルを報告する。一方、同サイズの直接学習studentも近い精度と報告され、剪定/蒸留の追加価値は限定的という比較もある。数値はすべて著者報告で当方実測ではない。学習は禁止のため、既存artifactの推論評価に限り、外部コード隔離確認後に検討する。

確認・未確認: 更新日: 保存資料では2026-09-04投稿(v1)を確認。以降の更新は未確認。 / stars数・直近活動: 未確認。 / 商用利用可能なライセンス: 未確認。 / 導入手順と学習済み推論artifactの公開状況: 未確認。 / CPU実行資源、RAM/DL容量、推論時間: 未確認。論文のサイズ/精度値は著者報告で当方実測ではない。 / 制約: 唐辛子3クラスの特定データセットと農業条件に限定。比較では同サイズの直接学習studentが94.87%と報告され、圧縮手法の上積みは小さい可能性がある。 / 最新後継の実在ID: 保存資料だけでは断定できず未確認。
次: 隔離確認後、学習せずに取得可能な6.01 MB INT8推論artifactの有無とCPU実行可否を確認する。

## GoiBERT: Compact Vietnamese Language Models via Two-Stage Knowledge Distillation
https://arxiv.org/abs/2610.30067

test / reading
目的はベトナム語のraw textを処理する小型encoderで分類・NER等を行うこと。外部word segmentationなしの26M/49Mモデル、複数タスク評価、compact baseline比のスループット報告が、構造化抽出とCPU小型モデル双方に具体的な候補価値を示す。圧縮の弱点として推論・NERのコストも明記される。値は著者報告で当方実測ではない。まずモデル/コード入手性とCPU負荷を確認する価値があるが、実行は隔離確認後とする。

確認・未確認: 更新日: 保存資料では2026-10-22改訂(v2)を確認。これ以降の更新は未確認。 / stars数・直近活動: 未確認。 / 商用利用可能なライセンス: 未確認。 / 導入手順とコード/モデルの公開状況: 論文は公開と記載するが、取得・導入可否は未確認。 / CPU推論資源、RAM/DL容量、推論時間: 未確認。スループットと性能値は著者報告であり当方実測ではない。 / 制約: ベトナム語に特化。著者分析では推論・NERの圧縮コストが残る。 / 最新後継の実在ID: 保存資料だけでは断定できず未確認。
次: 隔離確認後、学習済みbaseモデルが2GB以内で取得でき、NER推論をCPUで試せるか確認する。

## MOCHA: Multi-modal Objects-aware Cross-arcHitecture Alignment
https://arxiv.org/abs/2509.14001v5

test / reading
少数例からの個別物体検出を対象に、凍結VLMの領域意味情報を軽量な画像専用検出器へ移す手法。推論時にテキスト入力を要さない設計は、端末上の個別物体認識を試す具体的な価値がある。論文は4ベンチマークで平均+10.1改善と報告するが著者報告であり、独立実測ではない。まず論文の評価条件が想定する少数例の個別検出用途に合うか確認する。

確認・未確認: 公表更新日は資料上2026-06-22（v5）。 / stars数とリポジトリ活動は未確認。 / 商用利用可能なlicenseは未確認。 / 導入手順・配布重み・実機推論の導入容易性は未確認。 / 推論RAM・モデル容量・CPU/GPU資源の具体値は未確認。 / 実験のデータ条件や制約の詳細は未確認。 / 保存資料に最新後継の実在IDは記載なし。
次: 隔離確認後、少数例の個別検出で必要な教師・データ条件と推論モデルの実体を20分以内で特定する。

## Task-Specific Zero-shot Quantization-Aware Training for Object Detection
https://arxiv.org/abs/2507.16782

defer / reading
物体検出向けに合成校正データとタスク特化の量子化・蒸留を組み合わせる点は軽量画像認識に関係する。一方、論文は検出器の性能回復にタスク特化学習を使うと明記しており、現行の学習禁止・現金ゼロの枠内で試す具体的な経路を資料から確認できない。適用対象モデルと学習不要の配布物が確認できれば再評価する。

確認・未確認: 公表日は資料上2025-07-22。 / stars数とリポジトリ活動は未確認。 / コード公開との記述はあるが、コードURL・商用licenseは未確認。 / 導入手順、配布済み重み、学習不要での利用可否は未確認。 / RAM・モデル容量・CPU/GPU資源と計算時間は未確認。 / 対象検出器ごとの制約と現行予算での実行可能性は未確認。 / 保存資料に最新後継の実在IDは記載なし。
次: None

## Image Recognition with Online Lightweight Vision Transformer: A Survey
https://arxiv.org/abs/2505.03113

test / reading
軽量Vision Transformerの設計・動的ネットワーク・蒸留をImageNet-1Kで比較整理するサーベイで、画像認識向けの候補方式と精度・パラメータ・throughputのトレードオフを短時間で把握できる。実装や実測の導入候補が定まっていない段階で、次に読む対象を絞る具体的価値がある。

確認・未確認: 公表更新日は資料上2025-09-26（v3）。 / stars数とリポジトリ活動は未確認。 / 商用利用可能なlicenseは未確認。 / プロジェクトページURL、導入手順、実装・重みの配布状況は未確認。 / 個別方式のRAM・モデル容量・CPU資源は未確認。 / 調査対象の範囲と各比較条件の詳細は未確認。 / 保存資料に最新後継の実在IDは記載なし。
次: サーベイの比較表からCPU推論を想定できる方式を1つ選び、掲載条件と必要資源を確認する。

## PTQ4SAM: Post-Training Quantization for Segment Anything
https://arxiv.org/abs/2405.03144

test / reading
SAM向けのpost-training quantizationで、大規模な汎用セグメンテーションモデルの実行負荷を下げる画像認識上の具体的な手法。著者はSAM-Lの6-bit化について、instance segmentationでlossless accuracyと理論3.9倍加速を報告しており、実測とは区別して扱う。メモリ・計算負荷が導入障壁となるセグメンテーション用途を読む価値がある。

確認・未確認: 公表日は資料上2024-05-06。 / stars数とリポジトリ活動は未確認。 / コードURLと商用licenseは未確認。 / 量子化済み重みの有無・導入手順は未確認。 / 実機RAM・容量・CPU/GPU資源および理論値を再現する条件は未確認。 / 対応するSAM variantと量子化精度の制約は未確認。 / 保存資料に最新後継の実在IDは記載なし。
次: SAM-Lの量子化済み重みと対象ハードウェアの有無を論文のコード配布先で確認する。

## Bekko Embedding: Parameter-Efficient Multilingual Retrieval with Ultra-Compact Encoders
https://arxiv.org/abs/2607.25180

reject / reading
本資料の用途は多言語dense retrievalで、報告する比較もnDCG@10など検索順位の品質・速度である。今回の検索目的で求める網羅性補完、前処理、新サイト発見への具体的進展は資料から確認できず、CPU小型モデルとしても検索埋め込み以外の独立用途が示されていないため、現行の目的には採用理由がない。

確認・未確認: 公表日は資料上2026-07-28。 / stars数とリポジトリ活動は未確認。 / 商用利用可能なlicenseは未確認。 / ONNX/OpenVINO buildを公開するとの記述はあるが、URL・導入手順は未確認。 / 資料はa8m ONNX/OpenVINO buildの容量を124 MiBと報告。実環境のRAM・CPU資源は未確認。 / 検索ランキング以外の網羅性補完・前処理・新サイト発見への有効性は資料で確認できない。 / 保存資料に最新後継の実在IDは記載なし。
次: None

## Compress What You See, Not What You Say: Anchored Context Distillation for Latent-Observation Software Engineering Agents
https://arxiv.org/abs/2609.31430v1

test / reading
AI駆動開発向け。LOHAで古いツール観測を圧縮し最近の記録を正確なテキストで保つ設計と、ACDで行動のずれを抑える学習法は長いエージェント履歴の文脈コストに直接効く。論文内のSWE-bench結果は著者報告で、こちらでの実測ではない。

確認・未確認: 資料上の公表日2026-09-25。後日の更新日は保存資料だけでは断定不可。 / stars・リポジトリ活動、商用利用可能なlicense、導入手順はいずれも資料に記載なし。 / ACDの学習に必要な計算資源、モデル重みの配布形式・サイズは未確認。学習禁止と現金0の条件に適合する実行経路は未確認。 / 最新後継の実在IDは未確認。
次: 外部コードを隔離できることを確認後、学習なしで再現可能な推論・文脈管理経路があるかを確認する。

## Squeez: Task-Conditioned Tool-Output Pruning for Coding Agents
https://arxiv.org/abs/2604.04979

defer / reading
AI駆動開発でツール出力から必要な証拠箇所を抜く用途は具体的だが、資料は既報・停止扱いのSqueezであり、新しい独立根拠はない。指示に従い再推薦・再開を保留する。論文の数値は著者報告。

確認・未確認: 資料上の公表日は2026-04-04。後日の更新日は保存資料だけでは断定不可。 / stars・リポジトリ活動、商用利用可能なlicense、導入手順は資料に記載なし。 / Qwen 3.5 2B LoRAの配布重み、実際のRAM・ダウンロード量・CPU速度、学習済み成果物の実行要件は未確認。 / 最新後継の実在IDは未確認。既報停止案件のため、今回の資料だけで再開根拠は確認できない。
次: None

## SWE-Pruner: Self-Adaptive Context Pruning for Coding Agents
https://arxiv.org/abs/2601.16746

test / reading
AI駆動開発とCPU小型モデルの経路に合う。タスク目標を手掛かりにコード文脈を選ぶ0.6Bのskimmerは、長い開発履歴のコストを抑えつつ実装上の要点を残す具体策で、論文はベンチマークでのトークン削減と成功率改善を報告している。結果は著者報告で未再現。

確認・未確認: 保存資料はv4、最終改訂日2026-05-07。これ以降の更新日は資料だけでは断定不可。 / stars・リポジトリ活動、商用利用可能なlicense、導入手順は資料に記載なし（コードのリンク先内容も未確認）。 / 重みの入手形式・DL量、8GB RAM/20分CPU枠での推論可否、学習を伴わない利用条件は未確認。 / 最新後継の実在IDは未確認。
次: 外部コードを隔離できることを確認後、学習済み0.6B重みの配布サイズとCPU推論の実行手順を調べる。

## Trae Agent: An LLM-based Agent for Software Engineering with Test-time
  Scaling
https://arxiv.org/abs/2507.23370

defer / reading
AI駆動開発向けに、生成・枝刈り・選択を分けたrepository-level issue解決の設計は具体的。ただし資料は2025年の報告で、Pass@1や首位の記述は当時の著者報告に限られ、現行の有用性や低コストで試せる構成を示す保存根拠がないため保留する。公開年だけを理由に却下はしない。

確認・未確認: 資料上の公表日は2025-07-31。後日の更新日は保存資料だけでは断定不可。 / stars・リポジトリ活動、商用利用可能なlicense、導入手順は資料に記載なし。 / 使用モデル、推論時の計算量・費用、CPU/RAM要件、無学習で試せる構成は未確認。 / 最新後継の実在IDは未確認。
次: None

## What Survives When You Compress a Recursive Reasoner for the Edge?
https://arxiv.org/abs/2606.26488

test / reading
CPU小型モデル・エッジ推論に直接関係する。再帰推論で圧縮により局所予測と大域推論が異なる影響を受ける点を示し、較正INT4の3.3MBバックボーンなど具体的な配備案を報告するため、低資源向け設計知見を読む価値がある。数値は論文著者の実験報告であり当方の測定ではない。

確認・未確認: 保存資料はv2、最終改訂日2026-09-30。これ以降の更新日は資料だけでは断定不可。 / stars・リポジトリ活動、商用利用可能なlicense、導入手順は資料に記載なし。 / 3.3MBは著者記載のbackboneサイズであり、実際に取得可能な重み形式・DL量、実行時RAM/CPU要件、対象ハードウェアは未確認。 / 最新後継の実在IDは未確認。
次: 外部成果物を隔離して扱えることを確認後、3.3MBバックボーンの重み形式とCPUでの再帰タスク実行可否を確認する。

## F2G-Pose: Geometry-Aware Foundation Feature Lifting for Direct RGB-D Category-Level Object Pose Estimation
https://arxiv.org/abs/2610.31623

defer / reading
F2G-Poseは、分割済みRGB-D画像から物体の6D姿勢・寸法・形状をまとめて推定する専用機能で、ロボット操作などRGB-Dの物体姿勢推定には具体的価値がある。ただし今回の関心は軽量画像認識であり、この方式はLinux/NVIDIA GPU/CUDA拡張を必須とするため、現在の計算環境では試せない。画像認識全般に価値がないとは判断せず、適合するRGB-D用途とGPU環境が確認できるまで保留する。論文・公式repoを読解済みだが実測はない。

確認・未確認: 公表資料は2026-10-22投稿。GitHubは10-23更新・58 stars・2 forks、HF重みは09-26更新・15 downloads・0 likesを資料上で確認。現在の最新更新かは保存資料だけでは断定不可。 / F2G-PoseのApache-2.0コードライセンスを確認。必須依存RADIO v2.5-LのNVIDIA Source Code Licenseとカード上のresearch and development only表示を確認したが、商用利用条件の適合判断は未確認。 / F2G重みは約366 MB。RADIO重みは別途必要で、導入にはCUDA拡張ビルドも必要。RADIO重みを含めた総DL量、GPU VRAM/RAM、CPU実行可否は未確認。 / 保存済みHF著者検索にF2G-Pose以外の候補IDはなく、後継モデルIDは確認できない。実行・性能比較は未実施。
次: 次回、対象のRGB-D物体姿勢推定用途とNVIDIA GPUの利用可否を確認し、両方合う場合に公式サンプルを隔離環境で試す。

## IQuest-Q1: Advancing Foundation Capabilities for Agentic CLI Systems
https://arxiv.org/abs/2610.30949

defer / reading
IQuest-Q1はagentic codingとCLIでの複数段階ツール利用を狙うため、AI駆動開発という独立した関心に明確な用途がある。一次資料にはCLIベンチマークと実行手順がある一方、配布BF16モデルは320B超のパラメータで、確認済み混合量子化GGUFも約88 GB規模。現金0・DL 2 GB・RAM 8 GBの条件内で利用できる実行経路/APIは資料から確認できず、今すぐ試す候補にはできない。用途自体を否定せず、利用可能な小型モデルまたは予算不要の提供経路が確認できるまで保留する。実測・実行はない。

確認・未確認: 論文は2026-10-22投稿。GitHubは10-19更新・181 stars・8 forks、HFモデルは09-29更新・149 likes・4,777 downloadsを資料上で確認。現在の最新更新かは保存資料だけでは断定不可。 / ソフトウェアLICENSEは商用製品UIにIQuest-Q1の表示を求める変更付きMIT。重みのHF表示はlicense=other / iquest-q1で、利用条件の詳細な適用範囲は未確認。 / BF16パラメータ数は320,318,615,552。別作者のGGUFは6分割合計で約88 GB。CPU速度、必要RAM、利用可能な無料API/提供枠は未確認。 / 著者HF一覧でIQuest-Q1の後継IDは確認できない。FlemingやIQuest-Coderは別系統として記録されており、後継とは扱わない。論文・公式モデルカードの性能値は著者報告で、今回の実測ではない。
次: 公式資料に予算不要のAPI/ホスト推論があるか、または2 GB以下で動く公式互換量子化版が公開されたかを確認する。

## Cola2: Co-Adaptive Hierarchical Language Modeling with Continuous Semantic Compression and Balanced Discrete Generation
https://arxiv.org/abs/2610.31305

defer / reading
Cola2は意味単位の連続圧縮と離散生成を協調させる言語モデル研究で、長文テキストの圧縮表現や生成設計を調べる材料にはなる。ただし一次資料で確認できる評価は一般的な下流タスク・言語モデル尺度で、今回のNLP関心である分類または要約への具体的な適合性は示されていない。公式ページはコードとモデルを未公開予定としており、公開チェックポイントも確認できない。用途評価に必要な成果物がそろうまで保留する。著者報告の実験値を独立実測とは扱わない。

確認・未確認: 論文は2026-10-22投稿。保存資料にGitHub活動やstarsはなく、公式ページのコード・モデル表示はSoon。更新日と現在の公開状況は保存資料だけでは断定不可。 / モデル検索の一致結果は別目的の無関係なcola2名義モデルのみで、Cola2公式IDは確認できない。利用ライセンス、導入手順、推論資源、チェックポイントは未確認。 / 論文は約210B tokenの実験と346.03B source tokenのチェックポイントを報告。これらは著者報告で、今回の独立実測ではない。学習は予算・制約外。 / 公式資料で後継モデルIDは確認できない。論文と同じ研究チームのプロジェクトページは独立した話題信号として二重計上していない。
次: 公式コードまたは重みの公開後、分類・長文要約の具体的評価データと2 GB以下の推論可能性を資料で確認する。

## Deep Search in Real-World Web Environments
https://arxiv.org/abs/2610.30181

test / reading
検索エージェントのlive web調査を200問で評価する公開ベンチマークで、複数ソース発見・証拠統合を扱う。archive-freeの新サイト発見や欠落ページ回復を測る評価設計の具体的な土台として、候補タスクの網羅性を点検する価値がある。論文の39.0%等は著者報告であり、今回の実測ではない。コード実行は未提案で、次回の隔離確認を前提とする。

確認・未確認: 公開repo更新: 2026-10-23; GitHub snapshotは24 stars、0 forks。活動量の長期推移は未確認。 / repo licenseはApache-2.0と表示。商用利用条件の個別レビューは未確認。 / 導入はPython 3.10–3.13、pip install -e .と記載。datasetは200問CSVと対応動画。実際の取得サイズ・依存資源・ローカル実行資源は未確認。 / 実行コスト/必要API・モデル構成は未確認。READMEの評価条件は1問あたり最大1時間・100 tool callsで今回の枠を超える。 / 保存資料内に最新後継の実在IDは確認できず、latestは断定不可.
次: 隔離を確認後、タスク記述を読み、新サイト発見・欠落ページ回復を評価できる課題の有無を確認する。

## Is Complex Agentic Memory Always Needed? Naive RAG Is Stronger Than We Thought
https://arxiv.org/abs/2610.30755

defer / reading
長期会話メモリ向けのraw-history検索が、比較対象の会話ベンチマークで強い基準線になり得るという著者報告はNLP読解用途に関係する。ただし提供資料では外部LLM・embedding・rerankerのAPI設定を要し、予算0で即時に検証できる運用経路が確認できない。順位や精度は著者報告で今回の実測ではなく、明確な独立した話題信号も追加確認できないため保留する。

確認・未確認: arXiv page says submitted 2026-10-22; repo snapshot pushed 2026-10-25, 21 stars, 1 fork. Activity trajectory beyond these timestamps is unconfirmed. / repo license reports MIT; commercial terms not separately reviewed. / README gives setup instructions requiring Python 3.12 and PowerShell 7 and lists external LLM, vectorization, reranking, and DeepSeek service endpoints; exact minimum cost and credentials availability are unconfirmed. / Local CPU/RAM/disk needs and whether any useful offline path fits the 20-minute/8-GB/2-GB constraints are unconfirmed. No GPU requirement is established by these snapshots. / Saved materials do not establish a latest successor; successor model/repo ID unconfirmed and latest cannot be asserted.
次: None

## Policy-Mask: Budget-Aware Adaptation of Diffusion Features to Discriminative Tasks
https://arxiv.org/abs/2610.28889

defer / reading
画像認識の関心には、拡散モデル特徴を予算に合わせて選ぶという具体的な接点があり、論文は分類を含む著者報告の改善を述べる。ただしPolicy-Mask自体は下流目的で学習する手法で、学習禁止の条件では直ちに試す経路が確認できない。凍結拡散モデルの要件や実資源も未確認なので、現時点では保留する。

確認・未確認: 公開日は資料で2026-10-20と確認。arXiv履歴はv1のみで、以後の更新日は未確認。 / コードとモデルは候補情報上なし。stars・リポジトリ活動は未確認。 / 商用利用ライセンス、導入手順、配布重みの有無は未確認。 / 必要な拡散バックボーン、RAM、ダウンロード量、CPU推論時間は未確認。20分は割当上限で実測値ではない。GPU不要との候補情報は実行資源を裏付けない。 / 学習禁止条件との両立、具体的制約、最新後継の実在IDは未確認。
次: 論文本体で、既学習ポリシーによる推論だけで使えるか、学習と必要モデルを確認する。

## Meta Expert: Cross-Layer Expert Sharing for Storage- and Retrieval-Efficient MoE
https://arxiv.org/abs/2610.27598

defer / reading
MoEの層をまたいだ専門家共有で保存効率を上げる主張は、モデル容量を気にするCPU小型モデルの関心に接点がある。しかし概要の提案は既存モデルへの即時適用や訓練不要の推論改善を示しておらず、学習禁止の条件で今すぐ試す具体的経路は確認できない。容量・品質の記述は著者の論文報告であり、独立した実測確認ではないため保留する。

確認・未確認: 公開日は資料で2026-10-20と確認。arXiv履歴はv1のみで、以後の更新日は未確認。 / コードとモデルは候補情報上なし。stars・リポジトリ活動は未確認。 / 商用利用ライセンス、導入手順、配布重みの有無は未確認。 / 実モデルの保存量、RAM、ダウンロード量、CPU推論時間は未確認。20分は割当上限で実測値ではない。GPU不要との候補情報は実行資源を裏付けない。 / 学習禁止条件との両立、具体的制約、最新後継の実在IDは未確認。
次: 論文本体で、既存MoEに再学習なしで適用できるか、保存量と推論資源の条件を確認する。
