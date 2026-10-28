# 2026-10-28 夕方：日本語の小型判断モデルを実際に比較

## 結論・概要

**argos1111/modernbert-ja-310m-jev は日本語NLPでB（条件付き）、今回の明示ゲーム規則の置換にはC。** 新規の固定合成入力32件をCPUで推論した。開催状況の分類は23/24、単純キーワード規則は14/24。一方、ゲームの扉・鍵の規則は5/8、決定的ルールは8/8だった。文章の否定・訂正を扱う部品として追加確認の価値はあるが、ルールが明確な処理を置き換える根拠は得られなかった。

これは**比較済みの小規模診断**で、完全再現・一般的優位・大きな効果の証明ではない。NLPは12場面のclean/noisy、ゲームは4状態のJSON/自然文であり、32独立課題とは数えない。独立した追加データも強い対照モデルも無いため、Skillの`recommended`は空。詳細結果を隠す意味ではなく、推薦基準を満たしたという記録を作らないためである。

## 実行経路・引継ぎ

最新Skill `fe6925cd75838a9b52130f284cb3b931bfaf36db` を確認。開始時から`RADAR_LLM=native`。prepare→ホストの指定`gpt-6-luna`→終了確認→ID/入力SHA/Schema検証のprobe成功後に探索した。サービス内部の実モデルIDはunknown。候補判断も同じ経路で検証した。判断の自己申告に「実行前なのにsmoke」と引用元不一致が各1件あり、それぞれ1回の修正後に親が測定レベル・引用を照合した。CLI、APIキー追加、認証移設はしていない。

正規remote `research/2026-10-12-evening-radar` の `398863ba6611ec4c2c502c9cd5533a0922528108`、pending9,467から復元。4JSONをSkill一時コピーの`state`直下へ置き、採点・記録とも`--state-dir state`。順位低下は完了にせず元carry日付を保持した。朝・週次・SMTP・cronは変更しない。TinyJev、Moli/Lightpanda、SKU、PR19の既存停止は維持。朝のLaya公開応答監査は既報として参照し、今回の実測へ混ぜていない。

## 対象・版・利用条件

[公式モデル](https://huggingface.co/argos1111/modernbert-ja-310m-jev)、固定revision `07cda23579443e7a33c0f474114279fa032340d6`。日本語の質問＋状況と選択肢をcross-encoderで採点する約310Mモデル。TypeSafe Jev本体の再現ではない。9/19公開・更新。今回の作者一覧では同名の新しい後継を確認できなかった。likes/downloadsは関心の参考に留め、品質や独立した議論2件の証明にはしない。

重み1,260,829,436 bytes、SHA256 `85ede5652889f5e4e114da5c6cdb923c31ae1f08cb0f5d0008a0631d6a4541d0`。APIのLFSハッシュと取得本体を一致確認。標準`ModernBertForSequenceClassification`、`auto_map`なし、`trust_remote_code=False`、safetensorsのみ。重みCC BY-SA4.0、base MIT、データCC BY-SA/CC BY/MITの出典・条件を保持する。商用禁止の表記とは区別するが、商用導入の承認は行わない。

CPU用Torch2.7.1+cpu、Transformers5.17.0、Python3.12。configの保存版5.17.0に合わせた。4.57.1のwheelも事前取得したが、候補実行には使わず、そのbytesも予算に含めた。すべてのwheel名・SHA・サイズはmanifestへ保存。候補・依存約1.513GB、Docker imageと資料を加えても2GB以内。重み・wheelはgitへ入れない。

## 方法

[実行コード](../../experiments/research-repro-radar/ja-comparison-20261028/)、[入力・正解・数値](../../benchmarks/research-repro-radar/2026-10-28/)、[収集・判断台帳](../../experiments/research-repro-radar/runs/2026-10-28-evening/)。入力と正解のSHAをモデル実行前に固定し、モデルコンテナへ正解を渡していない。TinyJevの48問を流用せず、public/syntheticのみ。

- NLP：明日の勉強会が開催・中止・未定かを3択。否定、昨日との混同、訂正、希望と決定の区別を含む12場面にHTML残骸・関連記事語を付け、clean/noisyを別集計。単純baselineは中止語→未定語→開催の優先規則で、強い最適化済み比較器ではない。
- ゲーム：扉が開いていれば進む、閉じていて鍵があれば鍵を使う、無ければ待つ、の明示規則。4状態×2表現。ゲームシミュレータや対戦成績ではない。
- 公式カードの`質問: …\n状況: …`と選択肢ペアを使用。各入力の3選択肢を1batch、max_length512、truncation only_first、float32/eager attention、Torch2 threads。出力を見て入力・正解・promptを修正していない。
- Intel Xeon Platinum8573C。Docker CPU quota2、RAM8GiB、network none、read-only、cap-drop ALL、no-new-privileges、非root。通常workspace、認証、個人ファイルのmountなし。専用入力をstdin tarで一時領域へ渡し、終了で破棄。baselineも同じimage・資源上限の隔離コンテナで測った。

## 実測

|区分|モデル正答|単純規則正答|モデルMacro F1|平均秒/入力|
|---|---:|---:|---:|---:|
|日本語NLP全体|23/24|14/24|0.9582|0.9323|
|NLP clean|12/12|10/12|1.0000|0.7445|
|NLP noisy|11/12|4/12|0.9153|1.1202|
|ゲーム全体|5/8|8/8|0.6222|1.2654|
|ゲーム自然文|3/4|4/4|0.7778|1.2057|
|ゲームJSON|2/4|4/4|0.4444|1.3251|

モデルload1.417秒、import4.562秒、32入力の推論32.502秒、プロセスCPU70.073秒、peak RSS1,516,984KiB（約1.45GiB）。修復後の隔離起動・install・推論全体89.804秒。冷開始の1件目も入力別平均に含む。継続サービスのp95や競合負荷は測っていない。

NLP平均約64.35件/分、同条件1万件で約2.59時間の**経過時間換算**。noisyだけなら約53.56件/分、3.11時間。2thread使用なので経過時間とvCPU消費時間を混同しない。GKE/AWSの単価やAPI価格を今回取得していないため、金額・APIより安いという結論は出さない。旧profile参考価格を現行価格として流用しない。

誤答は`nlp-7-noisy`：昨日の講演会と明日の勉強会、付加した関連記事語がある入力。ゲームでは扉が既に開いている状態を3件誤った。確率値は保存したが校正された正解確率としない。NLPの9正答差だけで一般的な相対5%改善を宣言せず、同場面の表現違いによる見かけの標本増大を避ける。

## 問題・修復・採用

初回pip installは256MiBの`/tmp`で`OSError: [Errno28] No space left on device`。許可済み1回の修復として、同じコンテナ構成の既存6GiB `/work`内へ`TMPDIR`を移した。RAM8GiBやダウンロード上限、権限・ネットワーク制限は変更せず、再試行はexit0。モデル推論は成功した1回のみ。コンテナ終了を確認。Kaggle・GPU・学習・支出は0。

日本語NLPはB：独立した日本語の実用的な固定集合と、別の小型モデルを対照に追加比較する価値がある。今回の明示ゲーム規則はC：既存ルールが正確で、置換利益を示さない。モデル全般のゲーム能力を否定する結論ではない。Layaは既報Bのままで、新たな推論は行っていない。

## 探索・未検証ダイジェスト

HF trending全50件・新着全50件・10検索を最初に取得。MDPI、Crossref、公式GitHubを補完し、収集一意2,046件。取得APIの返却分をすべて扱ったが、検索backendの返却上限や全世界の網羅を保証しない。OpenReview5要求は403で未取得、迂回なし。既存carry込み採点入力9,560、報告対象8,130、review_pool20件の一次資料を確認し、別枠の日本語モデル1件を比較した。予定件数を実測数とは扱わない。

以下はすべて今回のモデル推論未実施。著者の速度・品質主張は当方実測ではない。資源・ライセンス等の不明点はJSONレビューの`unconfirmed`へ明示した。

- [LiquidAI/d1-omni-600M](https://huggingface.co/LiquidAI/d1-omni-600M)：選択肢確率で分類・抽出を短出力で返す設計は、雑多な大量入力をAPIより低コストでさばく狙いに合う。587Mで試行枠にも収まりそう。 保留：LF-MIT系ライセンスの商用条件; 実際の重み容量とCPU推論の速度・精度
- [Qwen2.5-Coder-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct)：コード生成モデルとして開発関心には合うが、通常のスクレイピング分類・抽出への直接適合は未確認。1.5B重みが2GB枠に収まるかも不明。 保留：ダウンロード容量; 大量スクレイプ処理の費用対効果
- [AfriNLLB: Efficient Translation Models for African Languages](https://arxiv.org/abs/2602.09373)：アフリカ諸語向け軽量翻訳モデルは低資源言語の処理に有用な可能性があるが、用途が翻訳であり対象言語やCPU性能が未確認。 保留：対象スクレイピングの言語; 配布重みの容量とライセンス
- [Deploying a BERT-based Query-Title Relevance Classifier in a Production System: a View from the Trenches](https://arxiv.org/abs/2108.10197v1)：本番の軽量BERT分類器の実例で、大量データの分類に近い。ただし古い報告で、今使える成果物や現行環境への再現性が不明。 保留：公開コード・重みの有無; 現在のCPUでの処理量
- [Gated Relational Alignment via Confidence-based Distillation for Efficient VLMs](https://arxiv.org/abs/2601.22709)：VLMの蒸留と量子化は軽量化関心に合うが、画像を含む分類等に用途が限られ、訓練手法の計算負荷と実用成果物が未確認。 保留：再現用コード・モデル; GPU学習費用とCPU実行可能性
- [Understanding and Improving Knowledge Distillation for   Quantization-Aware Training of Large Transformer Encoders](https://arxiv.org/abs/2211.11014)：BERT系encoderの量子化学習と蒸留は分類のCPU単価を下げうるが、古い論文で具体的な現在のモデル・成果物が確認できない。 保留：利用可能なコード・モデル; 最新手法に対する効果
- [I-ViT: Integer-only Quantization for Efficient Vision Transformer   Inference](https://arxiv.org/abs/2207.01405)：整数専用ViT推論は軽量vision関心に合う。一方、画像以外の大量スクレイプ処理とは別用途で、実環境の対応カーネル・速度が未確認。 保留：CPU実装・対応命令; 実測速度と精度
- [Lightweight Vision Transformer Compression for On-Device Plant Disease Detection in Resource-Constrained Agricultural Field Conditions](https://arxiv.org/abs/2609.05334v1)：制約環境の植物病害visionモデル圧縮で、軽量画像認識として関心に合うが、対象画像用途との距離があり、重み・CPU速度が不明。 保留：データ・重みの公開状況; CPU推論性能
- [ThinkBit: Search Codewords, Recalibrate Scales, Select by Reasoning](https://arxiv.org/abs/2610.34886)：2-bit量子化は低コスト推論に魅力があるが、対象は長文生成型のreasoning LMで、短い分類・抽出向けとは限らない。実装・CPU対応も未確認。 保留：コード・量子化済み重み; CPUでのスループットと品質
- [AnkitAI/TinyJev-0.6B](https://huggingface.co/AnkitAI/TinyJev-0.6B)：軽量な確率付き分類器として適合しそうだが、既存の子タスクが修復上限待ちの試行中なので再開せず保留。 保留：既存試行の修復結果
- [MOCHA: Multi-modal Objects-aware Cross-arcHitecture Alignment](https://arxiv.org/abs/2509.14001v5)：少数例の個別物体検出を軽量モデルへ蒸留する研究。画像認識関心には合うが、スクレイピング文章処理とは用途が異なり、データ・重みとCPU要件も未確認。 保留：公開コード・重み; CPUでの実行可能性
- [Task-Specific Zero-shot Quantization-Aware Training for Object Detection](https://arxiv.org/abs/2507.16782)：物体検出のzero-shot量子化はvision関心に合うが、訓練データを使わない量子化法の評価で、文章分類・抽出には直接適用できない。 保留：公開コード・対象検出器; CPU向けの実用成果
- [GoiBERT: Compact Vietnamese Language Models via Two-Stage Knowledge Distillation](https://arxiv.org/abs/2610.30067)：26M/49Mのraw-textベトナム語encoderは、雑な文面の分類・抽出をCPUで大量処理する方向にかなり合う。ベトナム語以外への適用、モデル公開、ライセンスが未確認。 保留：対象データの言語; 重み・コード・ライセンス; 分類/抽出タスクの評価
- [PTQ4SAM: Post-Training Quantization for Segment Anything](https://arxiv.org/abs/2405.03144)：SAMの後処理量子化は大規模visionモデルを軽くする話で、画像segment用途が別。軽量vision関心には隣接するが、CPU・容量の適合性が未確認。 保留：量子化後モデル容量; CPU処理速度・精度
- [Compress What You See, Not What You Say: Anchored Context Distillation for Latent-Observation Software Engineering Agents](https://arxiv.org/abs/2609.31430v1)：開発agentの観測文脈を圧縮し、ソフトウェア工学タスクの処理効率を上げる研究で、AI駆動開発関心に合う。ただし学習済みモデルやコード、GPU要件が不明で、スクレイプ処理用途ではない。 保留：コード・学習済みモデル; GPU依存と実行費用

LiquidAI作者の現行一覧では`LiquidAI/d1-2B`、`LiquidAI/d1-Omni-2B`、`LiquidAI/d1-3B`と600M系を区別。2B系metadataの10/28更新を新しい重み発表と断定しない。Qwen2.5-Coder1.5Bを最新と呼ばず、公式一覧の`Qwen/Qwen3-Coder-Next`、`Qwen/Qwen3-Coder-30B-A3B-Instruct`等は別の上位系統であり今回のCPU/DL枠内の同等比較品とはしない。

## 状態・次回

完了5件＝日本語モデルの今回の限定比較1件と、理由付き見送り4件（MoE最適化survey、SmartBench、online軽量ViT survey、今回対象外のBekko retrieval）。古さだけを理由に除外していない。ダイジェスト掲載のみ、TinyJevの既存中断、他の未検証候補は完了扱いにしない。pendingは9,467→9,481。元carry日の変更0、理由のない消失0。`record_run.py`で詳細推薦0の通常実行とfreshnessを記録済み。

採用機能の実装なし、draft PRなし、merge/deployなし。最終remote SHA・4JSON一致・exact-SHA CIはコミット後に確認し最終返答へ記す。研究結果を保存するだけで通常CI保守は行わない。

検証：候補exit0、32IDの欠落・重複なし、独立scorer・native Schema・引用・state連続性を確認。既存labテスト151件は1失敗/15skipで、既知の`robots-parser`未導入によるbot-diagnostics失敗。今回のモデル比較の失敗ではなく、依存修復や通常CI保守はしていない。
