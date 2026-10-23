# 一次資料読解台帳（30件、推論未実施）

初回native草案には引用不一致があり、その引用は不採用。最新訂正はreview-validation.jsonと本日の報告を優先。

## Qwen2.5-Coder-1.5B-Instruct
https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct

判定草案: reject / reading
AI駆動開発向けの小型コードモデルとしては具体的価値がある（著者はCode Agentsを用途に挙げる）が、この候補は昨日すでに説明済みで、保存資料の更新日は2025-01-12、今回その後の有意な新規証拠を確認できない。重複報告を抑制する。

確認状況: HF資料の公表更新日: 2025-01-12。 / stars・活動状況: モデルAPI資料にstarsや最近の活動指標なし。 / 商用利用ライセンス: API/カードにApache-2.0とあるが、利用条件の詳細は未確認。 / 導入: Transformers利用例あり。 / 資源: 1.54B parameters、最大32,768 contextとの記載。実測RAM/CPU速度は未確認。 / 制約: READMEはtransformers 4.37.0未満でKeyErrorを記載。 / 最新後継の実在ID: 保存資料内で未確認。

## A Survey on Inference Optimization Techniques for Mixture of Experts
  Models
https://arxiv.org/abs/2412.14219

判定草案: test / reading
MoE推論の最適化をモデル・システム・ハードウェア層に整理し、pruning、quantization、distillation等を横断するサーベイと更新中の文献リストがある。小型CPUモデルへの転用可能性は限定的で未確認だが、関心のある圧縮手法を20分の読解で発見できる価値がある。

確認状況: 論文: 初版2024-12-18、最終改訂2025-01-22。arXiv記録にはACM CSUR 2026 journal referenceあり。 / リストrepo: 2026-10-01更新、428 stars、最終push 2026-03-12。 / 商用利用ライセンス: repo APIでlicense=null。 / 導入: 論文・キュレーションリストであり、導入可能なソフトの導入手順は該当資料にない。 / 資源: 読解資源以外の実行RAM/CPU/GPU要件は未確認。 / 制約: MoE推論を対象としており、CPU小型モデルに各手法が適用可能か未確認。 / 最新後継の実在ID: 保存資料内で未確認。

## AfriNLLB: Efficient Translation Models for African Languages
https://arxiv.org/abs/2602.09373

判定草案: defer / reading
NLLB-200 600Mを剪定・量子化し、蒸留してアフリカ諸言語向け翻訳モデルと学習データを提供する点は小型モデルとして具体的。ただし提示された15言語ペアに日本語はなく、現時点で使う言語・翻訳案件との適合が分からないため保留。

確認状況: 論文: 初版2026-02-10、最終改訂2026-08-07、AfricaNLP 2026掲載との記載。 / stars・repo活動: arXiv資料に対応repoのID、stars、活動日時なし。 / 商用利用ライセンス: 未確認。 / 導入: 論文はTransformers版とCTranslate2版の公開を記すが、資料中に重みIDや導入手順なし。 / 資源: ベースがNLLB-200 600M。CPU/RAM/実測速度・サイズは未確認。 / 制約: 対応15言語ペアの列挙に日本語はない。 / 最新後継の実在ID: 保存資料内で未確認。

## Gated Relational Alignment via Confidence-based Distillation for Efficient VLMs
https://arxiv.org/abs/2601.22709

判定草案: defer / reading
画像認識関心に直接合うVLMで、論文はQwen3-VL-2B等のINT4化による性能を著者値で報告する。ただし昨日詳細済みで、今回の資料から昨日以降の有意な新規証拠は確認できない。さらに配布コンテナはCUDA/NVIDIA向けで、利用可能GPUと隔離実行の確認が必要なため保留。

確認状況: 論文: 初版2026-01-30、最終改訂2026-06-26、ICML 2026採択との記載。上記性能数値は著者報告で独立実測ではない。 / GitHub repo: 2026-10-03更新、211 stars、最終push 2026-10-01。 / 商用利用ライセンス: repo APIにApache-2.0。モデル重み個別の利用条件は未確認。 / 導入: v0.1.0コンテナ、HFモデル、ZeroGPU demoを案内。 / 資源: リリースはLinux/amd64 CUDA NVIDIA hostを対象と記す。RAM/VRAM要求は未確認。 / 制約: ローカル実行にNVIDIA CUDA環境を想定。今回実行は外部コードの隔離未確認のため提案に留める。 / 最新後継の実在ID: 保存資料内で確認できる配布IDはForeverBlue/Qwen3-VL-2B-GRACE-W4G128-AWQ。これより新しい後継は未確認。

## SLMRec: Distilling Large Language Models into Small for Sequential
  Recommendation
https://arxiv.org/abs/2405.17890

判定草案: reject / reading
論文が扱うモデルの用途はユーザー行動履歴から次の商品等を予測する逐次推薦に特化している。現在示された関心（NLP分類/要約、構造化抽出、画像認識、小型CPUモデル、AI駆動開発）に対する具体的な導入価値は資料から確認できない。

確認状況: 論文: 初版2024-05-28、最終改訂2025-04-20、ICLR 2025との記載。 / stars・活動: arXiv資料に対応repoのID、stars、活動日時なし。 / 商用利用ライセンス: 未確認。 / 導入: 論文記載の手法。公開コード/モデルの導入情報は与えられた資料にない。 / 資源: 13% parameters、最大8.0x推論速度は著者報告。具体的RAM/CPU資源と独立実測は未確認。 / 制約: 逐次推薦タスクに特化。 / 最新後継の実在ID: 保存資料内で未確認。

## SmartBench: Is Your LLM Truly a Good Chinese Smartphone Assistant?
https://arxiv.org/abs/2503.06029

判定草案: test / reading
中国語スマートフォン上の要約と情報抽出を含む20タスクのオンデバイスLLM評価基準で、分類/要約と構造化抽出の具体的な試用価値がある。論文は実機NPU量子化評価も記述し、READMEには評価手順がある。実行は外部コード隔離を次回確認してから。著者報告は独立検証済み実測ではない。

確認状況: 公開更新日: 論文v2は2025-08-26改訂。GitHub最終push 2025-09-26、updated 2026-10-05。 / 活動: stars 8、forks 1。 / 商用利用ライセンス: repo license null、未確認。データ元には各種ライセンスが列挙される。 / 導入性: READMEは結果生成・評価・処理の手順を記載。実際の依存関係と現環境への導入可否は未確認。 / 資源: 実機NPU量子化評価の記述あり。CPU/RAM/ダウンロード量は未確認。 / 制約: 中国語のスマートフォン利用場面向け。コード隔離を確認するまで実行不可。 / 最新後継の実在ID: 保存資料では確認不可。

## Understanding and Improving Knowledge Distillation for
  Quantization-Aware Training of Large Transformer Encoders
https://arxiv.org/abs/2211.11014

判定草案: defer / reading
小型Transformer encoderのQATでattention-map/output lossを使う知見はNLP向けに具体的だが、候補の手法はQATと学習を前提とし、現在の学習禁止条件に合わない。一次資料は論文のみで、今すぐ試せる導入物も確認できない。

確認状況: 公開更新日: 2022-11-20提出。保存資料では後続更新なし。 / stars/活動: repo情報がなく未確認。 / 商用ライセンス: 未確認。 / 導入: 保存資料にrepoや導入手順なし。 / 資源: CPU/RAM/DL量は未確認。 / 制約: QAT/蒸留の学習が必要で、現在は禁止。 / 最新後継の実在ID: 保存資料では確認不可。

## I-ViT: Integer-only Quantization for Efficient Vision Transformer
  Inference
https://arxiv.org/abs/2207.01405

判定草案: defer / reading
ViTのINT8整数専用推論は軽量画像認識に関係するが、記載された実測の展開先はGPU整数演算ユニットで、READMEもImageNetを用いるQATとTVMを案内する。CPUのみ・学習禁止の現条件で直接試す経路が確認できないため保留。

確認状況: 公開更新日: 論文v4は2023-08-07改訂。GitHub最終push 2024-09-02、updated 2026-07-28。 / 活動: stars 207、forks 26。 / 商用ライセンス: GitHub API資料でApache-2.0。 / 導入: READMEにはPyTorch/TVMの手順があるが現環境への導入は未確認。 / 資源: CPU/RAM/DL量は未確認。論文の実測はGPU整数演算。 / 制約: READMEのQAT手順はImageNetと30–90 epochsを推奨し、学習禁止条件と不一致。 / 最新後継の実在ID: 保存資料では確認不可。

## Lightweight Vision Transformer Compression for On-Device Plant Disease Detection in Resource-Constrained Agricultural Field Conditions
https://arxiv.org/abs/2609.05334v1

判定草案: defer / reading
唐辛子の病害3クラス分類という具体的な軽量画像認識事例で、論文は圧縮後6.01MB・精度95.13±2.32%を著者報告している。一方、提案はpruning/quantization/蒸留の逐次学習パイプラインであり、学習禁止に抵触する。比較値は著者報告で独立検証ではない。

確認状況: 公開更新日: 保存資料上v1提出日は2026-09-04。 / stars/活動: repo情報なし、未確認。 / 商用ライセンス: 未確認。 / 導入: 保存資料にrepo・重み配布先なし。 / 資源: 著者報告のモデルサイズ6.01MB。CPU/RAM/DL量・推論速度は未確認。 / 制約: 唐辛子3クラスの村落分割データ。提案統合パイプラインと比較学生はいずれも学習を伴う。 / 最新後継の実在ID: 保存資料では確認不可。

## Task-Specific Zero-shot Quantization-Aware Training for Object Detection
https://arxiv.org/abs/2507.16782

判定草案: defer / reading
物体検出向けのtask-specific zero-shot量子化は、合成キャリブレーション画像でデータ制約を避ける具体的な画像系の価値がある。ただし第2段階にtask-specific trainingを組み込み、コードはYOLO/Mask R-CNN/ViT向けの大きな研究用toolkitである。学習禁止かつCPU20分・RAM8GB・DL2GBの条件では今すぐ試す経路が確認できない。

確認状況: 公開更新日: 論文2025-07-22提出。GitHub最終push 2025-09-26、updated 2026-09-20。 / 活動: stars 31、forks 8。 / 商用利用: AGPL-3.0。商用利用時の義務との適合は未確認。 / 導入: 4系統の検証用実装と下位ディレクトリ別手順。現環境での導入可否は未確認。 / 資源: CPU/RAM/DL量未確認。 / 制約: 蒸留プロセスで学習を行う。ResNet Mask R-CNNの事前学習重みと評価手順はREADME TODO。 / 最新後継の実在ID: 保存資料では確認不可。

## GoiBERT: Compact Vietnamese Language Models via Two-Stage Knowledge Distillation
https://arxiv.org/abs/2610.30067v2

判定草案: test / reading
NLP分類・NERとCPU小型モデルの交点に新規性がある。論文は生テキストを扱う26M/49Mパラメータのベトナム語encoderを提示し、分類・感情分析では小型baselineに近い性能を著者報告しているため、ベトナム語の分類/抽出に使う価値を今すぐ確認する。これは著者報告であり当方の実測ではない。実行は次回、外部コードの隔離を確認してからにする。

確認状況: 公表更新日は資料上2026-10-22 (v2)。 / stars・repo活動は資料にrepoがなく未確認。 / 商用利用license未確認。 / 導入手順・checkpoint配布先/実サイズは資料内のthis https URLだけでは未確認。 / 著者は26M/49M parametersとするが実際のCPU速度・RAMは未測定。 / 論文はinferenceとNERでは圧縮コストが残ると記す。 / 保存資料だけではv2より新しい版や後継の実在IDを確認できない。

## Image Recognition with Online Lightweight Vision Transformer: A Survey
https://arxiv.org/abs/2505.03113

判定草案: defer / reading
軽量画像認識という独立の関心に合う調査論文で、効率設計・動的ネットワーク・蒸留の比較を俯瞰する用途はある。一方、surveyと同著者のまとめrepoが中心で、今すぐ試す個別モデル/実装の価値は示されず、具体的な画像タスクも未特定のため保留する。

確認状況: 公表更新日は資料上2025-09-26 (v3)。 / repo資料では10 stars、pushed_at 2024-10-21、updated_at 2026-08-18。活動の実質は未確認。 / repo license nullのため商用利用可否未確認。 / READMEは文献整理だが、実装導入手順は未確認。 / 実行資源・CPU適性は未確認。 / 保存資料に記載された内容はsurveyと参考文献一覧で、特定モデルの実測・比較値は抄録では未確認。 / 保存資料だけではv3より新しい版や後継の実在IDを確認できない。

## PTQ4SAM: Post-Training Quantization for Segment Anything
https://arxiv.org/abs/2405.03144

判定草案: defer / reading
SAMの画像認識に対するpost-training quantizationで、メモリ/計算コストを下げる具体的価値はある。しかしREADMEは実験に40GB超GPUを推奨し、CUDA ops、複数framework、COCOと複数のcheckpointを要するため、現行CPU/RAM/DL予算での試行条件を満たさない。GPU要件を理由に却下せず保留する。

確認状況: 論文公表日は2024-05-06。repo updated_atは資料上2026-09-21、pushed_atは2024-06-26。 / repoは85 stars、5 forks。 / repo license nullのため商用利用可否未確認。 / README記載の導入はPyTorch/MMCV等の導入、CUDA ops build、mmdetection setupを要する。 / READMEは実験用GPU >40GBを推奨。現行環境での資源実測なし。 / COCOとSAM/detector checkpointを準備する手順であり、予算内のDL容量は未確認。 / 精度・加速値は論文の著者報告で、当方の実測ではない。 / 保存資料だけでは後継の実在IDを確認できない。

## Bekko Embedding: Parameter-Efficient Multilingual Retrieval with Ultra-Compact Encoders
https://arxiv.org/abs/2607.25180

判定草案: reject / reading
これはmultilingual retrieval候補だが、保存論文で示される主な差分はretrieval benchmarkのnDCG順位/scoreと速度である。今回の検索候補に求める網羅性補完、前処理、新サイト発見の具体的進展は資料から確認できず、順位改善だけでは採用しない。著者報告の性能値は独立検証ではない。

確認状況: 公表日は資料上2026-07-28。 / stars・repo活動は保存資料にrepo情報がなく未確認。 / weights/dataのthis https URLしか示されず商用license未確認。 / 導入手順とweightsの実サイズ/形式は未確認。 / 論文はCPU速度を著者報告するが当方のCPU/RAM実測なし。 / search用途で必要なcoverage/preprocessing/new-site discovery改善は資料に記載確認できず。 / 保存資料だけでは後継の実在IDを確認できない。

## Squeez: Task-Conditioned Tool-Output Pruning for Coding Agents
https://arxiv.org/abs/2604.04979

判定草案: reject / reading
論文はcoding agentのtool-output pruningというAI駆動開発向けの具体的手法を扱い、著者はbenchmark上のrecall/F1とtoken削減を報告している。ただしこの案件では停止済みSqueezは読解のみで再開不可と明示されているため、再推薦・実行はしない。指標は著者報告で当方の実測ではない。

確認状況: 公表日は資料上2026-04-04。 / repo/stars/活動資料なし。 / 商用license未確認。 / 導入手順・weights配布・weights容量未確認。 / CPU/RAM実行資源は未確認。 / benchmark結果は著者報告で独立再現・実測なし。 / 保存資料だけでは後継の実在IDを確認できない。

## SWE-Pruner Pro: The Coder LLM Already Knows What to Prune
https://arxiv.org/abs/2607.18213

判定草案: test / reading
面向AI駆動開発の長いツール出力を、別の分類モデルでなくコーディングエージェント自身の内部表現で行単位に刈り込む新手法。論文は2バックボーン・4ベンチマークで品質維持と最大39%の入出力トークン削減を報告し、コンテキスト費用・遅延を下げる用途が具体的なので、隔離確認後に統合可能性を小さく調べる価値がある。これは著者報告で独立実測ではない。

確認状況: 公開更新: arXivは2026-07-20投稿、READMEは2026-07-22のhead・訓練コーパス公開を記載。repo APIのupdated_atは2026-10-15、最終pushは2026-07-22。stars 27、forks 5。 / 商用ライセンス: repo APIではlicense null。READMEのApache-2.0バッジ表記だけでは商用利用条件を確定できない。 / 導入・資源: Python 3.10+、PyTorch 2.9/CUDA 12.xの論文環境。SGLang改変、稼働中backbone、head checkpointが必要。CPU20分・RAM8GB内での動作は未確認。 / 制約: 論文の数値は著者報告で独立実測なし。repoの更新活動と公開headの取得・サイズ、最新後継の実在IDは未確認。

## SWE-Pruner: Self-Adaptive Context Pruning for Coding Agents
https://arxiv.org/abs/2601.16746

判定草案: defer / reading
コーディング作業の目標に応じて周辺コードを選択する0.6B skimmerで、トークン削減と成功率維持・改善を著者が報告している。用途は具体的だが、同じ作者の新しいSWE-Pruner Proがエージェント内部表現を使う別方式として提示されているため、旧skimmerを今試す優先度やCPU資源適合性を先に見極めたい。

確認状況: 公開更新: arXiv最終改訂は2026-05-07。repo API updated_atは2026-10-20、最終pushは2026-06-30、stars 320、forks 25。 / 商用ライセンス: repo APIはMIT License。モデル重み・データ各々の商用条件は未確認。 / 導入・資源: READMEはPyPI導入とHugging Faceモデルを案内。モデル実サイズ、CPU/RAM所要量、現行依存関係は未確認。 / 制約: 0.6Bモデル推論のCPU20分・RAM8GB適合性と実タスクでの再現性は未確認。最新後継実在IDは未確認。

## Trae Agent: An LLM-based Agent for Software Engineering with Test-time
  Scaling
https://arxiv.org/abs/2507.23370

判定草案: test / reading
自然言語指示からツールを使う一般的なソフトウェア開発エージェントで、モジュール構成を研究・拡張できる点がAI駆動開発に直接役立つ。READMEはCLI、複数LLM接続、軌跡記録とMITを示し、既存の自動化作業を置き換えずに隔離環境で設定・拡張性を評価する価値がある。論文の性能比較は著者報告。

確認状況: 公開更新: 論文は2025-07-31投稿。repo API updated_atは2026-10-23、最終pushは2026-02-05、stars 12155、forks 1361。 / 商用ライセンス: repo APIおよびREADMEはMIT。 / 導入・資源: READMEはPython 3.12+とuvを要求。利用には選択したLLM providerのAPI keyが必要で、現金予算0の範囲で利用可能な経路は未確認。 / 制約: 著者のベンチマーク値は独立実測ではない。依存のCPU/RAM適合性と最新後継の実在IDは未確認。

## What Survives When You Compress a Recursive Reasoner for the Edge?
https://arxiv.org/abs/2606.26488

判定草案: defer / reading
エッジ向け再帰推論モデルについて、量子化校正の挙動と推論経路の忠実度を調べ、3.3MBのINT4バックボーンを報告する研究。CPU小型モデルの関心に合う可能性はあるが、提示資料にはコード・重み・導入手順がなく、実際に試せるartifactと用途を確認できていない。

確認状況: 公開更新: arXiv初版2026-06-25、最終改訂2026-09-30。repoの更新日、stars、活動状況は資料なし。 / 商用ライセンス: 未確認。 / 導入・資源: 抄録は3.3MB backboneと述べるが、他の実行時メモリ、CPU速度、コード・重みの提供は未確認。 / 制約: 結果は著者報告で独立検証なし。最新後継の実在IDは未確認。

## ZeroQuant: Efficient and Affordable Post-Training Quantization for Large-Scale Transformers
https://arxiv.org/abs/2206.01861

判定草案: defer / reading
ZeroQuantは大規模TransformerのINT8/INT4量子化と高速サービングを目的とする。モデル精度と推論効率のトレードオフを調べる基礎資料にはなるが、資料中の実例はGPT-J 6BやGPT-NeoX 20B等のサーバー級モデルで、今回のCPU小型モデル向けの直接的な導入価値は未確認。

確認状況: 公開更新: arXiv投稿2022-06-04。repoの更新日、stars、活動状況は資料なし。公開年だけを理由に棄却していない。 / 商用ライセンス: 未確認。 / 導入・資源: 抄録は大規模モデル用の量子化・推論backendを説明するが、現在のCPU8GB環境への導入適合性は未確認。 / 制約: 抄録の性能値は著者報告で独立実測なし。最新後継の実在IDは未確認。

## IQuest-Q1: Advancing Foundation Capabilities for Agentic CLI Systems
https://arxiv.org/abs/2610.30949

判定草案: defer / reading
An agentic CLI foundation model has direct potential for AI-driven development, so size alone is not a rejection. But the official IQuest-Q1 artifact is a 320B-parameter BF16 checkpoint with 649,978,660,440 bytes of weights, far beyond this review’s 2-GB download, 8-GB RAM, and 20-minute CPU limits; license is custom/other. Defer until an applicable smaller official artifact or feasible hosted evaluation is identified.

確認状況: Paper submitted 2026-10-22. Official HF ID IQuestLab/IQuest-Q1 updated 2026-09-29; search snapshot showed 148 likes and 4,130 downloads for official ID, and community GGUF/FP8 IDs exist but are not official. Official card says license_name iquest-q1 (custom terms), so commercial-use status must be reviewed. / Official BF16 checkpoint is 320,318,615,552 parameters and 649,978,660,440 bytes; no supplied official reduced-size successor. CPU/GPU runtime and install requirements are not established. Resource mismatch requires defer; no candidate code/model execution performed. / Quantized/community format size is not confirmed in supplied official evidence and cannot be substituted for official checkpoint/license. Future official successor existence is unconfirmed.

## RouteWeaver: Weaving Mode Selection and Execution into Unified LLM Routing
https://arxiv.org/abs/2610.19705

判定草案: defer / reading
Unified mode selection plus model selection has a plausible AI-development value for choosing single-turn, multi-turn, or agentic execution. However, the supplied official 4B router artifact is 8,822,894,520 bytes, above the 2-GB download allowance, and no smaller newer successor is verified. Defer a model test until a feasible artifact or alternate bounded evaluation is identified; this is a resource hold, not a claim of no value.

確認状況: Paper submitted 2026-10-19; repo has 2 stars, updated 2026-10-23 but last pushed 2026-10-16, Apache-2.0. HF IDs e2rea1/RouteWeaver-4B and e2rea1/RouteWeaver-4B-cost exist, Apache-2.0, last modified 2026-10-09; router-only weights are 8,822,894,520 bytes. Cost variant has a real ID but distinct weights; do not conflate sizes. / No smaller successor confirmed. Exact install/runtime and CPU/GPU needs unverified; given the supplied artifact exceeds 2-GB download cap, execution is held. No measured comparison by us.

## Cola2: Co-Adaptive Hierarchical Language Modeling with Continuous Semantic Compression and Balanced Discrete Generation
https://arxiv.org/abs/2610.31305

判定草案: defer / reading
連続表現の圧縮と離散文生成を協調させる方法はNLPの表現・生成効率に結びつく可能性がある。ただし今回の資料では具体的な利用可能コード/モデルやCPUで試せる導入経路を確認できず、学習禁止・計算枠内で評価可能な手順がない。

確認状況: 2026-10-22提出のv1を確認。stars/活動、商用利用ライセンス、導入手順、資源要件は資料になく未確認。has_code=false・has_model=false。最新後継モデルの実在IDは未確認。学習や推論の制約も不明。

## Rethinking Sequence Compression with Test-time Regression
https://arxiv.org/abs/2610.31574

判定草案: test / reading
Sequence compression is relevant to efficient language and multimodal inference; the paper reports a concrete visual-token-merging use and sparse-attention use, so it merits a bounded, isolated feasibility check. These are author-reported results, not independently measured here. HF lists 10 upvotes and AI Weekly separately covered it, but the latter is a short summary of the same paper, so independent interest is a weak second signal; category remains unset.

確認状況: Paper submitted 2026-10-22; repo README says code/models released 2026-10-20. Repo has 8 stars, pushed 2026-10-23; GitHub license field is null, so commercial-use terms are unconfirmed. / Official collection contains ready checkpoints (so training is not inherently required): sihany/ttr_345m, ttr_1b, ttr_conv_345m, and sihany/ttr_vlm_conv_3_3. The 345m-named model is actually 446,631,936 parameters with an 893,293,472-byte safetensors file; config specifies flash_attention_3. 1b is 2,247,931,312 bytes; vision conv checkpoint totals 9,465,033,968 bytes, beyond the 2-GB download allowance. Do not combine alternate formats. / Execution not attempted; resource/runtime requirements remain unverified. Isolated inspection is required before any execution. HF small model last modified 2026-10-17; 1b 2026-10-17; vision 2026-10-19. Latest successor beyond these IDs is unconfirmed. / HF 10 upvotes and AI Weekly item are two distinct sources, but the news item appears to summarize the paper rather than report separate evidence; treat its independent-signal strength as weak. No comparative reproduction was performed; reported 55.6% is author-reported.

## Is Complex Agentic Memory Always Needed? Naive RAG Is Stronger Than We Thought
https://arxiv.org/abs/2610.30755

判定草案: test / reading
会話メモリ/質問応答のNLP経路で、履歴を要約・編集せずdense retrievalとrerankで根拠を渡す単純な基準線を比較する価値がある。論文は4 benchmarkの7設定中6設定で比較対象首位と報告し、欠落する関係情報を生履歴から補える可能性を示す（著者報告）。これは外部サイト検索の改善主張ではない。

確認状況: 2026-10-22提出のv1を確認。repoは2026-10-23更新、7 stars、直近push 2026-10-23。ライセンス表示なし（商用利用可否未確認）。READMEにPython 3.12等の導入説明と外部reader/embedding設定があるが、具体的なCPU/RAM/GPU・API費用は未確認。最新後継モデルの実在IDは未確認。隔離未確認の外部コードは実行しない。

## Policy-Mask: Budget-Aware Adaptation of Diffusion Features to Discriminative Tasks
https://arxiv.org/abs/2610.28889

判定草案: defer / reading
画像認識の軽量化には関係する。拡散モデルの中間特徴からタスクごとに時刻・層・チャネルを選ぶ学習方式で、著者は分類等の結果を報告しているが、コードも重みもなく、下流目的での学習を要するため、学習禁止・現金0の条件で今すぐ試す具体手段が確認できない。

確認状況: 公表日: 2026-10-20。保存資料に後続版や最新性の確認なし。 / コード/重みなし。stars・活動・商用ライセンス・導入方法は未確認。 / ポリシー学習と拡散教師の実行資源・CPU/RAM/ダウンロード量は未確認。 / 後継/代替の実在IDは未確認。著者報告値は記載されているが独立実測ではない。

## SCOPE: Compiling Natural-Language Policies into Neurosymbolic Predicate Programs
https://arxiv.org/abs/2610.31062

判定草案: test / reading
This is a direct fit for NLP policy classification and structured verdict extraction: natural-language rules are compiled into small neural predicates plus deterministic extractors and compositional logic. The real Plurai/SCOPE dataset is MIT licensed, making a small reading or data-shape check worthwhile; the paper describes joint program/data optimization, not necessarily training model weights.

確認状況: Paper submitted 2026-10-22; SCOPE dataset ID Plurai/SCOPE exists, last modified 2026-10-11, MIT tag, public and ungated, under 1K items, 2 downloads/0 likes in supplied snapshot. / No repository supplied; implementation and installation path are unconfirmed. Dataset artifact exists, but executable program/predicate resources and their exact CPU/RAM/download footprint are unconfirmed. / No training need is established: paper says program optimization jointly uses its training data; whether any specific reproduction requires model-weight training is unconfirmed. No later successor ID confirmed.

## Meta Expert: Cross-Layer Expert Sharing for Storage- and Retrieval-Efficient MoE
https://arxiv.org/abs/2610.27598

判定草案: defer / reading
MoEの層間で専門家を共有し保存・検索を効率化する研究だが、大規模MoEモデル設計に関するもので、現在のCPU小型モデルやNLP分類/抽出へ今すぐ適用する成果物が保存資料から確認できない。検索改善としての網羅性・前処理・新サイト発見の進展も示されていない。

確認状況: 公表日: 2026-10-20。保存資料に後続版や最新性の確認なし。 / コード/重みなし。stars・活動・商用ライセンス・導入手順は未確認。本文ライセンス表示はCC BY 4.0。 / MoE学習/推論に必要なモデル規模、資源、CPU適合性は未確認。 / 後継/代替の実在IDは未確認。

## Same Simulation, Different Evidence: Where Retrieved Tool Output Costs LLM Agents Accuracy
https://arxiv.org/abs/2610.26836

判定草案: test / reading
This targets an actionable AI-development issue in agents that inspect large tool output: retrieval can cause evidence substitution even when simulation output is correct. Its value is about harness behavior and structured evidence use, not external web search. A small static reading/check is worthwhile; no author result is independently reproduced.

確認状況: Paper submitted 2026-10-19; repo has 0 stars, last pushed 2026-10-20, MIT license. / Repo/release installation and data download requirements are unconfirmed; repository is available but no execution was attempted. CPU/RAM and external model/API costs for reproducing reported results are unconfirmed. / No later successor or independent replication confirmed. Paper reports author experiments; no comparative measurement by us.

## VerifierBench: Benchmarking the Generation of Executable Verifiers for LLM Agents
https://arxiv.org/abs/2610.28679

判定草案: test / reading
Verifier generation is directly useful to AI-driven development and evaluation: it checks executable validators for both false accepts and false rejects. The supplied paper reports a material precision/recall-style tradeoff, while a small static inspection of its frozen benchmark can be useful without claiming full reproduction. Keep API and large-scale reproduction requirements separate from this bounded inspection.

確認状況: Paper submitted 2026-10-19. Repo has 3 stars, pushed 2026-10-22, Apache-2.0. HF dataset Catherine1213/VerifierBench exists, updated 2026-10-23, public/ungated; 122 downloads, 0 likes, license tag other; card says frozen 222 tasks and 4,993 candidates. AppWorld content requires local reconstruction and source terms are in LICENSE.md. / Full reproduction requires API access and roughly 100-GiB x2 per supplied correction; these are not requirements for a small static check. Installation details and whether static inspection can be done within CPU/RAM/download/time limits remain unverified. / A later successor ID and independent replication are unconfirmed. Reported comparison 68.3 vs 62.1 has attack false acceptance 18.2 vs 7.4, an author-reported tradeoff, not a uniformly better result.

