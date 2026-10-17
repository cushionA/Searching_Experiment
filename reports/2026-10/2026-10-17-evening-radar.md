# 2026-10-17 JST 夕方 Research Repro Radar

結論：実測改善0件。一次資料28候補を読解しA1／B19／C8。pico-typeは限定用途で試す価値、Harness²とSkillOptは条件付き深掘り。Harness²デモは準備段階の権限例外で修復1回の上限に達して停止し、候補コード・モデル推論は未実行。失敗を完了や性能測定として扱っていない。

## 概要・方法

- 最新Skill `55ceed634506c5942d6a8e84c71d7e80acc103ff` の指定8資料は導入コピーとSHA256一致。profile v7（最少40、review_pool20、0円/30分/CPU20分/8GiB/2GB/修復1回/carry28日）を維持。
- 最初からRADAR_LLM=native。指定gpt-6-lunaをホストが受理し、probeの要求ID・タスクSHA256・Schema・期待値すべてexit0。実サービスモデルIDはunknown。43候補判断は28候補の追加資料による再判断を含み、すべてID・hash・Schema・原文引用を検証。引用修正4要求、意味修正2要求は各1回。判定成功は候補実行成功ではない。
- HF trending50件、新着50件、10クエリを先に取得（1,217一意）。MDPI/Crossref/GitHub補助16要求、arXiv5クエリ500レコードを統合し2,512一意。OpenReview5要求は403で停止、迂回なし。返されたページに次リンクなしという範囲で、世界全体の網羅性を主張しない。
- 繰り越し込み6,040件を全件処理、5,186件を採点。review_pool20件全て＋trending5件＋分類/動画/抽出3件を読解。最低件数で探索を打ち切っていない。freshnessは維持し、星0の新規repoの高得点を採用根拠にしない。
- 日付不明239件は原値を残してunknown。`requires_gpu=false`の既定加点はCPU適合の実証ではなく、実行は別途資源/権限で制限する。GitHub created/pushed/updated、HF更新revisionと論文公開日は区別。

## 実測と比較

品質、CPU時間、RSS、スループット、1万件換算はいずれも未測定。品質相対5%向上、または資源30%削減＋品質低下2%以内を支持する自前の比較は0件。著者の速度・精度・人気指標を実測効果へ置き換えない。API/GKE/AWSの金銭比較は行わず、過去の参考単価も利用しない。

**Harness²の簡易確認試行は失敗。** official commit `1cfe254e8d25b15f37048c3730134686abe60335`、package0.1.0、公開tarball7,056,332 bytes、Python公式image（展開サイズ119,431,103 bytes）を用意。固定image digestと42公開ソースのhash、ホスト側wrapperと2試行ログをbenchmarksへ保存した。ホスト側Python3.12.14、コンテナPythonの実バージョンはwrapper停止で未計測。

初回：source rootがumaskで0700になり、uid10001から`/app/run_demo.py`を開けず。公開ソースrootのみ0755へ1回修正。2回目：隔離確認wrapperが読めない`/root/.codex`をstatしてPermissionErrorになり、`run_module`より前に停止。sourceの公式デモ、帳簿2課題、LLMはすべて未実行。修復上限のため別環境・GPU・追加修正へ進んでいない。再開には新たなユーザー継続指示が必要。

隔離条件：network none、rootfs read-only、uid10001、capabilities全削除、no-new-privileges、RAM8GiB、CPU2、pids128、timeout120秒。mountは公開コードread-onlyと空の出力先のみ。認証・私有ファイルを渡していない。GPU0秒、学習0、現金支出0。例外は認証情報を取得したことを意味しない。

## 読解による深掘り候補

### B：Harness²

[論文](https://arxiv.org/abs/2610.14224)／[公式実装](https://github.com/google-research/harness2)。10月9日公開、repo更新10月14日、HF trending4、744 stars/77 forks。モデル重みを変えず、同一タスクで提案・試行・合成し、次タスクへ改善経験を持ち越す二段階の設計。AI開発の繰り返し修正を減らせるかが仮説。HFとGitHubの別基盤の注目はあるが、独立再現ではない。

Apache-2.0（第三者部分は別）、Linux/Python3.12、core依存0。決定論的デモは認証不要だが編集が決め打ちで、成功してもモデル能力の証明にならない。実モデルbenchmarkはNode/Cloud CLI/モデル設定等を要求し、READMEはAPI費用とsandbox無効の既定を明示している。当方はその経路を実行・採用していない。採用には同予算の固定課題で単純ハーネスと成功率・呼出回数を比較する必要がある。現在は上記停止状態。

### B：SkillOpt

[論文](https://arxiv.org/abs/2605.23904)／[公式実装](https://github.com/microsoft/SkillOpt)。5月22日公開、論文v2は5月25日、repo更新10月6日。HF trending37、18,496 stars/1,753 forks、MIT。古さでは除外せず、継続利用のあるスキル改善法として読む。

学習済み重みを変えず、skill文書の編集をheld-out評価で採否する。Harness²のオンライン試行とは違い、事前最適化した文書の再利用が中心。追加推論呼出し不要という記述は**配備時**で、最適化のrollout費用まで0ではない。大量の反復タスクで初期評価費を回収できるかが次の仮説。Python3.10+、pip導入、モデルbackendが必要。現在の無課金経路・呼出上限・具体的RAMは未確認で未実行。実利用スキルとは別の固定公開タスクで、採否用と最終評価用入力を分ける必要がある。

両候補は読解のみなのでreading_deep_dive_pendingに保存し、record_runが自動完了扱いにするrecommendedへ入れない。freshnessの確定推薦数は0件。その他のnative深掘りラベルは自動採用せず、以下の制約付きダイジェストへ留めた。

## 未検証ダイジェスト（新たな確認点）

- **A [pico-type / ARTH V2](https://huggingface.co/eulogik/pico-type)** — 1.5M級のbyte分類器。現行ARTH V2は14種リスク用で、FP32 11,664,968 bytes、実験的INT8 4,277,268 bytesをAPIで確認。一般文書分類の代替ではない。 保留：9 stars・3 likesの小規模ソース。未知ソース実行の事前承認未確認で未実行。まず公開固定入力で単純規則との比較が必要。Aは試す価値であり実行許可・採用ではない。

- **B [LightVAE](https://github.com/ModelTC/LightVAE)** — 10月10日公開、14日重み更新。Wan2.1 Lite decoderは70,875,748 bytes、H3 INT8は833,732,248 bytes。全配布物が2GB超という解釈は誤り。 保留：主評価はGPU。CPU/RAM不足の実証も既存Kaggle無料枠等の確認もなくGPU利用条件未成立。モデルcard Apache-2.0、repoライセンスAPIはnullでコードの利用条件は別確認。

- **B [OpenGCUA](https://github.com/Tongyi-MAI/OpenGCUA)** — 10月15日公開、HF trending32／GitHub56 stars。GUI・コード実行・状態判断をまとめたスタック。現行READMEではGCUAモデル自体は今後公開の項目。 保留：macOS14+実機セットアップとAPIキーを要求。モデルの実在・配布とハーネス公開を混同しない。Linux/無料推論・権限・RAM未確認。

- **B [3DCodeVerse](https://github.com/gaoypeng/3dcodeverse)** — 10月15日公開、HF trending31／63 stars。実行・幾何測定・視覚判定を分けるAI開発の評価設計。code Apache-2.0とdataset非商用CC-BY-NC-SAは別。 保留：Python3.13/Node/ベンダーCLIなどが必要。学習は実行禁止、無課金でのモデル実行経路と資源は未確認。商用データ利用に転用しない。

- **B [Self-Evolving Agent Harness](https://arxiv.org/abs/2610.18813)** — 10月13日公開、HF trending10／145 stars。66頁のsurveyで、固定モデルの周辺状態の更新と改善の実証を区別する。 保留：実行ツールではなく設計・評価の読解資料。CC-BY-4.0。サーベイの分類をそのまま性能向上の証拠にしない。

- **B [SeNER / Small Language Model Makes an Effective Long Text Extractor](https://arxiv.org/abs/2502.07286)** — 2025年2月公開の掘り起こし。長い固有表現のスパン相互作用を扱う。公式repoとbio_modelsの導入資料を照合した。 保留：公式説明はPython3.8・旧CUDA/PyTorch、実行権利・推論専用重み・CPU資源量が未確認。古いことではなく導入条件未確定で保留。

- **C [DM-Code-Agentの追加重複5件](https://github.com/frank6948/DM-Code-Agent)** — 当日確認した8 repoのREADME SHA-256が完全一致。新たに見つかった5コピーも0 stars/forks、NOASSERTION、README内のownerは別。 保留：Harper代表を保持し、追加5件と既に見送った2件だけ理由付き除外。独立した研究・利用実績として重複加算しない。

## ID・利用条件の照合

pico-typeの現行model IDは`eulogik/pico-type`、revision `34b82db0aa1bafd9e63848df73d4ce1c864e475b`、9月26日更新。旧base7-headとARTH V2の14-risk-headを区別。code/model cardはApache-2.0。著者自身が一般分類AG/SST-2/Enronはchance水準と述べ、長文では固定窓の希釈や誤検出が残る。18ms等は著者値。意味分類・秘密情報除去の安全策として単独採用しない。評価するなら、形式/言語を扱う旧baseとリスク専用ARTHを分ける。

LightVAE IDは`lightx2v/LightVAE`、revision `4925ea978147b9de492d7a5fd4f9c6d1854ca1cc`。H3通常decoderは3,248,356,840 bytesで上限超過だが、Wan2.1 Liteは約70.9MB。軽量decoder単体サイズと元encoder・依存・RAMは別。ComfyUI用H3 INT8を通常benchmarkへ代入しない。

Qwen2.5-Coder-1.5B-Instructの再確認ではrevision `2e1fd397ee46e1388853d2af2c993145b0f1098a`、必須FP重み単体`model.safetensors`は3,087,467,144 bytes。repo usedStorageの6GB超を必須downloadとして使う誤読を訂正した。2GB上限内の具体的量子化artifact・後継の用途適合は未確認。既報としてダイジェストを繰り返さない。

## 判定と未確認事項

A=今試す価値（PoC・承認済みを意味しない）、B=条件付き、C=今回の優先度は低い/不要。終端の見送りはREADME重複7IDのみ。各候補の根拠引用、原文hash、ライセンス・導入・リソースのunknown、次の確認点はreviews.jsonとreview-evidence.jsonに保存。

|ID|判定|理由・保留|
|---|---|---|
|2610.20193|B|目的は開発履歴から実行可能なSWE訓練タスクと軌跡を段階的に構築すること。漸進検証と弾力的なストリーミングが新規性だが、記載の成果は大規模データ構築と30B級モデルの調整に依存し、30分・CPU 20分・8GiBでは再現可能な採用確認に合わない。一次論文以外の独立した注目シグナルも提示されていないため、条件付きで保留。小規模な公開成果物とライセンス、CPU資源見積もりが確認できれば再評価する。|
|2610.21496|B|長いリポジトリ作業で文脈情報を行動に反映するため、リマインダー条件付きの軌跡蒸留を行う提案。Global/Turn-local Reminderを使う点は新しいが、効果主張はモデル学習後の比較であり、今回の30分・CPU 20分・8GiB、学習なしの枠では採用を試せない。独立した注目シグナルもなく、蒸留可能な公開成果物とライセンスが確認できるまで条件付き保留。|
|2610.21436|B|モバイルNPUの軽量ドラフターとサーバー側MTPを組み合わせ、推測デコードのサーバー計算量を抑える方式。端末とサーバーの分担が新規だが、Galaxy S26 UltraとLTE/5Gを使う単一クライアント評価であり、今回のローカルCPU・無償・30分の作業には合わない。一次論文以外の独立した注目シグナルもないため、端末/バックエンド要件と公開実装を確認してから条件付き再評価。|
|10.3390/en19204893|B|目的は4系統のLLMについて、GPU推論時の重み量子化とエネルギー・速度の関係を統制比較すること。複数精度・モデルを同一backendで比較した設計は有用だが、単一RTX 4090上のGPU限定測定で、品質も評価していないため、当方のCPU環境や実運用判断へ直接外挿できない。既存の無料GPU利用資格も未確認なので試行は保留し、対象GPU・モデルに合う具体的な評価需要と無料実行条件が確認できた場合に再検討。|
|github:frank6948/dm-code-agent|C|目的はPythonコードエージェントの監査・追跡と決定的評価で、AI開発用途には適合し得る。しかし親確認では、このREADMEは8件すべて同一ハッシュで、保持対象はHarper版と指定されている。このFrank版は独立した実装・新規性を加えず、別候補として採用する増分価値がない。READMEは別所有者hwfengcsを参照し、APIライセンスもNOASSERTION。Frank版は採用対象外とする。|
|github:harper3105/dm-code-agent|B|目的はPythonコードエージェントの実行過程を監査し、失敗診断や隠しテストで検証すること。追跡ログの分岐再実行と決定的評価は特徴的で、API不要の評価入口は20分CPU枠に合いそう。ただしREADMEは別所有者hwfengcsを参照する一方、GitHub APIのライセンス表示はNOASSERTIONで、出所・利用条件が未確認。独立した注目シグナルもないため、出所とライセンスを確認できたら無課金の小規模評価を検討する。|
|github:jasmijnramos37/dm-code-agent|C|目的はPythonコードエージェントの監査・追跡と決定的評価で、AI開発用途には適合し得る。しかし親確認では、このREADMEは8件すべて同一ハッシュで、保持対象はHarper版と指定されている。このJasmijn版は独立した実装・新規性を加えず、別候補として採用する増分価値がない。READMEのclone先はhwfengcsを指し、APIライセンスはNOASSERTION。Jasmijn版は採用対象外とする。|
|github:jasonpolyakov44995/dm-code-agent|C|監査可能なコードエージェントは開発用途に関係するが、検証済みホストメモではREADMEが8リポジトリ間で完全一致し、既存代表候補の重複と確認済み。新規性・追加価値がなく、APIもライセンスをNOASSERTIONとしているため、この複製候補は対象外とする。|
|github:kingjone0/mini-claude-code|B|目的は、Claude Agent SDKでコードエージェントを組み、独立pytest判定・権限境界・traceを検証すること。サンドボックスと反対対照を含む設計が特徴で、READMEは単純な修正靶場での作者実行結果を報告する。ただしClaude SDK/CLI依存のため、0円・有料バックエンドなしの枠で動かせるか不明。無料で互換の実行経路が確認できれば、小規模なオフライン評価を検討する。独立した注目シグナルは確認できない。|
|github:lferrara41942/dm-code-agent|C|監査可能なコードエージェントは開発用途に関係するが、検証済みホストメモではREADMEが8リポジトリ間で完全一致し、代表候補と重複すると確認済み。新規性・追加価値がなく、APIもライセンスをNOASSERTIONとしているため、この複製候補は対象外とする。|
|github:maximmulder1213/dm-code-agent|C|監査可能なコードエージェントは開発用途に関係するが、検証済みホストメモではREADMEが8リポジトリ間で完全一致し、既存代表候補の重複と確認済み。新規性・追加価値がなく、APIもライセンスをNOASSERTIONとしているため、この複製候補は対象外とする。|
|github:mdsubhanpasha/k8s-neural-collapse-manifold|B|Kubernetes障害をコードエージェント向け評価に結ぶ題材は関連するが、既存の評価用途に対する新規性・実用性は未確認。76%等はREADME記載の作者値に限られ、独立した注目シグナルもない。ライセンス不明でデータの利用条件も判断できないため、採用は保留する。|
|github:paultrust26/trot|B|観測履歴を圧縮してエージェントの文脈量を抑える手法は開発エージェントに直結し、README上は決定的ルールと軽量ゲートを組み合わせる点が特徴。ただし公開結果は作者報告のみで、選択的な補助LLMと学習済みゲートは今回の無学習・無課金条件で再現できるか不明。独立シグナルもなく、実装・依存条件を確認してから判断する。|
|github:piaoyun2022/coding-harness|B|最小Coding Agentの文脈管理とパッチ適用は用途に合うが、既存手法との差は証拠から判断できず、採用条件に合わない。READMEはDeepSeek APIキーとDockerを要求し、無課金・無学習の20分CPU枠で評価可能とは確認できない。ライセンスも不明なため、実行条件と利用権が明確になるまで保留する。|
|github:samantha3782/dm-code-agent|C|監査可能なコードエージェントは開発用途に関係するが、検証済みホストメモではREADMEが8リポジトリ間で完全一致し、既存代表候補の重複と確認済み。新規性・追加価値がなく、APIもライセンスをNOASSERTIONとしているため、この複製候補は対象外とする。|
|github:zawadzki2791/dm-code-agent|C|監査可能なコードエージェントは開発用途に関係するが、検証済みホストメモではREADMEが8リポジトリ間で完全一致し、既存代表候補の重複と確認済み。新規性・追加価値がなく、APIもライセンスをNOASSERTIONとしているため、この複製候補は対象外とする。|
|2610.14448|B|目的はRLVR学習時の非同期rolloutをINT4 KV cacheで高速化し、要所をINT8保護して学習精度低下を抑えること。手法は新規性があるが、報告値はQwenモデルを400 iteration訓練した著者結果で、当方の無償・無訓練・短時間枠では再現も導入もできない。論文以外の独立した注目シグナルや実装artifactも提示されず、今の採用根拠にならない。GPU学習基盤と公開コードが揃う場合に再評価。|
|hf:qwen/qwen2.5-coder-1.5b-instruct|B|目的はコード生成・修正向けの指示追従型1.5Bモデルで、CPU開発支援の候補として用途と規模は合う。通常重みはBF16約1.54B parametersだが、必須のmodel.safetensors単体が約3.09GBで2GB download上限を超える。repo総容量を理由にした以前の判断は誤りで、正確な重み容量で判断し直した。2GB以内の互換量子化artifactが確認できれば20分のCPU試行を検討する。|
|2412.14219|C|目的はMoE推論最適化研究をモデル・システム・ハードウェア別に整理するsurvey。分類の新規性は参考になるが、当面の小型CPUモデル評価や開発作業に直接適用する対象がなく、実装・比較artifactも提示 evidenceにはない。実行枠を使う価値はなく、MoE導入案件が具体化しない限り不要。|
|2602.09373|B|目的はアフリカ諸語翻訳向けの剪定・量子化モデルを提供し、低資源環境での推論を可能にすること。対象言語と効率化は独自性がありCPU制約にも方向性は合うが、当面の翻訳ニーズは示されず、提示証拠は論文のみで独立した注目シグナルはない。モデル容量、ライセンス、導入容易性も不明のため、対象言語の具体的需要と資源条件が確認できるまで保留。|
|2610.14224|B|モデル重みを固定したまま、タスク用ハーネスの改良と経験の継続蓄積を二段階で行う枠組みで、AI開発の運用改善に直結する。HF trending rank 4とGitHub 744 starsという別プラットフォームの注目、および論文・公開実装が揃い深掘り価値はある。ただし既存Harness2実行はコード開始前に一修復上限で停止しており、入力でも再試行禁止。読解段階に留め、実行再開は新たなユーザー継続後の判断とする。|
|2610.18813|B|固定モデルの周辺ハーネスを継続更新する研究を整理し、探索理論・評価・安全策まで扱う。自己進化エージェントの設計/運用を理解する読解資料として有用で、HF trending rank 10と別プラットフォームのGitHub 145 starsも注目シグナルになる。一方、これは即時に使う軽量ツールというより66ページの調査・理論資料で、モデル評価やコード実行を伴う採用判断は現在の枠に合わない。読解深掘りに留める。|
|2610.22341|B|目的は、コード生成エージェントに3D成果物を作らせ、実行・幾何測定・描画結果で修正する評価ハーネスとデータ基盤。DAG型の反復検証はAI開発の評価手法として興味深く、Hugging Face trendingとGitHub starsの別プラットフォームで注目があり、論文とコードも提示されているため読解深掘り価値がある。一方、実行にはベンダーCLI、Python 3.13とNodeが必要で、データセットも別条件。20分CPU・無課金バックエンドでの実行適合は未確認なので、今回は読解に留め、導入・費用・データ条件を確認できたら再検討する。性能値は著者報告で未検証。|
|2610.22200|B|目的はGUIとプログラム操作を組み合わせる汎用computer-use agentの公開スタックで、観測や状態判断をモデルへ移す設計が特徴。AI開発・エージェント評価に関連し、Hugging Face trendingとGitHub starsで別々の注目があり、論文と実装があるため読解深掘り価値はある。一方、掲載の実機ハーネスはmacOS 14+が必要で、構成例はAPI keyを要求する。無課金・CPU 20分の実行条件には現状合わないため読解に留め、利用可能な無課金モデル経路と権限・費用条件を確認できたら再検討する。性能値は著者報告。|
|2605.23904|B|目的は、固定モデルに対するagent skill文書を評価ロールアウトで反復最適化し、検証スコアが上がる変更だけ採用すること。重み学習を伴わず、Codexを含む実行環境へ移植する報告はAI開発のskill運用に直接関係する。Hugging Face trendingとGitHub starsの独立シグナルがあり、論文と実装もあるため読解深掘り価値が高い。ただし最適化時のモデル呼び出し・費用と利用可能な無課金経路が不明なので、0円条件を満たす構成が確認できれば検討する。数値は著者報告で未検証。|
|2608.14658|A|生UTF-8バイトから言語・形式・リスクなど複数の属性を分類する小型モデルで、コード分類や抽出前の振り分けに使える。ONNX単体は約9MB、学習なしのCPU利用を想定した公開物があり、30分・2GB以内で試す候補として適合する。ただし一般テキスト分類は作者記載で弱く、性能値は未検証なので、用途を形式/コード分類に絞りApache 2.0の範囲で評価する。|
|2610.14726|B|目的は動画生成のvideo VAEを圧縮し、復元品質を保ちながらデコードを効率化すること。時間・深さと空間・時間カーネルを分けた設計に新規性があり、論文に加えてGitHub 30 starsとHF 819 downloads/33 likesという別基盤の活動シグナル、コードと重みが示されているため深掘り価値はある。ただし効果はH100/RTX 5090等での著者報告で、動画VAE作業にはGPUが必要。手元CPU/RAMで不足する具体的需要と無料GPU枠のquota・認証・プライバシーは未確認なので、試行は条件確認後。|
|2502.07286|B|長文からaward等の長い固有表現を抽出するSeNERで、長文抽出用途には合う。BiSPAで候補スパン間の計算重複を減らす点が新しいが、提供リポジトリの導入記載はCUDA/PyTorch前提で、データ・事前学習モデルの取得条件も今回のCPU/2GB枠に適合するか分からない。権利とCPU対応を確認できれば再評価する。|

## 継続・次回

正規branch `research/2026-10-12-evening-radar@1fd340259b665e953f174ac3e3706ff6f42cf582` を開始時にfetch確認。独立研究branchから進め、朝branchのremoteは`0c6ddaa7b3d82eff6b0e2a889f76f99009aeac63`と一致。朝YOLO26n停止、Squeez停止、BitNet互換性hold、PP-LCNet停止を尊重し再試行していない。SKU、WorkAssistantClaude、別プロジェクト、PR19、メール/SMTP/cron/merge/deployに変更なし。

開始未完了5,176件。採点後5,186件に順位外7件を復元して5,193件。重複7IDを明示的に見送り、record_run後は5186件。以前の1,072件の保持を検算する。seen/mailを維持し、freshnessへ今回runを1件追加。Skill直下でrank_candidatesとrecord_runをともに--state-dir stateで実行し、4JSONを正規evening-stateへ戻す。

新規実装機能はなくdraft PRは作らない。保存commit・remote exact file hash・そのcommitのCI結果は最終返信に記載し、未確認で成功を先取りしない。CI成功はこの候補デモの成功を意味しない。

次回はpico-typeのソース承認と用途別公開fixtureを具体化する。Harness²の再実行は新たなユーザー継続指示まで停止。今回の準備失敗を理由にKaggleや別環境へ移さない。モデル/code実行0・収集2,512・一次読解28・未完了5186。
