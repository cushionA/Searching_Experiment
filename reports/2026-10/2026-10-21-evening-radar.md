# 2026-10-21 夕方・独立研究レーダー

## 結論

**Harness²をA（すぐ試す価値）の深掘り候補**として報告する。候補コード実行・推論・比較実測は0件。これは読解確認であり再現成功ではない。新しい根拠のないNCM等の既報詳細は繰り返さない。

## 概要・方法

指定開始09:01 UTC。環境起動後の最初の時刻記録は09:02:31 UTC。終了上限は指定開始から09:31 UTCに固定した。上流最新版55ceed634506c5942d6a8e84c71d7e80acc103ffの指定8ファイル、checkoutのAGENTS.mdと関連Skillを確認。RADAR_LLM=nativeを最初から明示し、gpt-6-lunaをホストspawnへ指定。サービス実モデルIDはunknown。probeの要求ID・入力hash・schema・期待値の検証に成功後、収集を開始した。

HF trending全50件、新着50件、NLP/画像/小型モデル/AI開発の10検索とMDPI/Crossref/GitHubを収集。2,042件を重複排除し、繰り越し込み6,214入力を全件採点。形式不正0、ranked 5,303、review_pool20件＋新着10件を一次読解。40件で打ち切っていない。OpenReview検索5件は403、arXiv検索API5件は429。候補のarXiv本文は取得できた。各API応答と検索範囲の上限があり、公開研究全体の網羅は主張しない。

## 深掘り詳細：A — Harness²

一次資料：[論文](https://arxiv.org/abs/2610.14224)、[公式コード](https://github.com/google-research/harness2)、[LICENSE](https://github.com/google-research/harness2/blob/main/LICENSE)。公開2026-10-09。取得時16,338 stars、1,511 forks、最終code push 10/14。mainは1cfe254e8d25b15f37048c3730134686abe60335。release一覧と最新mainを確認したが、別の正式後継版は未確認。

**目的・新規性・用途**：モデル重みを固定し、指示・ツール・メモリ・スキル等をPropose–Probe–Composeで対照的に改善する。タスク内の改良と、次タスクへ引き継ぐ改善履歴を分ける。AI開発で反復失敗を減らす設計仮説として価値がある。大量の低単価データを処理する本番では、探索コストを毎件払わず、改善済み構成を複数件へ使い回せるかが焦点。NLP品質や処理費用の優位は未確認。

**今の話題性**：[HF trending](https://huggingface.co/papers/2610.14224)1位と、独立した利用者による[issue #121](https://github.com/google-research/harness2/issues/121)の実装調査を確認。著者論文と著者repoを独立2信号に数えていない。第三者報告は話題・検証活動の証拠であり、性能の第三者再現成功ではない。

**結果・比較**：著者は単一改善ステップでWorkBuddy最大11.5ポイント、JobBench最大4.5ポイント、LAB部分合格率最大11.8ポイント改善と報告。当方の実測はない。品質相対5%以上、または品質低下2%以内かつ資源30%以上削減の「大きな効果」は認定しない。

**問題・制約**：issue #121は時間切れ・クラッシュ後でも成果物があればJobBench rolloutを完了扱いし、終了状態が軌跡へ残らないと指摘。[PR #122](https://github.com/google-research/harness2/pull/122)は確認時open・未マージで、提案も終了状態を可視化する範囲である。評価値の信頼性・再実行条件を確認する必要がある。READMEはモデル用Codex executorのsandbox無効化と無制限Webアクセスを明記しており、その既定経路を起動していない。通常CI保守を研究目的にはしない。

**導入・資源・商用**：Linux/Python3.12/uv。認証不要の決定論的ローカルdemoはあるが、AIモデルの改善実測とは別。モデルベンチはNode20+、Google Cloud CLI、対象によりDocker等とモデル課金が必要。repoコードはApache-2.0、第三者コード・データは個別条件。demoのCPU時間・ピークRAM・DL量は未測定で、8GB/20分/2GB適合を認定していない。

**導入判断・次**：Aは設計と無料demoを吟味する価値であり、本番導入や権限緩和の許可ではない。既存の隔離停止条件が解決した場合に限り、認証不要demoの生成物・時間・RAMを確認し、その後の固定タスク比較を検討する。API/GKE/AWSの最新価格と同条件実測がないため費用優位や損益分岐を算出しない。

## 未検証ダイジェスト

- **A — [TokenGate: Adapting LLMs for Fast and Memory-Efficient Prefill with Learned Token Pruning](https://arxiv.org/abs/2610.26754)**：長文prefillを学習済みgateで削減。公開NIAH adapterを確認し、追加学習が必須という初回判断を更新。 保留・制約：adapter約114MBに加えて基盤Qwen3-1.7Bが必要。CUDA PyTorch前提でCPU/RAM/総DL未確認。
- **A — [Qwen2.5-Coder-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct)**：公式GGUF Q4_K_Mが1,117,320,768 bytes。小型コード生成・修正の比較対象。 保留・制約：CPU品質・速度・ピークRAM未測定。実在する後継Qwen3-Coder-Nextは大容量で同条件の代替ではない。
- **A — [Unsupervised Tool Evolution for Coding Agents in Data-Lake Analytics](https://arxiv.org/abs/2610.27327)**：TEALはデータレイクの調査・結合・清掃ツールを実行履歴から改善し、次の質問へ再利用。 保留・制約：公式コード・商用license・モデル費用・独立2信号未確認。
- **B — [EdgeKernelBench: Unified LLM-Driven Kernel Generation for Edge Backends](https://arxiv.org/abs/2610.24648)**：EdgeKernelBench/LEKAは異種エッジのコード生成で正しさと速度を別評価。CPU/LLVM経路も記載。 保留・制約：TVM 0.23、対象端末とClaude Code等が必要。CPU最小構成、全依存license、資源は未確認。
- **B — [MemT-1.0: Towards Memory-Native Evolution](https://arxiv.org/abs/2610.26825)**：MemT-1.0は固定サイズの持続メモリをagentic codingに利用。Nano/Flashの実在ID確認。 保留・制約：Nanoも36B、例は2GPU。repoソフトはPolyForm Noncommercial、重みcardのApache/MIT表示とは別。
- **A — [OpenGCUA: Toward a Fully Open Stack for Generalist Computer Use](https://arxiv.org/abs/2610.22200)**：OpenGCUAはGUI操作とプログラム操作を統一し、OSWorld-v2の軌跡を公開。 保留・制約：8モデル比較は著者値。導入・費用・CPU/RAM・独立2信号未確認。
- **B — [DiscoveryRSI: Recursively Learning to Discover Valuable Hypotheses](https://arxiv.org/abs/2610.23839)**：DiscoveryRSIは仮説・反証条件・改善履歴を研究オブジェクトとして保存。 保留・制約：27Bと生命科学が中心。一般分析への移植、重みlicense、RAM/DL未確認。朝のDiscovery Curvesとは別論文。
- **C — [Good Judgment, Bad Advice: Decision-Model Controllers Raise Agent Cost Without Improving Success](https://arxiv.org/abs/2610.24649)**：Good Judgment, Bad Adviceは停止判断モデルの追加を対照実験で評価。 保留・制約：著者の21課題315試行では成功率同じで費用19%増。今回の同方式導入は見送り、一般化は未確認。
- **B — [Gated Relational Alignment via Confidence-based Distillation for Efficient VLMs](https://arxiv.org/abs/2601.22709)**：GRACEはVLM量子化。今回QAT用W4G128と真のpacked AWQを区別。 保留・制約：W4G128のmodel.safetensorsは4,877,470,336 bytes。名前だけでINT4省メモリと認定しない。AWQ実行条件は未確認。
- **C — [Bekko Embedding: Parameter-Efficient Multilingual Retrieval with Ultra-Compact Encoders](https://arxiv.org/abs/2607.25180)**：Bekkoのa25m/a8mとONNX配布を確認。 保留・制約：確認できる主価値は意味検索順位・速度。網羅性補完・前処理・新サイト発見への具体的価値未確認のため今回用途ではC。NLP分類抽出へ流用可能とは断定しない。

## 検証・state・保存

native初回30件と追加資料による6件の判定は全て180秒以内・schema適合。初回の引用/URL不一致5箇所は未採用とし、監査表へ残した。追加6件の引用は全件照合済み。native経路38テスト成功。JSON成功を候補推論と数えていない。モデル同系列の実在ID・個別配布サイズはmodel-followup.json/model-details.jsonへ保存。TokenGateの移転先token-gate-llms/TokenGateも確認した。

既存の資格情報隔離問題を保留のまま維持し、候補コード・重みDL・GPU・学習は0。新規認証、CLI fallback、権限緩和、SMTP、朝/weekly/cron、別担当SKU、Moli/Lightpanda、PR19を変更していない。現金0円。既存の承認質問を繰り返していない。

正規remote b9682b985b1c708d5680937fdbda1c27edea53b5から未完了6,136件を復元。Skill直下でrank_candidates.pyとrecord_run.pyを--state-dir state付きで実行。seen/queue/mail/freshnessを夕方専用へ同期。Harness²の読解詳細はseen/freshnessへ記録するが、未実行のためqueueには残す。明確に同方式導入を見送ったGood Judgment, Bad Adviceのみcompleted_ids。ランキング除外・digest・未実行・不一致だけでは完了にしない。記録後未完了6,212件、mail不変、adaptive_freshness維持。

成果は本run、benchmarks/research-repro-radar/2026-10-21-evening.json、当報告。実用機能完成ではないためdraft PRなし。独立ブランチへpushし、remote一致と当該CIの結果は親への最終報告に添える。

**収集2,042／一次読解30（review_pool20）／実検証0／記録後未完了6,212。**
