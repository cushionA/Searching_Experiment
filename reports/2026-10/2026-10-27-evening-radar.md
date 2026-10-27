# 2026-10-27 夕方レーダー

**小型decision modelの推論は未完了。TinyJev-0.6Bの実行準備まで進めたが依存導入で停止した。** 別途、gameAI向けAutoTavernの公開回答368件を隔離環境で再採点できた。これは採点器の実行確認であり、モデル推論成功ではない。現金支出0、学習なし。

## 実際に進めた確認

### TinyJev-0.6B — A：試す価値、実測待ち

[公式コード](https://github.com/ankit-aglawe/tinyjev)、[モデル](https://huggingface.co/AnkitAI/TinyJev-0.6B)。596,049,920 parameters、モデル本体1,192,132,928 bytesとhead2,099,536 bytes。モデルrevision `c559c2f7ea95069f92af8b999f512345bbb87d22`、コード `fec868b6b44ac5fddac64ff87d318ccceaff27ea`。MITの配布物で、基盤Qwen3-0.6B-BaseはApache-2.0。今回の小型版と4B v2は別モデルで、4B v2最小確認済みQ4_K_Mも2,708,804,000 bytesありDL2GB枠を超える。

固定合成入力48件と独立正解を推論前に作成した。NLPは4分類×3文×clean/noisyの24件。gameAIはhealth/enemy/ammoによる4行動をstructured/proseで24件。ゲーム方針はモデルにも明示し、選択肢順序も固定した。正解はモデル出力から作っていない。ベースラインはNLPの単純な語句照合とgameAIの明示ルール。48/48正解だが、この小さく単純な合成集合の性質であり実用精度ではない。モデル出力がないため品質差、推論時間、RAM、1万件換算は**未測定**。

隔離を現在の環境で検査：bwrapはuid mapがread-onlyで失敗。Docker 28.4.0は公式Python 3.12-slimでnetwork none/read-only/cap-drop/no-new-privilegesの最小実行に成功し、通常workspaceを見せなかった。PyPIに0.2.0がなく、公開公式sourceへ1回切替。その後、同じ隔離条件のoffline wheel導入が `Permission denied: /wheels`、exit1で停止した。修復上限1に達したため、この候補の追加修復・再実行・Kaggle再投入はしない。これは自動承認レビューによる拒否ではなく、実際のローカル導入エラーである。

取得済みモデル関連1,205,669,192 bytes、wheel226,060,045 bytes。CPU実行時は公式backendがfloat32を選び、MLX限定のquantize指定をCPU量子化として使わない。著者READMEにも構造化数値のgameAIでは入力変化に反応しにくいという注意があり、NLPと行動選択を別に評価する必要がある。次は、追加修復が別途許可された場合に限り、固定済み48件を変更せず比較する。

### AutoTavern — A：gameAIの評価材料、採点器のみ実行確認

[論文](https://arxiv.org/abs/2610.33845)、[公式コード](https://github.com/MCG-NJU/AutoTavern)。commit `fb61201a557ddd36a411cf44aac51ce4d9b606ce`。960課題から公開eval368件、保存済み4システムの回答を公式scorerで再採点した。候補コード・公開データだけを専用tarで空の一時領域へ送り、network none/read-only/1GB/2CPU、認証・通常workspaceを渡さず実行した。TinyJevの修復再試行ではなく、別候補の公開評価資料監査である。

保存済み回答のplay scoreはTavernAgent 0.9144、GPT-6 Astra 0.6316、Claude Opus5.5 0.5188、Jev 0.3700、chance 0.2182。**これらは著者の保存回答を再採点した値で、当方が各モデルを動かした精度ではない。** 独立に書いたexact-label採点でも232件中の正解数を集計した（201/161/140/76）。公式play scoreはscenario平均なので、このmicro accuracyと直接混ぜない。

監査処理自体は0.181秒、子scorer peak RSS19,772KiB。これは採点処理の資源であり推論資源ではない。公開されたTavernGym/TavernBenchはMITだが、ゲーム内容の第三者権利は別に残る。学習済みTavernAgentの重み・学習コード・課題生成器は配布対象外。モデルの強さを再現したとはいえない。今後のJevクローン比較には、合成ルール課題に加え、合法手・時間制約を持つ公開gameAI課題を使う具体的な材料になる。

## 未検証ダイジェスト

Aは「試す価値」で、PoC完了を条件にしていない。profileの独立話題性・効果の採択条件を満たした詳細推薦は今回0件。以下をdigestだけで完了扱いにしない。

- **A — [TinyJev-0.6B](https://huggingface.co/AnkitAI/TinyJev-0.6B)**：CPU用小型decision model。上記48件比較を準備したが導入で停止、追加修復待ち。
- **A — [Kev](https://github.com/jaredpalmer/kev)**：0.8B/4B/9B/27Bの実在を確認。0.8B adapter約43MBだけでは動かず、base約1.747GBが別途必要。今回の残DL枠・導入確認不足で保留。27Bは約51GBで枠外。
- **A — [AutoTavern](https://arxiv.org/abs/2610.33845)**：gameAIの固定公開課題とscorerが有用。公開回答監査のみ完了、学習済みモデル推論は未実施。
- **B — [D1-omni-600M](https://huggingface.co/LiquidAI/d1-omni-600M)**：587M、テキスト・画像・音声をtyped decisionへ。FP32本体2.349GBで枠外。公式GGUF Q8は本体407MB、別mmproj263MB。Transformers経路は5.15以上と独自コードを要求。LFM licenseの商用売上条件を確認してから導入判断する。作者は速度値をまだ公表していない。
- **B — [AnyJev](https://github.com/nokia-applied-research/AnyJev)**：無学習L0の選択肢位置補正と、学習済みTacit系は別経路。2B/4B/8B/9B等の記載を確認。複数rotationの計算量と対象ドメインでの較正確認が必要で、返却確率を普遍的信頼度とみなさない。
- **B — [Von](https://github.com/wfzyx/von)**：公式READMEはarchived、更新停止、D1を案内。395Mでも標準取得約3GB、英語限定・領域外のconfidence gateに注意。D1との互換性は未検証。
- **B — [Beyond Benchmarks](https://arxiv.org/abs/2610.34241)**：小型workerへのcoding委譲で、独立行動検査・protocol・cache条件を分ける実務報告。著者の観測研究でモデルランキングではない。今回の実験済みとは扱わない。
- **B — [Skill∞](https://arxiv.org/abs/2610.35434)**：task skillだけでなくmeta-skillの更新を再利用する提案。現行self-improvement Skillとの具体的差分を未照合のため一般的な分解・レビュー機構だけで推薦しない。
- **B — [AgentProofBench](https://arxiv.org/abs/2610.34634)**：固定受入ルールを変化する要求・同一開始状態で評価。既存の検証Skillへの増分と配布・license確認が必要。

## 方法、状態、制限

上流Skill `fe6925cd75838a9b52130f284cb3b931bfaf36db` の指定文書を読了。開始から `RADAR_LLM=native` を明示。ホスト指定`gpt-6-luna`の実probeを呼び、request ID `9941f4c2-957b-48d9-81a8-8ffb28570cab`、入力hash、schema、期待値の検証exit0後に探索開始。実サービスモデルIDは取得できず`unknown`。CLI/API fallback・認証変更なし。最初のprepareは導入コピーのstate.py不足で失敗し、そのファイル取得後にprepare成功した。probe応答は親が生成していない。

HF trending全返却50件、新着50件、10検索から1,224一意候補。MDPI2誌・Crossref5検索・GitHub4検索・arXiv5検索で補完。arXivは41ページを取得。OpenReview5要求はHTTP403。GitHubの同一検索でtotal_countとページ間に不整合があり、完全取得と主張しない。Crossrefは各100件の関連度窓、HFの検索backend上限は未確認。40件で打切らず、取得した全候補を保存・採点した。候補の記述を一次資料に基づき正規化した履歴も保存している。

収集5,710一意ID、繰越込み9,522入力を全件採点。ranked8,114、invalid0、最終review_pool20は追加読解を含め全て確認。29件の実質読解（モデル系列の容量metadata照合は別）。native判定の引用は原文照合し、2要求の引用だけ各1回、同一180秒内に修正。nativeの推薦候補のうち独立2信号を裏付けられないものは親側gateで推薦に採択しなかった。古さだけでは除外しない。

正本`research/2026-10-12-evening-radar@9a051ad18a16325dc3420f4067036e9649c47456`から4JSONをSkill-local `state/`へhash一致で復元。最終採点は元snapshotからやり直して中間順位のcarry増分を積み重ねず、`rank_candidates.py --state-dir state`と`record_run.py report.json --state-dir state`を使う。旧未完了9,384件と元carry日付を保持。未検証やdigestを完了にせず`recommended/completed_ids`は空。朝/weekly/SKU/Moli/PR19、SMTP/email/cron/automationは未変更。

Kaggleは最新版Skill/helperを読んだがdoctorがPackageNotFoundError。quota未取得、submit0。TinyJevの修復上限をKaggleで迂回していない。現在の実行条件を調べた結果であり、過去の隔離問題を一律理由にしたものではない。候補推論0、独立した採点器実行1、TinyJev準備失敗1。実用機能を完成させていないためdraft PRなし、merge/deployなし。価格・クラウド費用優位の断定なし。

証拠：[実行台帳](../../experiments/research-repro-radar/runs/2026-10-27-evening/start-manifest.json)、[判定台帳](../../experiments/research-repro-radar/runs/2026-10-27-evening/native-host-ledger.json)、[読解](../../experiments/research-repro-radar/runs/2026-10-27-evening/reviews.json)、[TinyJev停止記録](../../experiments/research-repro-radar/runs/2026-10-27-evening/tinyjev-trial-attempt.json)、[再採点出力](../../benchmarks/research-repro-radar/2026-10-27-evening/autotavern_audit_result.json)、[固定入力と採点コード](../../benchmarks/research-repro-radar/2026-10-27-evening/tinyjev/eval/)。remote SHA・保存後state照合・exact-SHA CIは会話の最終結果に添える。

検証：state保持・元carry日付・固定入力hash・最終pool読解・scorer成果物の検算は成功。repo指定labテスト151件は1失敗/15skip（既存bot-diagnosticsのrobots-parser不足）。対象外の依存修復はしない。

収集5,710／読解29（最終pool20含む）／モデル推論0／採点器監査1／モデル準備失敗1／未完了9,467（旧9,384を全保持、新規83）。
