# 一次資料読解台帳

初回native草案には引用不一致7件があり不採用。追加4件の訂正判定を優先。候補実行0、全件未完了。

## Qwen2.5-Coder-1.5B-Instruct
https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct

判定草案: reject / reading
昨日すでに詳細報告された候補で、今回の資料はモデルカードと既存1.5Bモデルの説明です。API記載の最終更新は2025-01-12で、昨日以降の有意な新証拠を確認できないため再推薦を抑制します。一次資料上は小型のコード生成・修正モデルでCPU試行候補になり得ますが、量子化済み重みや実測CPU性能は示されていません。

確認状況: 公開更新日の資料記載: APIでは最終更新2025-01-12。 / stars数・最近の活動は添付資料にありません。 / 商用ライセンスはモデルカードにApache-2.0とあるが、実際の利用条件の確認は未実施。 / 導入例はTransformersのGPU自動配置を使う例で、CPU量子化パッケージやCPU速度は未確認。 / 資源使用量・重みサイズ・8GB RAMでの実行可否は未測定。 / 最新の後継実在IDは資料から確認できません。

次: None

## A Survey on Inference Optimization Techniques for Mixture of Experts
  Models
https://arxiv.org/abs/2412.14219

判定草案: defer / reading
MoE推論をモデル・システム・ハードウェアの各層から整理するサーベイで、圧縮や蒸留を含むため小型モデルの効率化を調べる参考候補です。関連リポジトリは更新・利用状況が確認できますが、主題はMoE推論で、8GB RAM/2GB取得枠のCPU小型モデルに直接適用できる手法があるかは添付要旨だけでは分かりません。適用可能な節を確認できるまで保留します。

確認状況: 論文は2025-01-22改訂(v2); arXivページにはACM CSUR 2026の誌面情報がある。 / 関連リポジトリは2026-10-01更新、428 stars、19 forks。 / 論文サーベイの商用ライセンスは資料にありません。リポジトリのlicenseもnull。 / 論文本文の詳細と各手法の導入条件は未確認。 / MoE対象の必要資源、8GB RAM/2GB取得枠への適合、CPUで使える具体的実装は未確認。 / 後継サーベイの実在IDは資料から確認できません。

次: 付属リポジトリの圧縮・量子化節を読み、密な小型CPUモデルにも転用できる具体的手法があるか確認する。

## AfriNLLB: Efficient Translation Models for African Languages
https://arxiv.org/abs/2602.09373

判定草案: reject / reading
一次資料で確認できる対象はアフリカ諸言語間の機械翻訳で、提示された関心のNLP分類・要約とは異なる用途に限定されています。15言語ペア、枝刈り・量子化したNLLB-200 600M、翻訳用データ公開という具体的価値はありますが、現在の対象タスクに対する採用理由は確認できません。

確認状況: 論文は2026-08-07改訂(v2)、AfricaNLP 2026採択と記載。 / stars数・最近のコード活動は添付資料にありません。 / モデル・データの商用ライセンスは添付資料にありません。 / 論文要旨はTransformers版とCTranslate2版の公開を述べるが、実在するモデルIDや導入手順は確認できません。 / 個別モデルの資源要件、CPU速度、8GB RAM/2GB取得枠への適合は未確認。 / 最新の後継実在IDは資料から確認できません。

次: None

## Gated Relational Alignment via Confidence-based Distillation for Efficient VLMs
https://arxiv.org/abs/2601.22709

判定草案: reject / reading
GRACEは昨日すでに詳細報告された候補です。添付されたリポジトリ情報の最終更新は2026-10-03、リリースは2026-08-15で、昨日の報告に対する新しい証拠ではありません。資料はQwen3-VL-2Bの実パックINT4配布とCUDA/NVIDIA前提の推論を示しますが、既報再通知は抑制します。

確認状況: 論文は2026-06-26改訂(v5)、ICML 2026採択と記載。 / GitHubは2026-10-03更新、211 stars、7 forks。 / リポジトリlicenseはApache-2.0。個別モデル配布条件の確認は未実施。 / READMEはHub配布AWQ重み、推論手順、ZeroGPUデモを案内しているが、デモの動作は未確認。 / 著者の報告値はベンチマーク平均75.0等。手元での実測はしていない。 / 同系列の最新後継モデルIDは資料から確認できません。

次: None

## SLMRec: Distilling Large Language Models into Small for Sequential
  Recommendation
https://arxiv.org/abs/2405.17890

判定草案: reject / reading
一次資料が扱うのは、利用履歴から次の品目を予測する逐次推薦専用モデルです。小型化・蒸留・速度向上の報告はありますが、現在の関心である分類・要約やCPU汎用小型モデル用途とは明確に異なるため、具体的な採用理由がありません。

確認状況: 論文は2025-04-20改訂(v4)、ICLR 2025と記載。 / stars数・最近の活動は添付資料にありません。 / 商用ライセンスは添付資料にありません。 / コード実装や導入手順は添付資料にありません。 / 実モデルの資源要件・CPU速度・配布サイズは未確認。論文の13%は著者報告の比較値。 / 最新の後継実在IDは資料から確認できません。

次: None

## SmartBench: Is Your LLM Truly a Good Chinese Smartphone Assistant?
https://arxiv.org/abs/2503.06029

判定草案: test / reading
中文手机端摘要与信息抽取是直接对应的NLP用途；SmartBench按手机交互划分20项任务，含每项50–200个问答，并覆盖真实NPU量化部署评估，适合作为中文端侧助手分类/抽取评测集。论文与作者仓库属于同一项目，尚不足以认定独立双信号深挖；当前先核查可复用数据及评测依赖。

確認状況: 论文更新日期：2025-08-26；仓库元数据更新于2026-10-05，最近推送2025-09-26；stars 8、forks 1。 / 仓库未声明许可证；release列表为空。README列出若干来源数据集各自许可，但不能据此推定SmartBench整体或所有数据均可商用。 / 部署证据是特定vivo iQOO 12/骁龙8 Gen 3 NPU上的W4A16评估；实际复用所需存储、CPU/RAM、下载量和非NPU路径未确认。 / 数据/代码声称可用且仓库存在，但具体文件可用性、安装难度及许可兼容性尚未逐项核验。保存资料未给出最新后继基准的可验证ID。

次: 核对仓库中的数据文件、评估脚本依赖和数据许可，确认是否能在不训练模型的条件下单独复用分类/抽取子集。

## Understanding and Improving Knowledge Distillation for
  Quantization-Aware Training of Large Transformer Encoders
https://arxiv.org/abs/2211.11014

判定草案: defer / reading
论文提出attention-map与attention-output蒸馏损失用于Transformer encoder的量化感知训练，并报告sub-2-bit QAT效果；但核心收益依赖训练，而当前明确禁止训练。可作为将来允许训练时的技术参考，当前没有足够资料证明可直接改善现有NLP分类/摘要或CPU小模型。2022年的发表时间本身不作为否决理由。

確認状況: 公表日期：2022-11-20；保存资料没有代码仓库、活动/star数据、许可证或release信息。 / 商用许可、训练数据与教师/学生模型要求、训练时长及硬件资源均未确认；方法描述的QAT与当前禁止学习的约束冲突。 / 保存资料未提供复现导引或最新后继方法的可验证ID，不能据此断言其仍为最新。

次: None

## I-ViT: Integer-only Quantization for Efficient Vision Transformer
  Inference
https://arxiv.org/abs/2207.01405

判定草案: defer / reading
I-ViT以Shiftmax/ShiftGELU实现ViT INT8整数推理，方向对应轻量图像识别；论文的部署实证明确是GPU整数算术单元，且复现实验依赖ImageNet QAT训练。当前无GPU且禁止训练，因此暂不进入试跑；若已有兼容量化权重及CPU推理后端，才可能适用。

確認状況: 论文最后更新：2023-08-07；仓库最近推送2024-09-02，元数据更新2026-07-28；stars 207、forks 26。 / 仓库为Apache-2.0；商用时仍需核实依赖及模型/数据许可。 / README给出ImageNet训练命令，建议30/60/90 epochs；训练成本与当前禁止训练的限制不符。CPU端延迟、RAM、权重下载量和目标CPU后端支持情况未确认。 / 部署报告针对GPU整数单元，不应外推为CPU实测。保存资料未提供可验证的更新后继项目ID。

次: None

## Lightweight Vision Transformer Compression for On-Device Plant Disease Detection in Resource-Constrained Agricultural Field Conditions
https://arxiv.org/abs/2609.05334v1

判定草案: test / reading
辣椒病害三分类论文报告跨村庄、跨设备测试，并在6.01 MB INT8大小下给出95.13±2.32%准确率，能为轻量图像识别提供一个贴近低资源设备的可核查案例。论文还指出同尺寸直接训练学生达到94.87%，说明复杂剪枝/蒸馏是否值得需谨慎；当前价值是读证据并检查可复用模型/代码，不宣称本地运行或实测。

確認状況: 公表日期：2026-09-04；本批保存资料仅含arXiv摘要页，没有仓库、stars、近期活动或release信息。 / 代码、权重、数据和商用许可均未在保存资料中确认；论文中的小模型体积不能替代实际下载体积或运行内存测量。 / 方法涉及剪枝、量化、蒸馏与训练；本地训练禁止。摘要未说明训练GPU/CPU成本，也无可确认的CPU推理时延。 / 论文报告的是作者实验结果，不是本环境实测；保存资料没有后继版本或更新方法的可验证ID。

次: 查找作者公开的权重/代码与数据许可，并核对6.01 MB模型的CPU推理依赖和实测时延是否适合8 GB RAM设备。

## GoiBERT: Compact Vietnamese Language Models via Two-Stage Knowledge Distillation
https://arxiv.org/abs/2610.30067

判定草案: defer / reading
新鮮な一次資料では、ベトナム語の26M/49Mパラメータraw-text encoderで、論文著者は分類/NLI/NER等8ベンチマークで compact baseline 比の成績・throughputを主張。CPU小型NLP候補として筋はあるが、現行配布はTwoheartsTechnologies/GoiBERT-Sと-GoiBERT-Mへ移転し、どちらもgeneral-distillation backboneのみでtask headなし。今の予算では学習不可のため分類を今すぐ試す道が確定しない。さらに新ライセンスはcommunity条件付き商用利用で、規模不明の組織に本番採用を推せない。特定タスクheadまたは既存評価器が利用可能か確認後に再検討。

確認状況: 論文の比較値とthroughputは著者報告で、この検討では独立再現していない。論文v2は2026-10-22改訂。 / 公式GitHubは2026-10-23更新、5 stars/2 forks、repo license欄なし。現行配布HF IDsはTwoheartsTechnologies/GoiBERT-SとTwoheartsTechnologies/GoiBERT-M（各2026-10-24更新、各1 like）。旧2HeartsVN IDの最新状態は保存資料では断定しない。 / model.safetensors実サイズはS 105,609,040 bytes、M 197,486,112 bytes。S/MともF32 safetensorsメタデータあり。どちらもtask headなし。 / 商用条件は新しいGoiBERT Community Model License v1.0（2026-10-22）で、組織グループが100 personnel未満かつ1,000顧客未満の場合のみcommunity commercial use許可。BSD-3-Clause-Clear、Qualcomm RAIL等上流条件も累積適用。大規模組織には別契約が必要。 / goi-intent-v1はdelivery bot用の内部intent分類器で、カードにinternal use only/legal reviewとあるため汎用head扱いしない。公表済みhead付きの他の適合タスクIDは未確認。 / 依存関係、8GB RAMでの実メモリ、CPU速度/品質は未計測。2026-10-24以降の後継配布IDは保存資料だけでは確認できない。

次: 学習なしで使える、対象業務に合致した分類headまたは評価済み分類器が手元にあるか確認する。

## Task-Specific Zero-shot Quantization-Aware Training for Object Detection
https://arxiv.org/abs/2507.16782

判定草案: test / reading
物体検出の軽量化という画像認識経路に直結し、実データなしでタスク固有の合成校正画像を作り量子化モデルを蒸留する設計は具体的価値がある。著者らはYOLOv5/YOLO11/Mask R-CNN/ViT向け実装を公開しているため、隔離環境を確認後に1系統の再現可能性を調べる価値がある。論文・READMEの主張で、こちらの実測ではない。

確認状況: 論文は2025-07-22提出、ICCV 2025採択と記載。リポジトリの最終pushは2025-09-26、GitHub更新は2026-09-20、stars 31。 / AGPL-3.0。商用利用時の適合性・義務は未確認。 / READMEに再現手順への参照はあるが、対象バックボーンごとの依存導入と評価手順は未確認。README TODOに評価手順未公開とある。 / 実際のCPU/RAM/ダウンロード容量は未確認。量子化学習・蒸留は今回未実施。 / 最新後継の実在IDは未確認。

次: 外部コードを隔離できることを確認し、依存関係と配布済みYOLO重みのサイズを調べてCPU/RAM制約内で校正前処理だけを試せるか判断する。

## Image Recognition with Online Lightweight Vision Transformer: A Survey
https://arxiv.org/abs/2505.03113

判定草案: test / reading
軽量画像認識ViTの構成設計・動的ネットワーク・知識蒸留をImageNet-1Kの精度/パラメータ/throughputの観点から整理する調査論文で、画像認識経路の方式選定に使える。個別モデルを今すぐ採用する実装ではなく、まず掲載方式から小さなCPU候補を絞る価値。

確認状況: 論文は2025-05-06提出、最終改訂は2025-09-26 (v3)。GitHub最終push 2024-10-21、更新 2026-08-18、stars 10。 / リポジトリlicense未設定。個別掲載手法・実装の商用ライセンスは未確認。 / GitHub READMEは論文要約と既存研究リンク中心で、統一評価コード/導入手順は未確認。 / 各モデルのCPU/RAM/ダウンロード容量は未確認。 / 最新後継の実在IDは未確認。

次: 調査対象からCPU推論を明記したモデルを一つ選び、配布形式・ライセンス・8GB RAMでの導入可否を確認する。

## PTQ4SAM: Post-Training Quantization for Segment Anything
https://arxiv.org/abs/2405.03144

判定草案: defer / reading
SAMのPTQで実データ再学習を避け、量子化時の活性分布とsoftmaxに対処する手法は画像認識/軽量化に関連する。ただしREADMEはCUDA operatorのビルド、COCOと複数の重みを要求し、40GB超GPUを推奨する。現予算・CPU限定の試行条件と合わないため、SAMの具体的利用先とGPU環境が整う場合まで保留する。

確認状況: 論文は2024-05-06提出。リポジトリ最終push 2024-06-26、更新 2026-09-21、stars 85。 / リポジトリlicense未設定で商用利用条件は未確認。 / READMEにPyTorch/MMCV、CUDA operator、mmdetectionの導入手順あり。実際の導入成功は未確認。 / READMEは40GB超GPUを推奨。CPU/RAM実行可能性、重みやCOCOのダウンロード総量は未確認。 / 最新後継の実在IDは未確認。

次: SAMを対象にする実案件と40GB超GPUがあるかを確認し、両方あれば隔離環境で配布重みとCUDA依存を見積もる。

## Bekko Embedding: Parameter-Efficient Multilingual Retrieval with Ultra-Compact Encoders
https://arxiv.org/abs/2607.25180

判定草案: reject / reading
一次資料で確認できる用途は多言語dense retrieval encoderであり、示す主な改善もretrieval順位指標・速度である。今回の検索目的で必要な網羅性補完、前処理、新サイト発見の進展は示されず、NLP分類/要約用途にも結びつかない。CPU小型でもこの候補の採用理由としては具体的不足が確定する。

確認状況: 論文は2026-07-28提出のv1。 / 候補資料にrepo/model cardがなく、stars/活動状況、licenseと商用利用条件、導入方法は未確認。 / 論文は8M/25M active parameters、ONNX/OpenVINO 124 MiBの著者記載値を示すが、CPU/RAMや推論結果は独立に実測していない。 / 最新後継の実在IDは未確認。

次: None

## Squeez: Task-Conditioned Tool-Output Pruning for Coding Agents
https://arxiv.org/abs/2604.04979

判定草案: defer / reading
coding agent向けにtool出力から必要な証拠を残して文脈量を減らす課題はAI駆動開発に具体的に関連する。論文はQwen 3.5 2Bを用いた著者報告の評価を示すが、この候補は既に停止扱いで読解のみ、再開禁止の指示があるため推薦・実行に戻さず保留する。

確認状況: 論文は2026-04-04提出。候補資料はarXivのみでrepoのstars/活動状況、licenseと商用条件は未確認。 / 導入手順・公開重みの有無、CPU/RAM、モデル重みのダウンロード容量は未確認。 / 数値は著者報告であり独立実測ではない。 / 最新後継の実在IDは未確認。 / 既停止案件の再開禁止により追加確認・試行なし。

次: None

## SWE-Pruner Pro: The Coder LLM Already Knows What to Prune
https://arxiv.org/abs/2607.18213

判定草案: test / reading
coding-agent向けに、別分類器ではなく実際のエージェント隠れ状態からツール出力の行を剪定する新機構。著者報告では最大39%の入出力token削減と品質維持、MiMoでSWE-Bench Verified +3.8%としており、長いコード読解のコストと文脈保持の改善を試す価値がある。独立した話題信号は未確認のためカテゴリなし。実行は外部コード隔離確認後。

確認状況: READMEの更新日は2026-10-15（repo updated_at）、最新pushは2026-07-22。stars 27/forks 5。 / API repoのlicense欄はnull。READMEのApache-2.0バッジ表記との不一致を解消して商用利用条件確認が必要。 / 導入はパッチ適用済みSGLang、隠れ状態取得、学習済みhead、対応バックボーンを要求。必要GPU/RAM、実際のチェックポイント配布容量は未確認。 / 独立評価、現行の互換性、最新後継モデルの有無は保存資料では未確認。

次: 隔離環境で対応バックボーンとSGLang overlayの有無を確認し、代表的な長いツール出力1件で剪定前後のtoken数と回答保持を比較する。

## SWE-Pruner: Self-Adaptive Context Pruning for Coding Agents
https://arxiv.org/abs/2601.16746

判定草案: defer / reading
タスク指定に応じて周辺コードから行を選ぶ0.6B skimmerで、coding agentの文脈token削減という用途は合う。今回のSWE-Pruner Proと同じ著者・同じ課題領域で、より新しいProが隠れ状態を使う方式を提示しているため、旧方式を今すぐ別途試す優先度は低い。Proで満たせないモデル互換性が確認できた場合に再検討。

確認状況: 論文最終改訂は2026-05-07 v4。repo updated_at 2026-10-20、last push 2026-06-30、stars 320/forks 25。 / repo licenseはMIT。商用利用条件は資料上MITと表示されるが、重みの個別条件は未確認。 / PyPI配布はREADMEに記載。8GB RAM/2GB DL枠での導入可否と実測CPU速度は未確認。 / 保存資料で確認できる後継候補は同著者のSWE-Pruner Pro（2607.18213）。

次: Proが対象バックボーンに非対応と確認された場合のみ、0.6BモデルのCPU推論要件と公開重み容量を調べる。

## Trae Agent: An LLM-based Agent for Software Engineering with Test-time
  Scaling
https://arxiv.org/abs/2507.23370

判定草案: defer / reading
Trae Agentはissue解決用の複数エージェント構成を提供し、AI駆動開発への直接的な用途がある。論文ではPass@1の改善を報告する一方、実利用には選択したLLM providerのAPI keyまたはローカルOllama構成が必要で、現金予算0では有料APIを前提にすぐ試す条件が揃わない。ローカル構成の資源確認後に判断する。

確認状況: 論文公開日は2025-07-31。repo updated_at 2026-10-24だがlast pushは2026-02-05、stars 12,152/forks 1,359。 / repo licenseはMIT。 / 導入はUV、Python 3.12+。Ollama利用可能との記載はあるが、具体的モデルのRAM/CPU性能と2GB以内の取得可否は未確認。 / 最新後継の実在IDは保存資料で未確認。

次: Ollamaで利用可能な既存モデルがあるか確認し、API支出なしの最小課題実行が可能かを調べる。

## What Survives When You Compress a Recursive Reasoner for the Edge?
https://arxiv.org/abs/2606.26488

判定草案: defer / reading
数百万parameterのrecursive reasonerをedge向けに圧縮する研究で、軽量モデル関心に合う。特にINT4の影響が再帰アーキテクチャで異なり、per-channel calibrationで回復するという実用仮説が示される。ただし今回の資料は論文抄録のみでコード、モデル、導入手順が確認できず、検証可能な成果物を確認してから試す。

確認状況: 論文最終改訂は2026-09-30 v2。repo/stars/活動状況は資料なし。 / 商用ライセンス、実装・重みの公開有無、導入方法、RAM/CPU実測は未確認。 / 著者の抄録にある3.3MB/6x値は著者報告であり、独立実測ではない。 / 最新後継モデルの実在IDは資料で未確認。

次: 論文に対応する公開コードと小型backboneのIDがあるか確認し、実在すれば校正手順とライセンスを読む。

## ZeroQuant: Efficient and Affordable Post-Training Quantization for Large-Scale Transformers
https://arxiv.org/abs/2206.01861

判定草案: reject / reading
ZeroQuantはINT8/INT4で大規模BERT/GPT系モデルのサーバー推論コストを下げる研究であり、今回の小型CPUモデル関心に直接対応しない。保存資料で対象として挙がるのはGPT-J 6BやGPT-NeoX 20Bで、手元CPU向けの小型モデルや具体的導入成果物は示されていないため、候補としては不要。

確認状況: 論文公開日は2022-06-04。今回の資料にrepo/活動/stars情報なし。 / 商用ライセンス、現在の実装配布先、導入/CPU対応、資源要件は未確認。 / 新しい量子化手法との比較および後継実装の実在IDは資料で未確認。

次: None

## WeVisOne-VL: Unified Visual Perception from Objects to Pixels
https://arxiv.org/abs/2610.31329

判定草案: defer / reading
検出・OCR・カウント・pointing・segmentation・mattingを一つで扱うため、画像認識の具体的な価値はある。だが確認できた公式checkpointは2B/4Bで軽量画像認識の想定と合わず、F32 safetensorsだけでも2B版9,767,053,328 bytes、4B版19,322,413,240 bytes。4Bは2GB DL上限とRAM 8GBを大幅に超え、ローカル試行不可。HF Spaceは公式モデルを用いたdemoだが、hugging-apps側の実装adaptationで独立性能再現とは扱えない。GPU利用の見通しが立つまで保留。

確認状況: 論文は2026-10-22公開。公式repoメタデータ上のstars/直近活動とrelease一覧はこの要約では未確定、GitHub releases APIは空配列。 / HF公式配布はtencent/WeVisOne-VL-2B（更新2026-10-23、22 likes、F32 2,441,744,384 params、model.safetensors 9,767,053,328 bytes）とtencent/WeVisOne-VL-4B（更新2026-10-23、18 likes、F32 4,830,581,248 params、19,322,413,240 bytes）。各mask_decoder.ptは別途約38MB。APIのdownloads値は0。 / Tencent-authored repo/model cardはApache-2.0。Qwen3-VL等のthird-party componentsは個別の元ライセンスが残るため、全依存の商用条件は要確認。 / モデルカードはTransformers推論例を示すが、実際のRAM/VRAM最小値、CPU対応速度、量子化配布は未確認。demoのA10GはSpace側割当で独立性能評価ではない。 / 現行family検索は2B/4Bの2件。より新しい後継IDは保存資料から断定できない。

次: 2GB以下の量子化公式checkpoint、または費用ゼロで利用できるGPU実行先が追加公開されたか確認する。

## What Generation Knows: Unlocking Fine-Grained Understanding in Unified Multimodal Models
https://arxiv.org/abs/2610.23987

判定草案: defer / reading
画像理解の具体的な研究価値はある。生成経路の視覚表現を圧縮して理解経路へ注入し、細粒度視覚理解を改善するGAFUで、論文は同一データのSFT比較で複数ベンチマーク改善を報告する。一方、これは基盤モデルの後学習手法で、複数バックボーン/データの準備と学習を要するため、現在のCPU・予算制約内ですぐ試す道筋は確認できない。

確認状況: 論文は2026-10-14投稿のv1（保存資料記載）。より新しい後継IDは未確認。 / GitHubは2026-10-20 push、28 stars・0 forks。READMEにコードと学習設定、導入説明あり。 / GitHub APIのlicenseはnull。商用利用条件は未確認。 / READMEは複数の上流バックボーン別環境とモデル重みのダウンロードを示す。容量・VRAM・学習時間の実測は未確認。 / 論文の改善率は著者報告値で独立実測ではない。

次: READMEの最小バックボーン構成と推論/学習別のVRAM要件を確認し、既存チェックポイントで学習なし評価が可能か調べる。

## The LLM-Attacker: A Unified Red-Team Framework for Privacy Evaluation of Split and Distributed LLM Systems
https://arxiv.org/abs/2610.22722

判定草案: reject / reading
本候補は分割/分散LLMで中間表現から入力を復元する攻撃を比較評価するレッドチーム枠組みであり、対象の関心経路（分類/要約、抽出、画像認識、小型CPUモデル、AI駆動開発の評価・構築）に対応する一般的な開発・推論上の価値が一次資料から確認できない。プライバシー攻撃の対象システムを扱う予定も示されていないため、今回の候補としては不要。

確認状況: 論文は保存資料では2026年の提出記録。後継論文IDは未確認。 / GitHubは2026-10-19 push、1 star・0 forks、MIT license。READMEで導入/CLIを説明。 / このsuiteの研究報告に対応する新規実験はREADME上でなく、今回の更新はbranding/metadata中心。 / 依存資源と実行時間、現在有効な比較攻撃の網羅性は未確認。

次: None

## EvalResearchBench: Can AI Agents Build Their Own Feedback for Recursive Self-Improvement?
https://arxiv.org/abs/2610.04184

判定草案: defer / reading
AI駆動開発の評価設計に直接関係する。EvalResearchBenchはエージェントが課題を選び、採点器を実装し、パイロット後に評価器を固定する能力を測る。論文は13候補モデル等で評価するが、実際の実験にはcandidate APIと時間/API予算が必要で、現金予算0では再現可能な試行がすぐ成立するか不明。

確認状況: 論文は保存資料では2026-10-02頃の投稿資料、後継IDは未確認。 / GitHubは2026-10-06 push・2026-10-22更新、3 stars・0 forks。READMEにはmock runとLinux導入手順あり。 / GitHub API licenseはnull。商用利用条件は未確認。 / Claude Code native binaryまたはNode/Codex harness、Linux namespaces・Bubblewrap・Python 3.12等の制約がある。実行資源/所要時間は未測定。 / 論文の評価結果は著者報告。実験に必要なAPI利用枠/費用は不明。

次: mock fixture実験だけで研究者→開発フィードバック→固定評価器までの流れをAPI費用なしに確認できるか調べる。

## Diffusion Language Models Change Speed Across Tasks and Languages
https://arxiv.org/abs/2610.25835

判定草案: defer / reading
NLPの推論効率に関する判断材料として、タスクや出力の新規性により拡散LMの計算負荷が変わり、数学回答ではデコード負荷が正答性の手掛かりになり得るという仮説を提示する。著者は特定の2モデルをGPUで評価しており、今回のCPU小型モデルで直接再現できるか、公開実装/重みがあるかは保存資料から確認できず、すぐの実験価値は限定的。

確認状況: arXivの保存資料は2026-10-19投稿v1。後継IDは未確認。 / 関連するGitHub repoや星数/活動日は入力資料に含まれず未確認。 / 論文ライセンスはCC BY-NC-SA 4.0。商用利用適合性は未確認。 / 実験はH100上のDiffusionGemma-26B-A4B-itとDreamReasoner-8Bで実施。CPU 20分/RAM 8GBで動くか、公開重み容量、推論依存関係は未確認。 / 最大4.2ポイント改善は著者実験結果で独立実測ではない。

次: 公開されている推論コード/重みの有無と、既存ログからpasses/tokenを取得できるかを確認し、追加推論なしの検証可能性を調べる。

## Different Tasks, Different Timesteps: Temporal Task-Aware Quantization for Diffusion Language Models
https://arxiv.org/abs/2610.31198

判定草案: defer / reading
T²Quantは拡散言語モデルのdenoising timestepごとにactivation precisionを配分し、論文著者は同一bit budgetで最大6.7ポイント改善を報告。量子化そのものは関心に接するが、実装対象はLLaDA-8B-Instructで、READMEはfull-precisionとquantized両方を保持する40GB以上CUDA GPU、実行4–5時間を要求。現金0、GPUなし、CPU20分/候補の今回の条件では検証不能で、CPU小型モデル用の軽い量子化とも異なる。資源条件を満たす機会まで保留。

確認状況: 論文は2026-10-22公開。論文値は著者報告で独立再現なし。GitHub repoは3 stars/0 forks、2026-10-23更新、MIT。release一覧は空配列。 / READMEの例はLLaDA-8B-Instruct、4-bit GPTQ weightsにactivation quantizationを組み合わせる。学習ではないが、40GB CUDAと4–5時間を要する記載。 / 追加の重み配布は確認できず、LLaDA-8B-Instructを初回取得する実サイズ・利用条件、および他の最新後継モデルIDは保存資料では未確認。 / 推論の実行時間・必要RAM/VRAMがREADME記載を超えるか、CPU-only経路があるかは未検証。

次: 40GB以上のCUDA環境なしで動く小型DLM対応または既存の比較済みベンチ結果が公開されたか確認する。

## Agent Mosaic: Recognizing Confidentiality Leaks in Agentic LLM Systems
https://arxiv.org/abs/2610.30418

判定草案: defer / reading
AI駆動開発のエージェントが複数文書から機微な結論を合成して漏らすリスクを示しており、機密情報を扱うエージェント設計には具体的な注意点となる。ただし30件の合成例と限定された2つのライブharnessが中心で、論文は別文書・導入環境への一般化に限界を明記する。確認できるのは論文のみで、試せる実装や導入手順がないため、今すぐ試す候補ではなく保留する。

確認状況: 公表更新はarXiv v1の2026-10-21提出。保存資料にrepo情報がなく、stars・活動状況・実装の有無は未確認。 / 論文のライセンス表示はCC BY-NC-ND 4.0。商用利用を許すコードライセンスは確認できず、実装・データのライセンスは未確認。 / 導入手順、必要資源、再現用の公開artifactは保存資料から確認できない。論文は3モデルの制御シミュレーションとPi/Hermesのライブharnessを報告。 / 限界として、合成30例、固定のqueryとdistractor構成、最終回答のみ評価、提供モデルaliasの利用を論文に記載。 / 保存資料以降の最新版確認と最新後継の実在IDは未確認.

次: 著者が公開データ・再現コードを出した場合、機密結論を複数文書に分散した小規模テストで分類ヘッダーと集約ルールの漏えい低減を確認する。

## RIDAR: Safeguarding Research Integrity in Autonomous Research Agents via Automated Detection and Runtime Intervention
https://arxiv.org/abs/2610.31372

判定草案: defer / reading
RIDARはAI駆動開発に最も近い候補。completed agent trajectoriesのpost-hoc検査に加えてruntime feedbackを行い、READMEのadaptersにはClaude Code、Codex CLI、OpenCodeがある。MITコードと公開trajectory datasetは具体的な評価価値を持つ。一方、quick startはAnalyzer/Verifier API credentialsを要求し、今回の現金0・認証操作禁止条件では無償で動くか判断できず、READMEにある権限緩和command例も実行対象にできない。まず既存trajectoryへのローカル/無課金post-hoc経路の有無を隔離環境で確認する段階。

確認状況: 論文は2026-10-22公開、著者報告はRIV-Gold 60 trajectoriesで85.2 F1、runtimeでtask終了時に残る違反を36.8%減。独立再現なし。 / GitHub repoはMIT、2026-10-23 push/2026-10-24更新、5 stars/0 forks、release一覧は空配列。 / README quick startはAnalyzer/Verifier用API credentialsを要求。無課金ローカルモデル対応、各APIの従量費用、8GB RAM/CPU時間は不明。README記載の--dangerously-skip-permissions例は実行しない。 / HF dataset THU-KEG/RIDARはMIT、2026-10-23更新、24 downloads/0 likes。RIV-Gold 60 human-annotated、RIV-Wild 300 trajectories、RIV-Live 60 runtime/control paired trajectories。 / 成果はautonomous research agentの研究integrity用で、通常のcoding agent error検知への外挿は未検証。学習済みモデル/重み配布なし。 / 2026-10-24以降に公開された後継IDは保存資料だけでは確認できない。

次: 既存のCodex等trajectoryを、認証情報や権限緩和なし・無課金でpost-hoc検査できる実装経路があるか、隔離確認する。

## Deploying a BERT-based Query-Title Relevance Classifier in a Production System: a View from the Trenches
https://arxiv.org/abs/2108.10197v1

判定草案: defer / reading
CPU向けの小型NLP分類器として関心に合い、論文はeBayの検索QTRでBertBiLSTMを報告している。ただし用途はクエリと商品タイトルの二値関連度判定で、汎用分類・NERの実装そのものではない。報告値は著者の生産システム上の値で、モデルは独自1B件タイトルで事前学習され、論文記載のin-house推論toolkitを用いる。今回の保存資料には取得可能な重みや再現手順がなく、20分CPU枠で試せる状態か確認できないため、公開artifactとライセンスを確かめるまで保留。

確認状況: arXiv資料ではv1提出日が2021-08-23。これ以後の更新・後継IDは保存資料だけでは確認できない。 / stars・最近の活動、導入実績、公開重み/コードとその商用ライセンスは未確認。論文自体はCC BY 4.0表記だが、モデル/データの権利を示さない。 / 必要RAM・重みサイズ・CPU環境、独自e-commerceデータなしでの再現可能性は未確認。論文は1B商品タイトルでeBERTを事前学習し、764k件のQTR訓練データも記載する。

次: 著者の公開重み・推論コードが存在し、商用利用可能な条件でCPU上の小さなQTR例を動かせるか確認する。

## MOCHA: Multi-modal Objects-aware Cross-arcHitecture Alignment
https://arxiv.org/abs/2509.14001v5

判定草案: defer / reading
few-shotの個人向け物体検出という軽量画像認識の独立経路に合致し、論文は4ベンチマークでの改善を著者値として報告する。一方でMOCHAの核は凍結VLMから軽量detectorへの特徴蒸留で、事前学習・蒸留・few-shot適応の複数段階を要する。保存資料にはコード本文や実行可能な重み・依存手順がなく、現金0・CPUのみ・20分という今回の条件で即試行できる根拠がない。論文のCC BY-NC-SA 4.0表記は商用利用にも追加確認を要するため保留。

確認状況: 保存資料のarXivページはv5、最終改訂日2026-06-22。以後の更新・最新後継IDは保存資料だけでは断定できない。 / stars・最近のrepo活動、第三者導入、公開weightsの配布条件と各依存モデルの商用利用可否は未確認。論文表示のCC BY-NC-SA 4.0は非商用・継承条件付き。 / GPU/CPU別の学習・推論時間、必要RAM/VRAM、重み容量、現金費用は資料上で確認できていない。提案法はstudent事前学習、VLM特徴蒸留、few-shot適応を行う。

次: SamsungLabs/MOCHAの公開repoに対応する商用可の依存物とCPUで可能な最小inference/demoがあるか、隔離前提で確認する。

## Compress What You See, Not What You Say: Anchored Context Distillation for Latent-Observation Software Engineering Agents
https://arxiv.org/abs/2609.31430v1

判定草案: defer / reading
AI駆動開発に直接関係し、coding agentの過去のtool observationをsoft token化し、直近内容とagentのactionをテキストに残す設計は具体的価値がある。論文の著者報告では通常条件の成功率は非圧縮baseより低い一方、32K context制限下では改善し、単一GPU同時servingでthroughput増を報告するため、利益はcontext制限やserving負荷に依存する。ACDはモデル適応を伴い、35,140軌跡を使った学習を記載する。今のCPU・RAM・時間・学習禁止条件では評価に進めず、GPUを含む再現要件が確認できるまで保留。

確認状況: 保存資料のarXivページはv1提出日2026-09-25。以後の更新・最新後継IDは保存資料だけでは断定できない。 / 公開repo/重み、stars・最近の活動、第三者導入、およびモデル/データ/コードの商用ライセンスは未確認。論文はarXiv perpetual non-exclusive license表記。 / 学習・推論に必要なGPU種類/時間、RAM/VRAM、重み容量、現金費用は不明。論文には35,140軌跡・204,800-token sequenceでの1 epoch学習が記載される。

次: 隔離環境とGPU予算が確保できる場合に、公開実装でSWE-bench小subsetをK=3/非圧縮で比較し、context削減と解決率差を確認する。
