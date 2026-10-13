# 夕方Radar native実試験 — 2026-10-13

## 結論

**native判定経路は、実probeと保存済み実候補1件の判定まで成功した。** 親ホストのサブエージェントを実際に2回起動し、両方の応答でrequest ID・task hash・schema検証と `llm.judge_with_diagnostics()` のdict返却を確認。モック応答や親による正常値の補完ではない。

SWE-Pruner（2601.16746）の判定は **defer / reading / 推薦なし**。候補モデル推論は0件。配布artifact、モデルID/revision、ライセンス、実行資源が未確認のため今回は試さない。未完了1,057件を全保持し、保留結果と今回run実績だけ記録した。

## 概要・認可・版

前runの「nativeは確認待ち」は、親からの制約訂正で解消した。ユーザーの最新版再導入・試運転依頼の範囲内で既存ホストnativeを通常使用するものと確認。今回は開始時から `RADAR_LLM=native` を明示した独立run。CLI失敗からの自動fallbackではなく、CLI呼出し0、認証/権限/有料API変更0。

導入Skillは `cushionA/zabuton_skills@55ceed634506c5942d6a8e84c71d7e80acc103ff`。25ファイルのsource SHA256一致を再確認。同じ版で直前に成功した付属74テストのログを参照する。これらはunit/fixture試験であり、今回の実サブエージェント証拠とは別。

ホストの `collaboration.spawn_agent` に **model=gpt-6-luna、fork_turns=none** を明示し、probeとcandidateそれぞれ受理された。agent/task IDを台帳へ保存。これはホストの呼出し設定による確認であり、サブエージェントの自己申告ではない。provider側の実serving model ID/revisionはツールから返らず **unknown**。その独立同定までは完了していない。

Python3.12.14、jsonschema4.26.0。正規夕方branch開始SHA `0a5713d0173e3d38f08d7a10b298698bee42148e`。

## 方法

最新版platform/native.mdどおり、private temporary directoryにprepare要求を作成。専用サブエージェントへ要求JSONと新しい応答パスだけを渡し、外部検索・候補コード実行・state/認証アクセス・子CLI・追加agentを禁止した。agentの最終終了通知を受けてからvalidateを行い、同一要求/応答をPython judge APIにも渡した。各180秒期限内、応答修復0。

candidateには2026-10-13に保存したarXiv一次HTML、当日review、profile v7と未測定事項を渡した。対象はcoding-agent文脈圧縮のSWE-Prunerで、検索rerank、SKU、朝の調査、停止中PP-LCNetとは別。新規収集は0。保存資料だけの判断で、最新の後継調査を済ませたとは扱わない。

## 結果

| 段階 | 実結果 |
|---|---|
| native probe | ok=true / note=ready、validate exit0、judge API complete |
| probe時間 | 14:02:59〜14:03:55 UTC、親の観測区間56秒以内 |
| 保存済み候補判定 | candidate_id=2601.16746、defer、reading、recommended_category=null |
| candidate時間 | 14:04:30〜14:05:59 UTC、親の観測区間89秒以内 |
| 引用照合 | 2件とも保存HTMLのタグ除去・entity展開・空白正規化後テキストと一致 |
| 実候補モデル推論 | 0件、artifact/license/resources未確認で保留 |
| record_run | seen追加0、completed削除0、freshness推薦分母0 |
| state | queue/seen/mailはbyte単位で不変、freshnessに今回run1件のみ追加 |
| 再読込 | 公式load_queueで1,057件、重複0、runtimeと正規4JSON一致 |

最初の引用照合では、HTMLタグを含む生文字列への単純substring判定が1件失敗した。同じ引用を変更せず表示テキストへ正規化すると一致した。schema失敗や応答修復ではない。

native回答は雑なスクレイピングデータへの適合不明にも言及したが、親は**coding-agentもprofile独立関心である**ことを確認。今回の保留理由はartifact/ライセンス/資源の不足であり、全候補にスクレイピング用途一致を必須としてはいない。測定値を捏造しておらず、著者の圧縮効果を本環境の実測にしない。

## 比較・問題・導入判断

以前のCodex CLIはread-only/appserverエラーで有効応答0。nativeでは子CLIの初期化を使わず実応答が得られた。前回診断のCODEX_HOME read-only、installation_id無しを変更する必要はなかった。CLIそのものの復旧は主張しない。

**復旧範囲は手動ホストのnative判定経路。** 既存collectorの `judge_preflight.py` はcodex固定のままで、単独Python/cron全体のnative移行や定期実行は未検証。今回収集しないためcollector gateを実行していない。スケジュール、メール、課金設定は変更なし。

record_runは一度repo rootで呼びパス不在exit2となった。state変更前にSkill直下へcwdを訂正し、正式コマンドが成功した。モデルやnative応答の修復とは区別して記録した。

推薦0、completed_ids=[]。deferを完了へ変えない。現金0、候補download0、GPU0秒、外部候補コード実行0。今回の1件以上へjudge範囲を広げず終了。次はSWE-Prunerの公式モデルとライセンス・revision・後継・資源を確認してから短い実験を検討する。モデル推論未実施のため速度/RAM/品質/クラウド費用比較は未測定。

## 再現・保存

- code: 導入Skill `scripts/native_judge.py`、`scripts/llm.py`、`scripts/record_run.py`。
- `experiments/research-repro-radar/runs/2026-10-13-native-v3/`: report.json、provenance.json、hash manifest。
- `benchmarks/research-repro-radar/2026-10-13-native-v3/`: native-ledger.json（実host task ID、時刻、要求/応答hash、model設定、exit）、candidate-validation.json、record-run.json、state-verification.json。
- 生要求・応答はprivate一時領域に保持し公開Gitへ載せない。元公開資料は既存runでSHA固定。厳密な生応答の遠隔再取得は対象外で、公開台帳のhashと判定要約を保存する。
- probe: `RADAR_LLM=native python -B scripts/native_judge.py prepare --purpose probe --request-file <新規一時領域>/probe.request.json` → ホストspawn(model=gpt-6-luna) → agent終了確認 → `native_judge.py validate --request-file ... --response-file ...`。
- candidate: 保存済み2601.16746一次資料とprofileをpayload/instructionにし、`references/native-candidate.schema.json` 全文で同じprepare/spawn/validate。
- state: Skill直下で `RADAR_LLM=native python -B scripts/record_run.py <run/report.json> --state-dir state --today 2026-10-13`、4JSONを正規夕方stateへ同期。

実native判定2回（probe1＋候補1）／候補モデル実測0／完了0／未完了1,057。
