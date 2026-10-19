# 2026-10-19 JST 夕方研究レーダー

結論：**収集・採点・一次読解は実施、導入判断は未完了。モデル推論・候補コード実行・実測改善は0件。未完了5,236件を保持。** native応答の意味監査で用途条件の過度な限定と数値誤読を検出したため、A/B/Cを確定せず、完了IDを追加しない。再現成功とは呼ばない。

## 確認できた新しい資料上の結果

- NCMのXSum用tokenizer 299,626,079 bytesとgenerator 354,355,211 bytes、合計653,981,290 bytes（約654MB）を公式配布で確認。コードと配布モデルはMIT。推論用のこの2ファイルが2GB内でも、依存・評価モデル込みの全導入量、CPU時間、RAMは未測定。公式評価はGPU shard方式で、英語XSumの著者比較を日本語・CPUに外挿しない。[モデル](https://huggingface.co/hazyresearch/ncm-checkpoints)／[実装](https://github.com/HazyResearch/ncm)
- DLiteはREADMEのQwen3.5版「weights pending」と公式モデルAPIが食い違う。APIには `SJTU-DAI-Sys/Qwen3.5-4B-DLite` revision `c5e2498a044aaacbaa4695b95ae2bda79a3bc29e`、1,290,683,952-byte重みがある。**配布の存在は対応実装や動作の証明ではない。** target model本体も別途必要。[モデル](https://huggingface.co/SJTU-DAI-Sys/Qwen3.5-4B-DLite)／[実装](https://github.com/SJTU-DAI-Sys/DLite)
- Frozen 16MB語彙研究は小さい配布物がある一方、model cardは**非商用研究限定**、Gemma由来条件も記載。46MB/615–740 tokens/sは作者の特定CPU・128-token contextでの報告。品質同等を全指標で達成した結果ではなく、こちらの実測はない。BitNetの保留を解除しない。[カード](https://huggingface.co/QLNI/embeddings-are-overpriced)
- REMORYはQwen用memory単体8,023,041,444 bytes、GLM用4,966,315,580 bytes。旧mocoV3 IDはfudan-generative-aiへ解決。READMEは最低2×80GB GPUを推奨。10/18追加のオンライン学習は現条件では実施しない。[公式実装](https://github.com/fudan-generative-vision/Remory)

## 方法・版・入力・実測

開始時にopen PR19/21/23/25/26/28を確認し、既存SKU/他担当作業は変更していない。朝7c5396bのMoli/Heritrix/Firecrawlと前朝WebMCP/SetFit/SmolVLM2、前夕Bekko System One/GraniteDocling/nanoMuse/Edge0/UnlimitedOCRを照合。新規性が確認できない既報候補の詳細通知をしない。SKU ca0d84aの負荷データを新精度証拠に使わない。

最新版Skill 55ceed634506c5942d6a8e84c71d7e80acc103ffの指定8資料、checkout AGENTS.mdと関連.agents/skillsを読み直した。正規夕方remoteは55bcd617ae45f5d8a36bc618e7be05154713bf46で開始時にそれ以上の更新なし。5,204件をSkill直下stateへ復元。Python3.12、jsonschema4.26.0。

最初からRADAR_LLM=native。公式prepare→gpt-6-luna指定ホストspawn→終了確認→validate exit0。probe要求ID c7bde56a-f3fe-4f97-98ad-dcdc7bd52a46、task SHA256 9783d15e810baeba3639e873bf561db9fcf5936782b05227f9ccab78fd1592c4。要求ID/入力hash/schema/期待値を検証。ホスト指定はgpt-6-luna、サービス実モデルはunknown。CLI fallback・新規API判定経路・資格情報コピー/変更・権限緩和なし。

HF trending50、新着50、広い10クエリで1,220一意。MDPI/Crossref/GitHub827レコードとarXiv500レコードを統合し2,515一意。OpenReview5検索は403。全返却ページを保存、APIの返却上限があり全世界の網羅とは主張しない。workloadで探索語を狭めず、公開年だけの除外なし。83件が前夕収集との差分。DOI/arXiv/id/URLの標準キーで重複排除したが、独立fork/別ID間の意味的重複は完全検出ではない。

全入力6,084件（繰り越し込み）を処理、5,229件採点、review_pool20件を全件読解。追加11件も資料取得・確認。20分estimateは確認割当で、性能推定ではない。requires_gpu=false既定値もCPU適合の証明ではない。一次資料150ファイル・6,512,353 bytesをhash/bytes照合。重み・候補依存download0、GPU0秒、現金0円。CPU時間/RSS/品質/スループット/1万件換算は未測定。資料処理とnative応答は候補推論ではない。API $6.25/万件やCPU15.5秒/件のprofile基準に対する費用優位は判定不能。

## 判断監査で止めた事項

6バッチ31候補をnative要求へ渡したが、呼出し側がverdict配列を指定したことがdict契約に不適合。候補schemaは変えず外側をreviewsオブジェクトにする形式修復を各要求1回だけ行った。5バッチ25候補はschema検証成功。残る6候補は共通180秒期限で停止し、完了扱いにしない。保存済み応答・要求は私有作業領域、公開成果物にはhash/検証結果を保存する。

さらに親の原本照合で、検索用途の条件を画像・NLP・AI開発の全分野へ拡張したC判定、Bekko Embeddingを具体的欠落なしでcoverage改善としたA判定、Qwen重量の別形式重複加算、Edge0の9/30更新を前夕後の変化とした誤りを検出。これらの判断は採用しない。引用不一致もreview-validation.jsonへ記録し報告根拠から除外する。**25件のschema成功は25件の妥当な導入判断ではない。A/B/C確定は未完了。** 追加の意味修復・期限延長で押し切らず、すべてpendingへ残す。raw reviewsは監査対象であり採用結果ではない。

## 実測・根拠ある深掘り

実測改善0件、今回確定した深掘り推薦0件。上記の一次資料上の配布・制約確認は読解成果であり、性能や導入可否の認定ではない。品質相対5%／品質低下2%以内＋資源30%削減を確認した比較なし。NCM/REMORYの注目信号はHF順位とGitHub starsがあるが、判定監査未完了のため推薦として自動完了させない。

## 未検証ダイジェスト

- [Next-Concept Modeling: Fast, Parallel Coarse-to-Fine Text Generation](https://arxiv.org/abs/2610.14162) — HF trending35位。階層VQコードを粗→細で並列生成し要約を高速化する設計。XSum用tokenizer+generatorは653,981,290 bytes、MIT。 保留：CPU経路/RAM/日本語品質未検証。公式評価はGPU shard方式。追加判定が期限切れのためA/B/C未確定。

- [REMORY: Learning Residual Memory for Context Compaction](https://arxiv.org/abs/2610.11287) — HF trending45位。要約に残差soft memoryを足す設計。MITコード、Qwen用memoryだけで8,023,041,444 bytes、2×80GB GPU推奨。 保留：2GB取得・8GB RAM条件外。10/18のonline training追加は学習禁止のため試さない。A/B/C未確定。

- [DLite: History-free Speculative Decoding for Agentic AI](https://arxiv.org/abs/2610.23787) — draft側の履歴KVを省く投機的復号。公式Qwen3.5-4B-DLiteに1,290,683,952-byte重みを確認、READMEのweights pendingと不一致。 保留：target本体も必要。現行APIのQwen3.5対応/総取得/RAM/CPUは未確認。A/B/C未確定。

- [Embeddings Are Overpriced? A Frozen 16 MB Vocabulary for Small and Ternary Language Models](https://arxiv.org/abs/2610.22428) — 小型モデルの語彙を固定512-bit表にするCPU志向研究。配布kernel/armC.binは46,236,196 bytes。 保留：非商用研究限定＋Gemma由来条件。BitNet保留維持、実行なし。独立性能比較・日本語品質・A/B/C未確定。

- [LLM Inference in a Flash!](https://arxiv.org/abs/2609.16161) — 整数量子化と辞書KV圧縮をFlash内演算へ組み合わせる一次論文。通常SSD offloadと異なる設計。 保留：専用Compute-in-Flashの解析モデルが中心。公開実行物/対応hardware/商用条件/実測効果未確認。判定監査保留。

## 保存・停止・次

rank_candidates.py --state-dir stateを1回実行。順位から外れた既存7件もretain_unfinishedで戻し、開始時5,204件の全IDと初回carry日を保持。completed_ids=[]、recommended=[]でrecord_run.py <report.json> --state-dir stateを実行し、詳細0の実行記録を残す。seen/mailはbyte一致、freshnessのみ今回の0推薦記録を追加。朝・weekly stateは変更なし。probeは成功しているのでprobe失敗によるstate不変ルールには該当しないが、後段判定失敗はstatusに明記する。

Squeez修復上限、YOLO26nの/app権限失敗、Harness²隔離PermissionError、PP-LCNet停止、BitNet保留を維持。別環境/GPU/日付変更で再開なし。隔離問題の既存質問は返答待ち、再質問・自動化停止なし。候補コードを実行しておらずKaggle移行条件も未成立。認証/無料残量/GPU機種/private設定は今回照会していない。

実用機能を追加していないのでdraft PRなし。merge/deploy/メール/SMTP/cron変更なし。mail.jsonはsent=[]でlast_sent_at不明。今回はdigest5件のため両方0の14日heartbeat条件には該当せず、親が通知を担当する。送信済みと記録しない。

次回はこの資料を使い、検索用途だけの制約と他の関心分野を分離した判定要求を確認する。今回失敗した判断をCとしてキュー削除しない。NCMは英語要約のCPU実装経路と依存全量、DLiteはAPI実装とQwen3.5重みの整合、Frozen語彙は利用許諾、REMORYは資源条件が具体的な保留理由。推論開始を承認した意味ではない。

収集2,515／全処理6,084・採点5,229／一次読解review_pool20＋追加11／候補実検証0／記録後未完了5,236。remote最終hashとそのcommitのCIは最終返信で報告する。
