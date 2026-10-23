# 2026-10-23 JST 夕方・独立研究レーダー

## 結論

**詳細推薦0件、未検証ダイジェスト10件。** TTR-Compressor、SCOPE、VerifierBench/Gavelなどに試す価値（A）はあるが、独立した話題性2信号の基準を十分に確認できず、深掘り推薦には昇格しなかった。実測の大きな効果0、候補コード実行・モデル推論0。再現成功や簡易実行確認とは扱わず、すべて一次資料の読解確認である。

昨日のTokenEconomizer/AKVと今朝のSearchJevは有意な追加根拠がないため再通知しない。既存の資格情報隔離に関する停止、Moli/Lightpandaの追加修復保留、PR19検索追加中止を維持。SKU・朝の実行を変更しない。

## 方法・入力・版・時間

開始09:04 UTC、期限09:34 UTC（18:04–18:34 JST）、現金0円。CPU候補20分、RAM8GB、重みdownload2GB、候補修復1回、carry28日。最新版上流Skill `55ceed634506c5942d6a8e84c71d7e80acc103ff` の指定8ファイルを読み、実行側コピーとのhash一致を確認。checkoutのAGENTS.mdと `.agents/skills` も確認した。

`RADAR_LLM=native` を開始時に明示。ホストモデル `gpt-6-luna` のprobeを実施し、要求ID `8f081ee2-34e4-41b7-94ef-47651dafd14d`・task hash・schema・期待値が一致したexit 0の後でのみ探索した。サービス側実モデルIDは `unknown` でありホスト指定と同じだと推測しない。最初のprepareは出力親ディレクトリ未作成で失敗し、私有一時ディレクトリ作成の1回修正で成功。認証/CLI/権限の修復ではない。

HF trending全返却50件、新着50件、関心5分野の10検索から1,223一意候補。MDPI2誌、Crossref5検索、arXiv5検索、GitHub4検索で補完し、GitHubの290件検索は3ページすべて取得。最終2,710候補。40件で収集を打ち切っていない。ただしarXiv/Crossrefは各検索100件の窓であり全ページ完走ではなく、網羅性は限定される。OpenReview5検索はHTTP403、迂回せず不足として記録。

繰り越し込み6,607入力をすべて採点、ranked5,554、形式不正0。最終review_pool20件＋新着10件を一次資料で実際に読解。6件を追加資料で再判定した。native全36判定がschema検証成功、要求作成から応答保存まで各180秒以内。初回11引用は原文/URL不一致で不採用。追加6件の引用は原本完全一致。詳細は `native-host-ledger.json`、`review-validation.json`、`reading-ledger.md`。

## 読解で分かったこと（詳細推薦・実測ではない）

**TTR-Compressor**：[原論文](https://arxiv.org/abs/2610.31574)／[公式コード](https://github.com/Sphere-AI-Lab/ttr-compressor)／[公式collection](https://huggingface.co/collections/SphereLab/ttr-compressor)。論文10/22、repo 8 stars、10/23 push。入力token間の相関を回帰の閉形式解に取り込み、pooling/MLPで失われる詳細を保持する設計。長文と文字を含む画像の効率化に具体的価値がある。

著者の視覚merger比較は、TTR-conv 3×3平均54.85に対しMLP 2×2が54.93、視覚token55.6%削減。これは著者値で、当方の時間・RAM・品質の実測ではない。token削減率をCPU時間削減率と読み替えず、汚い入力への耐性も未確認。

公式 `sihany/ttr_345m` が実在。実際は446,631,936パラメータ、safetensors 893,293,472 bytes、revision `36f2508ef1e073e6af42cfdd632b18a2a232a338`。名前だけで345Mと断定しない。configは `HyDenForCausalLM` / `qwen_hyden` / `flash_attention_3`。CPUロード・custom実装・RAM8GB内は未確認。`sihany/ttr_1b` は2,247,931,312 bytes、視覚版 `sihany/ttr_vlm_conv_3_3` は9,465,033,968 bytesで上限超。後継版未確認、repo/LICENSEと小型model cardは404、商用条件未確認。

[HF10 upvotes](https://huggingface.co/papers/2610.31574)と[AI Weekly当日解説](https://aiweekly.co/alerts/ttr-compressor-matches-mlp-merger-with-556-fewer-visual-tokens)を取得したが、後者は同論文の要約で独立根拠として弱く、deep_dive_valueにはしなかった。次は隔離条件が解決した場合に限りcustom attentionのCPU経路を静的確認し、既成小型checkpointを短く確認する設計。学習やGPUへ迂回しない。視覚版の著者比較を小型言語checkpointの結果として流用しない。

**SCOPE**：[原論文](https://arxiv.org/abs/2610.31062)は複合ポリシーを小型の述語・決定的抽出・論理へ分ける。NLP分類/抽出/判定として評価し、Web検索条件を課さない。[Plurai/SCOPE](https://huggingface.co/datasets/Plurai/SCOPE)は実在・MIT、4タスクtrain309/test562件、生成rationaleはtrainのみ。実装・推論用プログラム・モデル資源未確認。programとtraining dataの最適化を重み学習必須とは断定しない。次は1ポリシーの分解による抽出漏れ/判定不一致を小さい固定入力で確認する設計。独立話題性2信号未確認。

**VerifierBench/Gavel**：[原論文](https://arxiv.org/abs/2610.28679)／[公式実装](https://github.com/DwanZhang-AI/verifier-suite)。正しい別解を生成検証器が拒む問題を扱う。repo 3 stars・10/22 push・Apache-2.0。[データID](https://huggingface.co/datasets/Catherine1213/VerifierBench)も実在するがデータは個別条件。追加README著者結果は同じGPT-6-astraでBA_norm 62.1→68.3に対し攻撃誤受理7.4→18.2。全指標で改善したわけではない。全再現はAPIとworkspace/Docker各100GiB等を要し今回枠外。小さい静的監査とは区別する。次は正解・正しい別解・誤解の組で要求外の制約を検証器が加えていないか確認。独立話題性2信号未確認。

## 未検証ダイジェスト（10件）

A=試す価値、B=条件付き。PoC完了をAの条件にはしない。すべて読解のみ。

- **A — [TTR-Compressor](https://arxiv.org/abs/2610.31574)**：入力token群の相関を回帰係数へ圧縮し、長文・視覚token削減を狙う。 保留：実在sihany/ttr_345mは446.6M・重み893MB、configはflash_attention_3。CPU動作/商用license未確認。HF10票とニュース要約の独立性は弱く、深掘り2信号未達。

- **A — [SCOPE](https://arxiv.org/abs/2610.31062)**：複合ポリシー判定を小型の述語と決定的抽出・論理へ分ける。NLP分類/抽出に直接関係。 保留：Plurai/SCOPEデータは実在・MIT。実装/配布プログラム・実行資源未確認。プログラム最適化を重み学習必須とは断定しない。

- **A — [VerifierBench / Gavel](https://arxiv.org/abs/2610.28679)**：生成検証器が正しい別解まで拒む問題を、正負両方のケースで評価する。 保留：コードApache-2.0、データは個別条件。追加著者結果は攻撃誤受理とのtradeoffあり。全再現はAPIとworkspace/Docker各100GiBを要し、今回未実行。

- **A — [Same Simulation, Different Evidence](https://arxiv.org/abs/2610.26836)**：計算が正しくてもツール出力の読み替えで回答が崩れる現象。根拠の明示と抽出の設計に価値。 保留：コードMIT・データ別条件。科学シミュレーションに限定された著者結果で、汎用NLPへの転移・無料評価経路は未確認。

- **A — [Raw-RAG](https://arxiv.org/abs/2610.30755)**：会話履歴を変形せず保存し、必要な関係・依存を根拠として残す単純な比較基準。 保留：外部サイトのrerank追加とは別用途。license・API費用・CPU資源未確認。独立2信号未達で再現なし。

- **B — [RouteWeaver](https://arxiv.org/abs/2610.19705)**：モデルだけでなく単発/複数段/agent実行の方式も選ぶrouter。 保留：e2rea1/RouteWeaver-4Bとcost版が実在。routerだけ8.82GBでDL上限超。公式評価は80GB GPU1枚＋worker群。

- **B — [IQuest-Q1](https://arxiv.org/abs/2610.30949)**：CLIエージェント向けのMoE基盤モデルを新着から捕捉。 保留：公式IQuestLab/IQuest-Q1はBF16約650GB・独自license。15B活性化は総320Bの保存量を減らさない。小さい公式後継未確認。

- **B — [Cola2](https://arxiv.org/abs/2610.31305)**：連続意味圧縮と離散生成を協調させる階層言語モデル。 保留：原論文のみ確認。実装・推論checkpoint・license・CPU資源未確認。arXiv HTMLは404。

- **B — [Policy-Mask](https://arxiv.org/abs/2610.28889)**：拡散特徴の時刻/層/チャネルを予算に合わせ選ぶ画像認識手法。 保留：学習済みの利用可能policy/重み・商用条件・CPU資源未確認。新規学習は今回範囲外。

- **B — [Meta Expert](https://arxiv.org/abs/2610.27598)**：MoEの層間でexpertを共有し、パラメータ冗長性を減らす設計。 保留：内部expertアクセスの研究で、Web検索条件を適用しない。公開実装/重み/導入条件未確認。

## 実行・比較・問題・state

候補CPU時間・RAM・品質・throughput・1万件換算は未測定。候補重みDL0、GPU0、現金0。現行の一次価格比較も行っていないため、API/GKE/AWSより安いとは主張しない。ネイティブJSON判定成功は候補推論成功ではない。初回判定の「実装なし」は未確認に限定し、SCOPEデータ、TTR checkpoint、RouteWeaver/IQuest公式IDを追加一次資料で訂正。Meta Expert内部アクセスとSame Simulationの出力解釈を外部Web検索と混同した初回理由も不採用。

正規remote `e7f2392b3115e44d9b5e40873339881a19943c8f` からseen/queue/mail/freshnessをhash一致でSkill直下stateへ復元。初回採点後にGitHub次ページを追加し、元queueから再採点したためcarryを2回進めていない。最終review_pool20件は初回と同一。順位除外・未実行・中断・digestを完了扱いにせず、過去の完了IDを再発見だけで復活させない。今回completed_idsは空。

`record_run.py report.json --state-dir state` をSkill直下から実行し、詳細0も正常記録（seen0追加、freshness0/0、completed0）。報告文の作成に1回cwd指定誤りがあり、文書パスだけ修正。record_run自体は成功しており二重実行していない。seen/mail不変、freshness今回1run追加、queue未完了を正規evening-stateへ同期。未完了6,309→6,606。

外部メール/SMTP/Actions cron、朝state、SKU、PR19、Moli/Lightpandaは変更なし。GPUは計算不足の失敗がないため利用せず、隔離停止を迂回しない。既存の隔離保留について重複質問もしない。実用機能未完成なのでdraft PRなし、merge/deployなし。独立研究ブランチと正規夕方stateブランチへ保存し、push後remote SHAとCIを会話で報告する。

**収集2,710／一次読解30（review_pool20）／実検証0／詳細0／記録後未完了6,606。**
