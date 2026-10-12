# 夕方レーダー試運転 — 2026-10-12 UTC / 2026-10-13 JST

## 結論

Skill最新版の導入、実候補収集、全件採点、一次資料20件の読解、夕方専用状態の保存・再読込を実施した。**候補モデルの推論と必須Codex judgeは環境エラーで未完了**。推論0件を成功と数えない。候補673件のうち444件が採点上の報告対象、229件は関心/話題性ゲートで対象外。ダイジェスト15件、未完了キュー444件。確定推奨は0件とし、以下のA/B/Cは一次資料に基づく暫定の調査優先度である。AにPoC完了を要求してはいない。

すぐに次の確認をする価値があるのは、画像分類のCPU基準としての **PP-LCNet**、生成が必要な短文処理の **Return of the Encoder**、AI開発の評価設計としての **SWE-TaskFlow**。コード観測圧縮の **Squeez / SWE-Pruner** は単純な行削除・既存要約に勝つかを同じ条件で調べたい。GLiNER2は既往研究があるため、既存分類実験の再実行を今回の新規成果にしない。

一次資料照合は実際に役立った。HF metadata の `2505.03113` はsurveyに無関係なCrossViTを関連コードとして提示し、PP-LCNetには第三者実装が付いていた。`2409.01990` は題名が原論文と一致しなかった。機械スコアの実装有無やCPU加点を、そのまま実行可否に使うべきではない。

## 更新前後と採用profile

最新main: [7abebd12df90dbce8eabf741384a52a171dd1799](https://github.com/cushionA/zabuton_skills/commit/7abebd12df90dbce8eabf741384a52a171dd1799)。この環境に旧Skillはなかったため初回導入。`.agents/skills/research-repro-radar/` の20ファイルをupstreamとGit blob単位で一致確認した。元Skillの改造はしていない。

前のmain `fc0f3796` との更新点は、未完了候補を完了記録まで保持すること、JSON Schemaによるjudge応答検証、freshness/送信記録の整合性など。付属テストも追加されている。upstream workflowの変更はこのrepoへ取り込んでいない。

| 項目 | 前回ユーザー確認値 | 今回採用 |
|---|---:|---:|
| profile | v7 | v7 |
| 最低探索 / review pool | 40 / 20 | 40 / 20 |
| run / 候補CPU時間 | 30分 / 20分 | 同じ |
| RAM / download | 8GB / 2GB | 同じ |
| 自動修復 / carry | 1回 / 28日 | 同じ |
| GPU / 現金支出 | 無効 / 0円 | 同じ |

制約の拡大なし。profileのemail指定だけはユーザーの今回指示を優先し、メール送信禁止・親会話報告を運用overrideにした。朝07:00、夕方18:00の既存スケジュール、cron、SMTPは変更していない。正規stateは Searching_Experiment の専用ブランチに置き、朝やzabuton_skills weekly ci-stateと分離。

## 技術概要・既存手法比較・導入判断

以下の数字はすべて**著者報告**で、今回の実測ではない。ライセンスはrepoに関する確認であり、個別重み・データの利用条件は別途確認する。

### A: PP-LCNet — CPU画像分類の基準

CPU/MKLDNN向けに畳み込み構成を選んだ小型画像ネットワーク。公式実装は [PaddleClas](https://github.com/PaddlePaddle/PaddleClas)、Apache-2.0。[原論文](https://arxiv.org/abs/2109.15099) はCPU上の精度/レイテンシの両立を狙う。小型CNN基準として扱いやすく、大量画像の低単価処理に適合する可能性がある。

今回、0.5x公式重み7,553,598 bytes、公式アーキテクチャ、公開の犬画像を取得。推論環境の準備が失敗し、分類結果・latency・RAMは未測定。公式コードをホストでimportしていない。次は同じCPU/runtime/前処理/threadsで既存画像分類器と比較し、独立したラベル付き入力のAccuracy、劣化画像の差、batch throughputを測る。小さい重みだけを安価さの証明にしない。

### A: Return of the Encoder — 小型text2textの比較

[原論文](https://arxiv.org/abs/2501.16273) は小型モデルのencoder-decoder構成をdecoder-onlyと比較し、edge上で初回token latencyを47%削減、throughputを4.7倍と報告。教師からの蒸留による改善は最大6平均performance points。[Microsoft公式repo](https://github.com/microsoft/encoder-decoder-slm) はMITで、330MモデルのCPU latency 1591msを別途掲載する。

入力理解を一度行い、短い出力を生成する構造は、長い雑入力から短い要約・抽出出力を得る処理に合い得る。一方、固定ラベル分類ならencoder分類器や単純な線形分類器との比較が先。1591msの入力長/出力長/runtime条件を揃えず、1万件コストへ直結させない。次は単一のtext2textタスクで品質・load時間・RSS・出力長込みCPU秒数を比較する。

### A: SWE-TaskFlow — AI開発の評価方法

[原論文](https://arxiv.org/abs/2610.09633) は4,782件のIDE利用セッションを調べ、質問・計画・レビュー・修正などの遷移を評価対象へ入れる。700件のSWE-Bench Pro pilotでは、逐次的な指示分割でコストが約2倍になっても解決率は安定して改善しなかったと報告する。

issue修正だけの指標から、実際の利用フローに合う評価へ広げる点が有用。新しいagentを導入する前に、小さな固定タスク1〜3件でコード質問・レビュー・修正を区別し、テスト通過、手戻り、時間、呼出コストを同条件で記録する。今回はSKU/Claude/Codex/PR26へ適用・介入していない。

### B: Squeez / SWE-Pruner — コード観測の圧縮

[Squeez](https://arxiv.org/abs/2604.04979) はタスクに必要な原文ブロックを抽出し、2Bモデルでrecall 0.86、F1 0.80、入力token92%削減を報告。[公式repo](https://github.com/KRLabsOrg/squeez) はApache-2.0で、小型ModernBERTの抽出型選択肢もあるが、同等品質とはまだ確認していない。

[SWE-Pruner](https://arxiv.org/abs/2601.16746) は0.6B skimmerで重要行を選び、agentタスクのtokenを23〜54%削減と報告。[公式repo](https://github.com/Ayanami1314/swe-pruner) はMIT。再現スクリプトは4GPU以上を推奨するが、これは学習/評価手順の条件でありCPU推論が不可能という証明ではない。

比較相手は単純な行削除、切詰め、現行要約。[Harness設計の実証研究](https://arxiv.org/abs/2609.20804) が示す「規則的削除→要約」の基準をまず置く。token削減だけでは、証拠の取りこぼしや追加CPUコストを正当化できない。次は保存済み公開軌跡のoffline replayで、必要行のrecall・タスク成功・overflow・CPU時間を比較する。検索サービスの再順位付け、PR19 mLateOn検索追加は提案していない。

### B: GLiNER2 — 既往との差分がある場合のみ

[原論文](https://arxiv.org/abs/2507.18546) と [公式repo](https://github.com/fastino-ai/GLiNER2) は、NER・分類・階層的構造化抽出を統一encoderとschemaで扱う。repoはApache-2.0。短い出力とCPU処理を重視する用途には合うが、既存のGLiNER2分類研究と重なる。今回の採用・実行はなし。未評価の抽出schemaがある場合だけ、雑入力10〜30件のfield/span F1と出力形式破綻を確認する。

### C: 現在の制約で進めないもの

VisionWeaveは大規模MLLMの画像token節約で、今回のCPU小型分類とは条件が違う。Edge0の3GiBはactive memoryであり、35Bの4-bit重みは19.5GBと原文にあり、download2GB制約を満たさない。GEB-1.3BやLiteGUIは今回の用途に対する測定・公式artifact・資源条件が不足している。公開年齢だけを却下理由にしてはいない。

## 検証方法・結果・エラー

1. HF trending全50、新着50、分類・抽出・小型CPU・軽量画像・coding agentの5検索（120/98/120/120/120）を取得。7応答の重複を除いて673件。40件に達した時点で取得を打ち切らず、取得応答全体を採点した。APIにnext Linkがなく、その検索backendの返却上限以遠は未調査。
2. 全673件を最新版 `rank_candidates.py` へ入力。444 ranked / 229 rejected / review20 / scheduled1 / deferred443。**scheduled1は実行予定にすぎず、実測1件ではない。** 未確認の資源は外部実行ゲートで止める。CPU20分は最大確認枠で、測定済み所要時間ではない。
3. 上位20のarXiv一次ページと6公式READMEを取得、URL・sha256・取得bytesを保存。読解表は `reviews.json`。取得時の不正確なmetadataを監査可能にするため、原データと初回順位は保持。
4. `RADAR_LLM=codex`、既存ChatGPT login、`llm.judge(... model=gpt-6-luna)` を試したが、read-only filesystemでin-process app-serverの初期化に失敗しnull。診断を含め2呼出しともモデル出力なし。有料APIへ迂回せず、新規資格情報設定なし。Lunaサブエージェントのread-only調査メモは最終judgeの代用にしていない。native指定モデルの独立runtime確認はしていない。
5. PP-LCNetの初期隔離案bwrapはuid mapのread-onlyエラー。Dockerは28.4.0が利用可能で、Python公式imageを取得。Docker内PyPIのTLSエラーを1回だけ修復し、host既存の検証済みTLS経路でbinary wheelsのみ取得した。その後buildxが `/home/agent/.docker/buildx` への書込で停止。追加修復せず**推論0件**。認証をコンテナへ渡していない。候補コード・重みは実行前の状態にとどまる。
6. Skillの24テスト成功。実stateで444件の持越し復元、673件分の再発見重複抑制、同run_idのfreshness冪等性、正規state不変を確認。既報抑制/完了削除の合成fixtureは実候補数へ含めない。
7. repo規約のlabテスト151件は1失敗・15skip。失敗は既存bot-diagnosticsテストの `robots-parser` 不足。Skillテストやstate検証の失敗と混同せず、通常のCI保守へ範囲を広げていない。

記録したバージョンはPython3.12.14、Codex CLI0.159.0-alpha.3、jsonschema4.26.0。Paddle3.2.2、Pillow11.3.0はwheel取得のみ。ダウンロードの保守的計上は355,495,342 bytes（wheel・model入力・非圧縮base imageの合算、通信overheadは未計測）。実験用ファイルには依存ハッシュとimage digestを含む。現金支出0、GPU使用0、外部メール0。

候補CPU時間・peak RAM・推論throughput・精度・1万件実測換算は未測定。未実行コードの予定値で埋めない。profileの参考値 `$6.25/1万件`、CPU `$0.145/h`、15.5秒/件の分岐は過去の前提であり、現行価格を再検証していない。今回は採用・費用優位の決定に使用せず、今後の品質と実測資源比較の基準案に留める。

## ダイジェスト（初回採点上位15）

いずれも推論効果・再現性は未検証。titleやrepoが訂正されたものは一次資料の題名を使う。延期理由はjudge利用不能と未実行で、個別制約は追記した。

1. [Lightweight ViT Survey](https://arxiv.org/abs/2505.03113) — 画像軽量化/蒸留との一致。surveyでありモデル実行対象ではない。
2. [VisionWeave](https://arxiv.org/abs/2610.07987) — 新しい視覚token効率化。大規模GPU条件のため保留。
3. [Squeez](https://arxiv.org/abs/2604.04979) — code観測圧縮と公式実装。CPU資源確認待ち。
4. [SWE-Pruner](https://arxiv.org/abs/2601.16746) — 0.6Bの重要行抽出。GPU再現手順とCPU推論を分けて確認待ち。
5. [DeepCode](https://arxiv.org/abs/2512.07921) — paper-to-codeと公式実装。LLM依存・費用・隔離未確認。
6. [EfficientLLM](https://arxiv.org/abs/2505.13840) — 量子化の品質/資源tradeoff。GPUクラスタ評価のためCPUへ転用未確認。
7. [GLiNER multi-task](https://arxiv.org/abs/2406.12925) — 軽量encoderによる抽出。checkpointと既往研究との差分待ち。
8. [Return of the Encoder](https://arxiv.org/abs/2501.16273) — 小型text2textのCPU比較。再試験用モデル・測定条件の固定待ち。
9. [Efficient Foundation Models Survey](https://arxiv.org/abs/2409.01990) — 圧縮設計の整理。元metadataの題名誤対応を確認。
10. [SynthSelf](https://arxiv.org/abs/2610.13113) — 新しい小型モデル継続学習。公式artifact・訓練資源未確認。
11. [Edge0](https://arxiv.org/abs/2609.18063) — HF trending掲載のMoE/SSD推論。35B重み19.5GBでdownload超過。
12. [FP8 Post-training Quantization](https://arxiv.org/abs/2309.14592) — 75アーキテクチャの低精度比較。公式はIntel Neural Compressor、CPU加速未確認。
13. [GLiNER2](https://arxiv.org/abs/2507.18546) — schema抽出/分類と公式コード。既往との差分待ち。
14. [SWE-TaskFlow](https://arxiv.org/abs/2610.09633) — 新しいagent評価設計。評価用軌跡の対象定義待ち。
15. [LSNet](https://arxiv.org/abs/2503.23135) — 軽量画像認識と公式実装。GPU throughputをCPU性能へ転用しない。

今回収集673件、一次読解20件、候補コード実行0件、未完了444件。追加の画像CPU候補PP-LCNetは詳細読解20件に含まれ、実行準備を試みたが推論に到達していない。

## 保存・次に試すこと

正規state: `research/2026-10-12-evening-radar` ブランチの `experiments/research-repro-radar/evening-state/`。初回としてseen/queue/mail/freshnessを保存。`record_run.py` を実行し、確定推奨・completed_idsが空のため未検証444件を保持。mailは空のまま。既報抑制の仕組み自体はfixtureで確認したが、今回未確定の候補を「既報」として隠していない。

次はCodex judgeのread-only初期化を解決し、既存認証のまま同じ経路で検証すること。PP-LCNetの追加修復は今回の1回上限を超えるため、このrunでは行わない。再開時に書込可能なDocker build設定先を確保し、network none・非root・read-only root・資格情報非共有の実行条件を確認してから、保存済み入力/コード/hashで簡易確認する。状態を初期化して持越しを捨てない。

実用機能が完成したとは判断していないためdraft PRは作らず、研究成果と未完了stateを独立ブランチへ保存する。merge/deployなし。
