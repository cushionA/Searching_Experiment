# 夕方Radar 最新版再導入 v2 — 2026-10-13

## 結論

**最新版55ceed6の再導入と74テストは成功。Codex CLIは未復旧。新設native経路の実試験はbackend制約の確認待ち。** CLIの失敗をnativeの失敗と混同しない。候補モデル推論0、実候補judge0、未完了1,057件は全件維持。

## 概要・変更

上流main `55ceed634506c5942d6a8e84c71d7e80acc103ff` は前回f986e0cから2コミット、9ファイル1,280行追加/16行削除。修正本体a06beeaとPR6 merge。コード・参照・テスト25ファイルを再導入し全SHA256一致、9ファイルが更新/追加された。profile v7、正規state4JSONは変更なし。

新修正は `RADAR_LLM=native` を追加し、親ホストのサブエージェントが判定してPythonがrequest ID・task hash・schema・probe値を検証する方式。子Codex CLIの初期化書込を直す変更ではない。認証コピー、権限緩和、有料APIの追加を要求するコードは導入差分に見られない。

ただし今回の委譲指示は「別backend…の追加承認ではありません」と明記した。明示的制約をSkillで上書きできないため、nativeへの切替を実行せず確認を求めた。許可済みCodex CLI経路を1回だけ実確認した。自動承認レビューによる拒否ではない。

## 方法

同じ実行環境・夕方worktreeで最新remoteをfetch。開始SHAは `da508f875578ac5b88a5851e4c2c5a5c447c92f9`。AGENTS.mdの継続条件を維持し、最新版SKILL、profile、OpenAI/native platform、candidate-schema、quick-checkとコード差分を確認した。

既存専用venvへrequirementsを再導入。Python3.12.14、jsonschema4.26.0、Codex CLI0.159.0。付属unit testsを実行。モデル指定は既存gpt-6-luna、実サービス側model ID/revisionは応答未取得で不明。秘密内容を読まずCODEX_HOMEのis_dir/statvfs/os.accessとinstallation_idの存在だけを確認した。

## 結果・比較

| 項目 | 結果 |
|---|---|
| 導入ファイル | 25/25上流と一致 |
| 付属tests | 74/74成功。nativeの成功fixture/モックを含み、実LLM成功ではない |
| CODEX_HOME | exists=true、mount_read_only=true、write_access=false |
| installation_id | exists=false、write_access=null |
| 実Codex probe | 約5.92秒、親exit1、exec子exit1、verdict=null |
| exec診断 | read_only / appserver、stdout0文字、stderr197文字 |
| 前回との差 | exec stderr SHA256は前回と同一 |
| native実呼出し | 0、backend制約の確認待ち |
| 実候補judge / モデル推論 | 0 / 0 |
| state | 4JSON hash一致、公式load_queue再読込1,057件、重複なし |

CLIの失敗はCODEX_HOME診断と整合する。ただし、実際に失敗した書込対象がinstallation_idだったとstderrの信号だけから断定しない。新版はnativeを選ぶことでCLI初期化を通らない設計であり、CLIの同一エラーはその経路の動作確認にはならない。

## 問題・導入判断・次

必要な確認は、今回に限り `RADAR_LLM=native` を明示選択し、ホストの `gpt-6-luna` 指定サブエージェントで固定probe、続いて保存済み公開候補資料1件を判定してよいか、というbackend範囲の明確化。認証/資格情報の移設、CODEX_HOME書込、権限緩和、別サービス/API利用は提案していない。

成功しても判定経路の成功と候補モデル推論は別。test/deferは完了にせず、rejectも根拠照合後にのみ完了を検討する。現時点ではprobe成功していないため、収集/採点/record_run/完了ID更新は実施していない。Kaggleに移す性能不足も観測していない。

現金0、モデルdownload0、GPU0秒、修復0。stateは夕方専用を維持。以前の試運転run/reportは上書きしていない。native向けcollector gateへの切替も未実施。上流改造、merge/deploy、SMTP/cron変更、SKU/朝/PP-LCNet/他担当への介入なし。

## 再現・証拠

- `experiments/research-repro-radar/runs/2026-10-13-reinstall-native-v2/`: installation、上流diff、outcome、hash manifest。
- `benchmarks/research-repro-radar/2026-10-13-reinstall-native-v2/`: unit tests、依存、CODEX_HOME診断、実Codex probeとstate検算。
- CLI再現コマンド（自動再試行なし）: Skill直下で `RADAR_LLM=codex python -B scripts/llm.py --model gpt-6-luna --timeout 180`。
- read-only検算: `python -B experiments/research-repro-radar/verify_reinstall_smoke.py --run 2026-10-13-reinstall-native-v2 --ref <取得済み夕方commit>`。
- 元39f7dfbの過去資料166 hashも一致。新検算器はrun指定対応を追加し、旧runの固定manifestは変更していない。

遠隔SHA/CIは最終応答で報告する。native経路の失敗/復旧は未確認であり、単にCLI失敗を新版全体の不具合とは扱わない。
