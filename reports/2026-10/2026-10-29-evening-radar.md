# 2026-10-29 夕方 Research Repro Radar

## 結論

**THX-01は日本語NLPの追加検証B、明示ゲームルールの置換C。一般採用Aや「大きな効果」は認定しない。** 32件の固定合成入力で実比較を完了。第三者評価と別作者の派生実装という独立2信号を確認でき、詳細報告の区分は「深掘り価値」。両信号は日本語品質の独立再現2件を意味しない。

今回の新規収集は **1,939一意候補**、一次資料読解24候補（最終review_pool20を全件含む）、実モデル試験1モデル32入力。前回9,481件の保留を全件維持し、終了後の未完了は **9,514件**。THX-01だけを完了記録した。朝のLayaや日本語モデルを再実行していない。

## 概要・新しさ

[THX-01公式モデル](https://huggingface.co/doofz/THX-01)はmmBERT-base由来322Mの非自己回帰型判定器。Choice/Score/Noulに加え、原文中の数値・抜粋・引用箇所を扱う。Apache-2.0で商用利用可能（表示等の条件に従う）。PyPI `thx01==1.0.0`、モデルrevision `b323afcef149f9288a9d0daef4badd67b9b87c61`を固定。公開紹介10/10、モデル更新10/16。重み643,835,524 bytes、tokenizer/config込み678,203,656 bytes。GitHub starsは該当公式repo未確認、HF likes/downloadsは保存済みmetadata参照。スターや著者記事を独立検証として数えていない。

[第三者のTyped Decisions評価PR](https://huggingface.co/doofz/THX-01/discussions/1)と[独立作者によるlaya-indic実装報告](https://huggingface.co/doofz/THX-01/discussions/2)を原文確認した。著者のGPU約10msとJev APIの速度差は実行条件が違い、CPUでの優位を示さない。抽出性能・確率校正・日本語一般化も今回未測定。

## 方法

10/28作成済みの同一input/gold/scorerを、新しいTHX-01にのみ適用。NLPは12状況×clean/noisyの24表現、ゲームは4状態×構造化/文章の8表現、計16独立状況32表現の診断用合成データ。入力・正解・採点器のSHAはfixture-manifest。正解を候補コンテナに渡していない。入力順や正解を結果に合わせて変更していない。弱いNLPルールと明示ゲームルールは同じホストで比較実行した。

AMD EPYC 9V74、CPU quota2、Torch2.7.1+cpu、Transformers5.17.0、公式thx01のSDPA、float32、2threads。Dockerはnonroot65534、network none、read-only、cap-drop ALL、no-new-privileges、8GB、専用tmpfs。公開モデル・wheel・合成入力だけをtarで渡し、host credentialや個人ファイルを見せない。外部コードをhostへimportしていない。サーバ/APIは使わず、現金0、GPUなし、修復0。

## 結果（完全再現ではなく小規模比較）

| 系統 | 今回THX-01 | 今回ルール | 保存済み日本語モデル | 保存済みLaya multilingual |
|---|---:|---:|---:|---:|
| NLP | 22/24 | 14/24 | 23/24 | 12/24 |
| ゲーム行動 | 2/8 | 8/8 | 5/8 | 4/8 |

| THX-01内訳 | 正解 | Macro F1 | 平均秒/件 |
|---|---:|---:|---:|
| nlp-clean | 12/12 | 1.000 | 0.1940 |
| nlp-noisy | 10/12 | 0.815 | 0.2458 |
| game-prose | 1/4 | 0.133 | 0.1918 |
| game-structured | 1/4 | 0.133 | 0.1870 |

NLP平均 **0.2199秒**、約272.9件/分。同じ短い入力・逐次実行なら1万件で約36.7分という単純外挿。ピークRSS **2.27 GiB**。セットアップから終了45.8秒。load 9.17秒、import 2.09秒。model+wheels+fixtureの入力総量908,107,000 bytes、別途Python slimイメージと公開資料を取得、保守的に累積1.2GB未満。

## 比較の限界・問題点

弱いルール14/24に対する22/24の差は、この小標本の観測であり品質相対5%という閾値だけで一般的な改善を認定しない。保存済み日本語モデルより1問少ない。日本語モデルはIntel8573C/eager、LayaはAMD/SDPAで測定しており、異なる実行・モデル間の速度比を因果効果としない。今回のゲームでは選択肢を返せてもルールを守れず、単純な条件分岐の置換理由はない。

雑な大量データの短い分類は候補だが、長文切詰め、入力分布、抽出精度、ラベル数、信頼度閾値を別途確認する必要がある。大規模バッチのthroughputは未測定。クラウドCPUの課金額・API費用優位は現在一次価格を調べていないため未判定。CPU時間とwall timeを混同せず、上記1万件換算はwall時間。GPU、Kaggle、追加認証は使用していない。

## 調査範囲と後継確認

HF trending全50件、新着50件、10クエリ各API返却120件を保存し、HFだけで1,238一意。MDPI、Crossrefを補完し、GitHub公式OpenDeciderと関連モデル群を追加。40件で打ち切っていない。API返却上限・今回の時間配分によりWeb全体や各検索バックエンドの全件網羅ではない。OpenReview5要求は403で本文確認不可、制限迂回なし。収集レスポンス、URL、取得時刻、hashはledgerに保存。

全1,939件を採点し、既存carryも投入。最終rank input9,592、ranked8,152、review_pool20。これらは新規取得数や実験数ではない。ランキング対象外・期限超過でも既存未完了候補を完了扱いにせず、9,481件と最初のcarry日を保持した。鮮度調整はprofile通りで無効化していない。

OpenDeciderのnano/ONNX/small/TD/MLX/GGUF/medium/largeの現行ID・revisionを確認。ONNXは新しい別モデルではなくnanoの配布形式。英語中心の適用範囲と実CPU品質は未確認。[laya-indic](https://huggingface.co/arunp0/laya-indic)はv2、カードが指定する後継は[Yama v3](https://huggingface.co/arunp0/yama-indic)でXLM-R largeへ変更。Yama重み1,172,856,580 bytesを追加すると累積2GB枠超過のため未取得。GPUへ迂回しない。OpenDecider等の未実行候補は完了扱いにしない。

## 未検証ダイジェスト（最大15件）

1. [LiquidAI/d1-omni-600M](https://huggingface.co/LiquidAI/d1-omni-600M) — 多言語テキスト・画像・音声を一回の推論で分類する587Mモデルで、用途は合う。readme上はTransformersの追加依存とremote codeが必要、ライセンスは独自lfm1.0、速度値なし。CPU/RAM適合と商用条件は未確認。 保留: 未実行。今回のモデル比較枠はTHX-01に使用。
2. [Qwen2.5-Coder-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct) — コード生成用途は開発支援に関連しApache-2.0だが、1.5Bの生成モデルは小型分類/抽出の直接用途から外れ、30分枠内のCPU評価も不確実。CPU速度・RAM・商用利用条件以外に、既存候補より有効か未確認。 保留: 未実行。今回のモデル比較枠はTHX-01に使用。
3. [A Survey on Inference Optimization Techniques for Mixture of Experts   Models](https://arxiv.org/abs/2412.14219) — MoE推論最適化のサーベイで学習・背景把握には役立つが、検索可能な新規実装やモデルそのものではない。実務の新規発見範囲を単独で増やさず、CPU小型モデルへの直接適用も未確認。 保留: 未実行。今回のモデル比較枠はTHX-01に使用。
4. [AfriNLLB: Efficient Translation Models for African Languages](https://arxiv.org/abs/2602.09373) — アフリカ諸語翻訳モデルの研究で、対象言語が明示された翻訳案件なら関連するが、一般分類/抽出との一致は弱い。効率性の主張だけではCPU/RAM適合や精度優位を確認できない。 保留: 未実行。今回のモデル比較枠はTHX-01に使用。
5. [Deploying a BERT-based Query-Title Relevance Classifier in a Production System: a View from the Trenches](https://arxiv.org/abs/2108.10197v1) — 本番BERTクエリ・タイトル関連度分類の実例で実務性はあるが、対象の小型CPU分類/抽出へ転用できるか、実装を現在の環境で再現できるかは未確認。導入可能な成果物と資源量を確認できるまで保留。 保留: 未実行。今回のモデル比較枠はTHX-01に使用。
6. [Gated Relational Alignment via Confidence-based Distillation for Efficient VLMs](https://arxiv.org/abs/2601.22709) — 効率的VLM向け蒸留の研究で軽量視覚と関連するが、実配布モデル・CPU実行性は資料上確証なし。比較対象と実測を確認する価値はあるものの、現時点で導入試験の根拠は不足。 保留: 未実行。今回のモデル比較枠はTHX-01に使用。
7. [Understanding and Improving Knowledge Distillation for   Quantization-Aware Training of Large Transformer Encoders](https://arxiv.org/abs/2211.11014) — Transformer encoderの量子化対応学習を扱いCPU小型化の参考になるが、論文の手法を対象モデル/ツールへ適用できるか、現行の再現可能な実装があるかは未確認。導入可能性と比較実測を確認できるまで保留。 保留: 未実行。今回のモデル比較枠はTHX-01に使用。
8. [I-ViT: Integer-only Quantization for Efficient Vision Transformer   Inference](https://arxiv.org/abs/2207.01405) — I-ViTは整数のみのViT推論を扱い軽量視覚に合うが、視覚タスク専用で現環境のCPU対応・導入容易性は未確認。性能比較の記述を確認してから優先度を判断する。 保留: 未実行。今回のモデル比較枠はTHX-01に使用。
9. [Lightweight Vision Transformer Compression for On-Device Plant Disease Detection in Resource-Constrained Agricultural Field Conditions](https://arxiv.org/abs/2609.05334v1) — 制約環境向け植物病害検出ViT圧縮で軽量視覚に一致するが、用途が特化し、実装・ライセンス・CPU測定は未確認。比較可能な実測確認後に試験判断。 保留: 未実行。今回のモデル比較枠はTHX-01に使用。
10. [ThinkBit: Search Codewords, Recalibrate Scales, Select by Reasoning](https://arxiv.org/abs/2610.34886) — 量子化コード語とスケール再調整に関する新しい論文だが、対象モデル規模・CPU小型モデルでの適用性・公開実装は資料から未確認。量子化に関心は合うものの試験根拠が足りない。 保留: 未実行。今回のモデル比較枠はTHX-01に使用。
11. [Yama Indic v3 successor to laya-indic](https://huggingface.co/arunp0/yama-indic) — 多言語分類には有望だが、指示の累積DL上限2 GBに対し重みだけで超過するため今回は保留。GPUへ切り替えて回避しない。Apache-2.0でも資源制約は解消しない。 保留: 累積DL2GB枠超過のため未取得。
12. [AnkitAI/TinyJev-0.6B](https://huggingface.co/AnkitAI/TinyJev-0.6B) — Qwen3 0.6Bを基盤とする英語の型付き判定モデルで関心に合い、MIT。指定の追加ダウンロード承認待ちのため、取得・導入・完了判定を進めず保留する。 保留: TinyJev既存の追加DL承認待ち。再実行なし。
13. [MOCHA: Multi-modal Objects-aware Cross-arcHitecture Alignment](https://arxiv.org/abs/2509.14001v5) — MOCHAはマルチモーダル物体認識と異種アーキテクチャ整合の研究で視覚に関連するが、CPU向け配布モデルか、実タスクに有用かは未確認。 保留: 未実行。今回のモデル比較枠はTHX-01に使用。
14. [Task-Specific Zero-shot Quantization-Aware Training for Object Detection](https://arxiv.org/abs/2507.16782) — 物体検出向けタスク特化ゼロショット量子化学習で視覚圧縮の関心には合うが、モデル導入やCPU実行の実用性は未確認。 保留: 未実行。今回のモデル比較枠はTHX-01に使用。
15. [GoiBERT: Compact Vietnamese Language Models via Two-Stage Knowledge Distillation](https://arxiv.org/abs/2610.30067) — ベトナム語小型言語モデルの二段蒸留で、対象言語案件なら関連するが用途が限定的。論文記述だけではCPU要件・公開物・比較性能は確認できない。 保留: 未実行。今回のモデル比較枠はTHX-01に使用。

## 採用・次の確認

THX-01はB: 実際の日本語文書から独立goldを先に固定し、分類と原文数値/抜粋抽出を分けて比較する価値がある。明示ゲーム行動ルール置換はC。今回のfixtureを調整して成績を埋め合わせる追加試験は行っていない。

TinyJev原48件0/48・追加DL承認待ち、Moli/Lightpanda追加修復待ち、SKU別件、PR19mLateOn停止を維持。追加承認質問は重複送信していない。朝タスク、SMTP、cron、merge/deployへの変更なし。実用機能の変更ではなく研究記録なのでPR作成なし。

## 継続・検証証拠

`RADAR_LLM=native`を最初から選択し、実host subagent probe→request ID/hash/Schema/期待値のvalidate exit0後に探索開始。指定modelはgpt-6-luna、実サービスIDはunknown。全native要求はprivate一時領域、公開台帳はtask ID/hash/検証結果のみ。読解判定の誤引用と曖昧なゲーム置換表現は各1回の応答修復で訂正し、測定値は変えていない。

正規復元元commit674113afd1576a6244e9103387401cd2fc0a2762、Skill upstream fe6925cd75838a9b52130f284cb3b931bfaf36db。Skillローカル`state`をrank/record_run両方で使用し、seen/queue/mail/freshnessを正規evening-stateへ戻した。mailは不変。remote永続化とexact-SHA CIの結果は最終応答で報告する。

- 再現コード: `experiments/research-repro-radar/thx-comparison-20261029/`
- 入力・正解・全予測・採点・時間/RSS・隔離条件: `benchmarks/research-repro-radar/2026-10-29/`
- ソース・全候補・読解・台帳・状態検算: `experiments/research-repro-radar/runs/2026-10-29-evening/`

ローカルlab必須チェックは151件実行、既存robots-parser未導入による1失敗、15skip。候補実行はexit0。新規発見した未実行のOpenDecider/laya-indic計7variantは字句スコア除外で消さず保留へ維持。
