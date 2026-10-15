# 2026-10-15 夕方 Research Repro Radar

## 結論・概要

**候補推論の成功0、深掘り推薦の確定0。** 23,721一意候補を収集し、繰越込み23,872件を処理、5,128件を順位付け。最終review_pool20件を一次資料で読解しnative判定を検証した。未完了5,133件には前回1,072件をすべて保持。AはPoC完了を必須としないが、今回は独立した話題性2信号などの推薦基準を確認できなかった。

## 方法

最新Skill `55ceed634506c5942d6a8e84c71d7e80acc103ff`、profile v7。`RADAR_LLM=native` を最初から選択。ホスト要求モデル `gpt-6-luna`、実サービスモデルID `unknown`。probe、保存済み資料1件、20件読解の各request ID/input SHA256/schema/期待値を検証。引用20件は保存済み原文payloadと完全一致。native判定は候補推論の測定ではない。

HF trending430ページ（先頭ページ再取得を含む）・新着初頁・10関心クエリを探索。arXiv5クエリ500件、MDPI2フィード200件、Crossref/GitHubを補完。HFは取得予算と読解時間で打ち切ったため網羅完了ではない。OpenReviewは5件とも403。arXiv版番号を正規化して35重複を統合。GitHubの似た説明文だけでは同一研究と断定せず、DM-Code-Agent2repoは両方fork=false/原著性未確認と記録。

候補ごとの目的、新規性、日付、stars/開発、license/商用条件、導入、CPU/GPU/RAM、制約、適合、検証価値は [reviews.json](../../experiments/research-repro-radar/runs/2026-10-15-evening/reviews.json)。知らない項目は未確認。source取得・順位データと読解判断を分けて保存。

## 実測・詳細確認

### Squeezの旧150M抽出モデル：推論に届かず保留

[KRLabsOrg/verbatim-rag-modern-bert-v2](https://huggingface.co/KRLabsOrg/verbatim-rag-modern-bert-v2) revision `6a967332efedfe5aca9b85b8310cc68d9ac6f881`、598,439,784-byte重みのSHA256を照合。公式cardのApache-2.0。現在の[Squeez-2B](https://huggingface.co/KRLabsOrg/squeez-2b)は別の2B生成モデルであり今回未検証。

Python3.12 / torch2.7.1+cpu / transformers4.53.3 / safetensors0.5.3。ネットワークなし・read-only・非root・cap-drop ALL・no-new-privileges・2CPU・RAM8GiB・ホストmountなしのコンテナ。空の一時BUILDX_CONFIGで以前のbuildx問題は解消。最終的にtorch.compile→Inductor cache→getpassで `KeyError: getpwuid(): uid not found: 10001`。候補推論 **0/10**。25.424秒は失敗した初期化のwall timeで、推論時間・CPU性能・RAM測定ではない。

**修復上限の逸脱:** 担当サブエージェントがネットワーク不通をwheelのoffline導入で修復した後、ファイルmode600の読取り権限も修復し、上限1回に対し2回行った。親の監査で発見して追加実行を停止した。成功扱い・完了ID化しない。自動承認reviewの拒否や認証コピーではなく、作業上限の遵守失敗である。

### 同じ10合成入力のベースラインのみ測定

|方法|clean 5件：保持文字率|noisy 5件：保持文字率|正解行recall（各群）|
|---|---:|---:|---:|
|全文保持|100%|100%|9/9 = 1.000|
|質問の単純keyword部分一致|40.4%|25.1%|3/9 = 0.333|

文字数でありtoken数ではない。小さい合成5組の結果で、Squeezの品質や一般性能を示さない。入力hash・モデル/依存hash・手順・エラー・per-case数値は [benchmark](../../benchmarks/research-repro-radar/2026-10-15-squeez-modernbert-continuation.json)。親が入力hash、image ID、ベースライン再計算一致を確認した。期待deltaは「全文の必要行recallを保ち文字量を減らす」だったが、モデル比較は未達。

## 比較・採用判断

大きな効果の閾値（品質相対5%以上改善、又は品質低下2%以内かつ資源30%以上削減）を候補モデルで測定できていない。CPUで動くことだけでAPI/GKE/AWSより安いとはしない。1万件換算・throughput・現在単価比較は未評価。著者の性能値と今回のbaseline測定を混同しない。

読解優先度はA=すぐ試す価値、B=条件付き、C=現状不要。今回のnative reviewはA0/B14/C6。Cも今回確定の除外には使わず未完了を保持。新着repoのキーワード・鮮度加点でAI開発実験が上位を占めたが、stars/再現/communityが不足するものが多く、順位を推薦に直結させなかった。

## 未検証ダイジェスト（最終順位上位15）

1. **C [SWE-Stream: Scaling Executable Training Data for SWE Agents via Progressive Construction and Elastic Streaming](https://arxiv.org/abs/2610.20193v1)** — SWE agent向けに開発履歴から実行可能な学習タスクと軌跡を段階構築し、ストリーミング実行する研究。本文では10,728リポジトリ、80,874タスクなどを報告するが、著者報告値で独立再現ではない。 保留: training prohibitedの制約に合わず、ユーザーの主用途からも距離がある。掲載された数値は論文の主張であり、今回の環境での実測ではない。
2. **B [cacacacc/local-swe-agent-eval](https://github.com/cacacacc/local-swe-agent-eval)** — ローカル配備LLM coding agentをSWE-Bench Verifiedで評価する実験用framework。タスクの隔離、モデル呼出し記録、再開可能な評価などを扱う。 保留: model callとSWE-bench実行は時間・計算資源を要する。loopback制限は直接socketを開くプログラムを完全には防がない、と抜粋が明記。評価品質の独立再現なし。
3. **B [divyanshu4501/fixpilot](https://github.com/Divyanshu4501/fixpilot)** — title上はissueからPRを作るcoding agentで、SWE-bench Liteを評価対象とする。添付資料にREADME本文や実装詳細がなく、機能範囲の確認は限定的。 保留: タイトルとURL以外の一次資料が不足。SWE-bench Liteの実測主張や再現手順は確認できず、20分の予算で評価可能かも不明。
4. **B [harper3105/dm-code-agent](https://github.com/Harper3105/DM-Code-Agent)** — ローカル先行・監査可能なPython code agentと30 taskのhidden-test benchmarkを提供するとするrepository。README抜粋はテスト、trace、checkpoint、評価手順を説明する。 保留: 候補の独自性、READMEとrepositoryの対応、licenseの不一致が大きな確認点。30題・hidden tests等はREADME上の記述で今回実行していない。
5. **C [jasonpolyakov44995/dm-code-agent](https://github.com/JasonPolyakov44995/DM-Code-Agent)** — 前候補とほぼ同じ説明・README内容を持つDM-Code-Agent候補。GitHub metadataでは別owner JasonPolyakov44995のrepo、同じ30 task benchmark等をうたう。 保留: 近似候補と同一README記載のため独自性、実装の出所、メンテナンス責任が分からない。二つのrepoや作者記載を独立した研究信号として数えられない。
6. **B [kingjone0/mini-claude-code](https://github.com/kingjone0/mini-claude-code)** — Claude Agent SDKを使う手書きmini coding agentとSWE-bench風評価基盤をうたう。README抜粋ではpytestの通過数、benchmark上の方法論上の制約、MIT等を説明。 保留: READMEは自作注入欠陥やissue文面の代理など評価手法上の制約を明記。768 tests passed等はREADMEの主張で、未実行。外部Claude API利用なら無課金条件に合わない可能性。
7. **C [kkolvin/sim-agentic-grpo](https://github.com/KKolvin/sim-agentic-grpo)** — SWE-bench coding agentをmulti-turn GRPOで訓練し、そのtraining stepをtraceから再生するsimulatorを提供。 保留: 高価なGPU trainingのtraceから成る研究で、simulator結果もmeasured cost model/priorsに依存する。現ユーザーのCPU小型モデルと入力前処理には距離がある。
8. **C [mdsubhanpasha/k8s-neural-collapse-manifold](https://github.com/mdsubhanpasha/k8s-neural-collapse-manifold)** — Kubernetes本番障害をNeural manifoldとして分析しcoding-agent学習用SWE-bench風datasetにする、という候補。titleは76% autonomous fix rateを主張する。 保留: 76% fix rateは候補タイトルだけにあり、提供された本文から方法、標本、独立評価の証拠を確認できない。K8s障害特化で適用範囲が狭い。
9. **B [muvon/octobench](https://github.com/Muvon/octobench)** — coding agent構成を共通benchmark caseで比較するharness。real-commit tasks、SWE-bench-Live、複数domainのbenchmarkを扱う。 保留: 多くのbenchmarkの本実行はmodel provider、Docker、データ取得に依存。candidate自身のoctomindも含まれるため、fairness設定や同一条件を確認する必要がある。測定結果はREADME claimのみ。
10. **C [openmoss/swe-bench-science](https://github.com/OpenMOSS/SWE-bench-Science)** — scientific software向けcoding-agent benchmark。科学分野の契約・数値挙動を持つtaskをDocker runnerで評価する。READMEは20 domain、license gate等を説明。 保留: 科学ソフト領域に特化し、20 domain/96 unrestricted tasks等はREADMEの説明。task-level licenseが分かれ、model/providerの実行費も未確認。今回は未実行。
11. **B [paultrust26/trot](https://github.com/paultrust26/TROT)** — coding agentの観測履歴を規則・学習ゲート・選択要約で圧縮し、コンテキスト費用を抑える提案。 保留: SWE-bench解決率・46–55%節約はREADME記載の主張で追試未確認。独立した話題信号が2件確認できず、深掘り分類要件を満たさない。
12. **B [piaoyun2022/coding-harness](https://github.com/piaoyun2022/coding-harness)** — DeepSeekベースの最小coding agentとSWE-bench評価ハーネス。 保留: SWE-benchは最大300件評価を記載するが実測完了を示す証拠とは扱わない。独立した話題信号2件なし。
13. **B [riamunshi/quantized-slm](https://github.com/riamunshi/quantized-SLM)** — 量子化小型LMの推論時手法で推論忠実度を回復する研究コード。 保留: 中心的な学習工程はGPU/Colabと学習済みcheckpointを要する。重み取得条件とダウンロード量不明。
14. **B [shiva-shivanibokka/codepilot-swe](https://github.com/shiva-shivanibokka/CodePilot-SWE)** — SWE-bench Liteでagentとagentlessを比較するcoding agent兼ベンチマークハーネス。 保留: README自身が小規模試験の限界・未提出例を記す。記載の評価値は独立検証ではない。依存する外部モデルの費用が制約。
15. **B [shlokgoel/fixpoint](https://github.com/ShlokGoel/fixpoint)** — Anthropic Messages API上のcoding agentとSWE-bench Lite評価、失敗分類・reward-hacking検出。 保留: 独自性は派生元との比較確認が必要。API費用とモデル条件が不明でゼロ予算適合は未確定。

## 課題・次の手順

Squeezの追加修復はこのrunでは行わない。次に実行する場合は上限超過の記録を引き継ぎ、UID/Inductor cache条件をレビューしてから別途許可された範囲で判断する。Qwen2.5-Coderを最新とはせず、公式 `Qwen/Qwen3-Coder-Next` metadataも保存したが、モデルサイズ・用途・推論費用の適合は未測定。DoubtLessは公式例が80GB GPUとQwen3-32B/0.6Bで、即CPU導入とは扱わない。

BitNetの前日0/24・template0/4は配布物の互換性/adoption holdとして参照し再実行なし。朝のCrawl4AI/Docling/GLiNER2も再実行なし。SKU最新引継ぎのresume-s000 v1・15,616件回収を確認し、ジョブ/branchesを変更していない。PR19は停止のまま。メール/SMTP/Actions cron/merge/deploy/Kaggle操作なし。実用機能を実装していないためPRなし。

## 検証・保存

Skill tests **74成功**。指定lab tests **151件、1失敗・15skip**。既存bot-diagnosticsのrobots-parser未導入が原因でCI保守は行っていない。専用監査はstate全保持、candidate ID重複、review20一致、Schema、入力hash、baseline再計算、保存manifestを検証。

詳細推薦0でもskill直下から `record_run.py <report.json> --state-dir state` を実行。seen/mailを維持し、freshnessに今回を追記、queue5,133件を正規evening-stateへ戻した。保存先branchは `research/2026-10-12-evening-radar`。push後のremote blob一致とCIの正確な状態は会話の最終報告に記載（この文書作成時点では未判定）。

収集23,721件／詳細読解20件／候補推論成功0件／未完了5,133件。
