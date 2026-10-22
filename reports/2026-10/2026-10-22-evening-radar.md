# 2026-10-22 夕方・独立研究レーダー

## 結論

**A：TokenEconomizerとAKVを読解確認による深掘り候補として報告。** 候補コード実行・推論・比較実測は0件。「大きな効果」や再現成功は認定しない。Aは試す価値であり、PoC完了や本番導入の意味ではない。既存の資格情報隔離に関する停止条件を維持した。

## 概要・方法・入力

指定開始09:04 UTC、上限09:34 UTC。現金0円、CPU候補20分、RAM8GB、重みDL2GB、修復1回、carry28日を拡大していない。最新上流Skill `55ceed634506c5942d6a8e84c71d7e80acc103ff` の指定8ファイルを読み、checkoutの同8ファイルとのhash一致を確認。AGENTS.mdも確認した。RADAR_LLM=nativeを選び、ホストspawnにgpt-6-lunaを指定。probeのrequest ID・task hash・Schema・期待値が一致しexit 0になってから探索を開始。サービス実モデルIDはunknownで、ホスト指定と同一だと断定しない。

HF trending全応答50件、新着50件、分類/要約/抽出/軽量画像/小型モデル/AI開発の10検索、MDPI2誌RSS、Crossref5検索、GitHub4検索、arXiv5検索を取得。OpenReview5検索は403。重複排除後2,515候補、繰り越し込み6,310入力を全件採点（形式不正0、ranked5,350）。review_pool20件＋新着10件を一次読解。40件で打ち切っていないが、API返却件数と検索範囲には限界があり全公開研究の網羅は主張しない。workloadは検索語制限ではなく実用判断に使った。

## 深掘り1 — A：TokenEconomizer

[論文](https://arxiv.org/abs/2610.22732)／[公式実装](https://github.com/microsoft/TokenEconomizer)。公開10/15、取得時168 stars・13 forks、最終push10/22、最新main `0ce0e067b42b1dd5a5f5984275d07f8c7686c44a`。正式後継は未確認。

**目的・新規性・用途**：同じrepoで反復する環境上の失敗をmicro-skillとして次回へ渡し、未使用ツールと失敗ループを管理する外付けcontroller。モデルを変えず、既定では追加モデル呼出しを行わない。大量・低単価のNLP処理そのものではなく、そのパイプラインを開発する反復作業の費用削減仮説として具体的である。

**話題性**：[HF trending](https://huggingface.co/papers/2610.22732)41位と、著者とは別の[公開投稿・コメント議論](https://www.linkedin.com/posts/apanshina_uc-berkeley-and-microsoft-just-published-activity-7518598074869694465-VQJ5)を確認。tool削減によるcache再構築費用が議論されている。第三者性能再現ではない。

**結果・比較**：著者は277 SWE-Bench課題・6repo・5agent/model組合せで平均token cost18.4–30.9%削減、Pass@1は5条件中4条件で改善と報告。当方実測なし。最大削減率だけで品質相対5%改善、または品質低下2%以内かつ資源30%改善を認定しない。

**導入・商用・資源・問題**：コードApache-2.0、Python3.10+、主要packageは標準ライブラリのみと記載。Claude Code/Codex/Copilot CLI/Piへのhookを使う。CPU時間・RAM・全DL・実費は未測定。無料helperはagent自身のモデル費用が無料という意味ではない。対応CLI版はREADMEの範囲に限定される。

**toolpruneは既定autoでagent設定を書き換える。** 第三者投稿の承認制という説明を採用せず、公式READMEの `toolprune=propose` と `paid=false` を確認。今回pluginを導入せず、.codex設定にも触れていない。cache再構築と稀に必要なツールの喪失を評価する必要がある。

**判断・次**：隔離が確認できた場合にのみ、公開/合成した3本のsession logで環境失敗を再発させ、micro-skillの重複・誤学習、propose時の設定非変更を調べる。その後に固定開発課題で成功率とcache込みtoken費を比較する。認証にアクセスする自動導入は実施しない。

## 深掘り2 — A：Cache Engineering for Agents / AKV

[論文](https://arxiv.org/abs/2610.28577)／[公式実装](https://github.com/thu-nics/AKV)。公開10/20、取得時27 stars・2 forks、最終push10/21、最新main `c567a429a058f1abce3b2879e69978b09e0fda4c`。正式後継は未確認。

**目的・新規性・用途**：履歴を削除・要約するとprefixが変わり、残存文脈まで再prefillする。AKVは文章を維持したままKV cacheをdropし、残存keyの位置をreposで調整する。operation履歴もcache同一性の条件にする。長いAI開発・文書処理agentの費用とthroughputへの具体的価値がある。短文分類encoderの代替ではない。

**話題性**：[HFの67upvote](https://huggingface.co/papers/2610.28577)と[DAIR.AIの独立解説・第三者議論](https://www.linkedin.com/posts/dair-ai_we-love-papers-that-go-deeper-down-the-stack-activity-7518733866879336448-FcTh)を確認。独立した関心の信号であり性能再現ではない。

**結果・比較**：著者報告は閾値要約比で平均score+1.7ポイント、API換算cost−27.7%、出力throughput最大1.2倍、P95 latency最大1.5倍の高速化。論文付録A.3は固定token単価の**API-equivalent**費用であり、GKE/AWSの実請求額ではない。当方実測なし。古い価格を現行とせず、費用優位・1万件換算は未確認。

**導入・商用・資源**：コードApache-2.0、Python3.12、Rust/Cargo、起動時C++20 kernel compilation、SGLang拡張が必要。トップREADMEはGPT-OSS-120B/TP2だが、[system README](https://github.com/thu-nics/AKV/blob/main/akv/system/README.md)にはQwen3-0.6B例もあり120B必須とは判断しない。attention backendはGPU対応前提で、CPU専用動作・全DL・RAM/VRAM・20分枠適合は未確認。Context requestではLoRA・speculative decoding・外部/session cache等が非対応。

**問題・判断・次**：残存KVは削除前の情報を取り込んでいるため、dropと情報消去は同義ではない。削除tool responseだけに正解がある合成taskで、drop/repositionと残存文の再prefillを比較し、正答・情報残留・計算量を別評価する。既存の隔離停止を計算不足としてKaggleに迂回しない。Aは設計検討の価値であり実行条件充足ではない。

## 未検証ダイジェスト

一次読解のみ。推論・品質・資源は実測なし。既報Harness²/NCM/TokenGate/Qwen2.5-Coder/GRACEの同じ詳細は抑制した。

- **B — [GoiBERT](https://arxiv.org/abs/2610.30067v1)**：ベトナム語raw text向け26.4M/49.4M encoder。語分割を省く分類・NERの候補。 保留理由：S/M実重み105.6/197.5MBを確認。配布はtask head無し、商用license未確認。著者の53/99MB表はbf16条件で実配布量と異なる。

- **B — [LGWM](https://arxiv.org/abs/2610.11942)**：画面だけでなく行動後の遷移を潜在表現で検証する軽量GUI認識。 保留理由：CPU指定あり、推論173M・重み約691.5MB。17msはA100著者値。CPU時間/RAM・独立話題2信号は未確認。

- **B — [PrismaCoder](https://arxiv.org/abs/2610.20538)**：コードと描画結果を分離したルーブリックで視覚コード生成を評価。 保留理由：9B RL重み計18.82GBで2GB超。35B-A3B RLはID実在でも重み未掲載、SFTとは別。重みMIT表示、repoの商用条件未確認。

- **A — [Socratic Skill Learning](https://arxiv.org/abs/2610.17860)**：実践と教師の問いを交互に行い、Skillが未見課題へ転移するか測るAI開発手法。 保留理由：Node22.19+/Docker/model endpointが必要。コードMIT、教材は個別条件。現金0での経路・独立話題2信号未確認。

- **B — [PACE](https://arxiv.org/abs/2610.29545)**：線形attentionの再帰stateを毎tokenでなく周期的に4bit化し誤差蓄積を抑える。 保留理由：著者評価は4B–35B hybridモデル。公式実装/配布物/license/CPU適合未確認。state圧縮と重み圧縮は別。

- **B — [TraceForge](https://arxiv.org/abs/2610.29680)**：教師の答えからstudent系統に合う推論traceを生成する蒸留手法。 保留理由：生成器・criticの学習が必要で今回対象外。公式配布・商用条件未確認。真正性scoreを正答率と解釈しない。

- **B — [Ask-to-Distill](https://arxiv.org/abs/2610.27450)**：正解ラベルなしの質問選別で蒸留効率を高め、既成studentも公開。 保留理由：1.5B student実在・MIT表示、単一重み3,554,214,752 bytesで上限超。学習は行わず、小容量形式未確認。

- **B — [VX-One](https://arxiv.org/abs/2610.30090)**：unknownを持つ型付き判定headの小型分類。CPU低遅延と校正崩れを併せて報告。 保留理由：ネットワーク侵入検知用でNLP分類器ではない。HF IDはコード配布のみ、重み未掲載。学習/資源・用途適合未確認。

- **A — [農作物病害の軽量ViT圧縮](https://arxiv.org/abs/2609.05334v1)**：村・端末を跨ぐ評価で圧縮パイプラインと同サイズstudent直接学習を比較。 保留理由：直接学習studentでも近い精度という著者結果が重要。既成重み/商用条件/CPU実測・独立話題2信号未確認。

- **A — [Recursive Reasonerのエッジ圧縮](https://arxiv.org/abs/2606.26488)**：再帰過程の量子化誤差を追い、再学習なし校正で回復する小型化の仮説。 保留理由：3.3MBは著者のbackbone値。入力埋込みを含む総資源・公開artifact・実測・独立話題2信号未確認。

- **B — [ATEST](https://arxiv.org/abs/2610.25519)**：GPU混雑時の電力費用をオンライン学習し、追加サンプリングの停止を選ぶ。 保留理由：A100/L40S評価。低負荷短タスクではtail latency/throughputが悪化。実装とCPU適合未確認。

## 問題・検証・state・次

native初回30件・追加8件はrequest ID/hash/Schema検証成功、各180秒以内。初回8引用・追加2引用が不一致で不採用。詳細2件の採用引用は完全一致。reviews.jsonとupdated-reviews.jsonは未採用部分を含む判定草案で、確定事実は本報告とreview-validation.jsonを優先する。LGWMのCPU不可、配布容量不明等の誤読は保存API/READMEで訂正。PTQ4SAMの資源制約のみのCは採用せず追加判定Bを採用。completed_idsは空。

GoiBERTの.git付きURLを正規化し実在repoへ到達。旧namespaceからTwoheartsTechnologiesへの実在IDを確認。PrismaCoder35BのRL/SFT、VX-Oneのコード/重み、Ask-to-Distillの学習/既成student推論を区別した。RAM/CPU/商用条件の不明値は未確認のまま。

正規remote4715cc03e4ea376a268e700e94fd3b9ff51981b4から未完了6,212件を復元。Skill直下でrank_candidates/record_runを--state-dir state付きで実行。未実行の深掘り2件はseen/freshnessへ記録しqueueへ保持。ランキング除外・digest・中断は完了にせず、過去の理由確認済み見送りを再発見だけで復活させない。seen/queue/mail/freshnessを正規夕方保存先へ同期、mail不変、adaptive_freshness維持。記録後未完了6,309件。

ローカル報告作成時に作業ディレクトリの指定誤りが1回あり、文書パスのみ修正した。候補コード実行・環境修復ではない。record_run自体は成功し、同runを二重記録していない。

外部コードの資格情報隔離に関する既存停止を維持。計算不足で失敗していないためKaggle利用なし。新規認証・権限緩和・CLI fallback・メール・cron・朝/weekly・SKU・PR19・Moli/Lightpandaを変更していない。朝task結果へアクセスせず再実行もしていない。通常権限で読めるorigin/mainのreportsには10/22朝報告を見つけられず、朝との完全重複照合は未確認。

保存対象は本run、benchmarks/research-repro-radar/2026-10-22-evening.json、本報告、夕方state。実用機能完成ではないためdraft PRなし、merge/deployなし。remote SHA一致とCI状態は親への最終報告に添える。

**収集2,515／一次読解30（review_pool20）／実検証0／記録後未完了6,309。**
