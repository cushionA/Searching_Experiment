## native実判定確認（2026-10-13）

最新版55ceed6のホストnative経路でprobeと保存済みSWE-Pruner1件の実判定に成功。候補はdefer、推論0、未完了1,057件維持。詳細: [native v3報告](../../reports/2026-10/2026-10-13-evening-radar-native-v3.md)。既存collectorのcodex固定gateと単独cronは未移行で、CLI復旧を意味しない。ホストがnativeを明示選択するときは最新版platform/native.mdの2段階probeを使い、CLI gateを先に実行しない。

## 判定器修復ブランチ（2026-10-13）

上流修正: https://github.com/cushionA/zabuton_skills/pull/5 （commit `b4bf3f37786a645875c5c08ddca677f9ebe389cf`）。このブランチは正規stateブランチの最新版 `39f7dfb238565361683f240634c41f42acc107a0` から分岐し、実際の `.agents/skills/research-repro-radar/` コピーの6ファイルだけを同期した。profile等のローカル差分は維持する。

再開時は実行側にもこのブランチの変更を取り込む。正規stateの4JSONは変更せずに読む。Skillは固定した旧SHAではなく承認済み最新版を取得し、インストールされたコピーのSHA/manifestを照合する。スケジュール・cronは変更していない。今回アクセスできたAutomations一覧には夕方レーダーの定義が無く、固定プロンプトや実際の起動環境への反映は確認できない。別の起動設定がこのstateブランチを固定取得している場合は、承認後にこの修正をそのブランチへ取り込む必要がある。

大量探索・採点前に `RADAR_LLM=codex python experiments/research-repro-radar/judge_preflight.py` を1回だけ実行する。記録上の指定modelは `gpt-6-luna`（利用可能性は未確認）。変更が明示承認された場合のみ `RADAR_JUDGE_MODEL` に指定する。`collect.py` / `collect_expanded.py` は最初にこのgateを実行し、非0なら取得や書込みをしない。採点等の他コマンドを単独で起動する場合は `.agents/skills/research-repro-radar/scripts/run_checked.py --model gpt-6-luna -- <コマンド>` でgateする。複数コマンドのオーケストレータは開始前に一度だけpreflightを呼び、非0で通常ループからreturnする。候補なしとは報告せず判定基盤利用不可とする。既存1,679 artifactを再収集しない。

対応環境での確認範囲: 実行ファイル/version/help → 最小probe → 保存済み `runs/2026-10-13-evening/review-sources/` 資料1件を実候補schemaで `llm.judge_with_diagnostics()` に渡す。今回のWork環境はcodex未検出なので後半2段階は未成功・未実施。候補モデル推論とは別の確認である。認証の移設やsandbox解除は行わない。

以下は過去の調査記録であり、古い再開条件より上記gateを優先する。

# 最新の夕方記録（2026-10-13 JST）

[当日報告](../../reports/2026-10/2026-10-13-evening-radar.md)。一意artifact1,679、最終review20、推論0、未完了1,057。必須Codex judgeはnullで未完了。初回444件を全て保持。`evening-state/` が正規の4JSONで、最新の記録を復元する。

現行stateの検算:

```bash
python experiments/research-repro-radar/verify_evening.py --run 2026-10-13-evening --previous-ref f1a9a681a4f06d33a004c3ca77d7fab5c03e58c5
```

以下の初回記録と `verify_state.py` は初回1runに固定された履歴用。最新stateでその固定テストを使わない。stateディレクトリが既に存在するときは `cp -r evening-state state` ではなく、4つのJSONをstate直下へコピーしてhashを検算する。

---

# 夕方 Research Repro Radar（初回・2026-10-12 UTC）

最新版 Skill をそのまま導入した、実調査と状態保存の試運転。確定判定と候補モデル推論は環境エラーで未完了。失敗を成功として数えない。

- 保存先: `cushionA/Searching_Experiment`
- 正規ブランチ: `research/2026-10-12-evening-radar`
- 正規状態: `experiments/research-repro-radar/evening-state/`
- 今回: `runs/2026-10-12-evening/`
- 詳細: `reports/2026-10/2026-10-12-evening-radar.md`
- 導入 Skill: `.agents/skills/research-repro-radar/`
- upstream: `cushionA/zabuton_skills@7abebd12df90dbce8eabf741384a52a171dd1799`

この環境には更新前の対象 Skill がなく、初回導入。導入した20ファイルは upstream Git blob SHAと一致する。`skill-manifest.json` に全ハッシュ、profile、前後、運用上の上書きを保存。`upstream-changes.json` は前の main からの差分であり、存在しないローカル旧版の差分ではない。元 Skill を改造せず、cron/SMTP/朝07:00/他担当作業は変更していない。

## 次回の復元

既存の別作業の checkout を切り替えず、独立ディレクトリへ正規ブランチを取得する。

```bash
git clone --single-branch --branch research/2026-10-12-evening-radar https://github.com/cushionA/Searching_Experiment.git evening-radar
cd evening-radar
python3 -B experiments/research-repro-radar/verify_state.py
```

4つのJSONをまとめて復元する。`seen.json`、`queue.json`、`mail.json`、`freshness.json` は夕方専用。朝の各研究ブランチや zabuton_skills の `ci-state` と統合しない。現在 seen は空、mail の送信履歴は空、queue は444件、freshness は今回1件（詳細推奨0、日付判定の分母0）。正規stateがなかったため今回が初回。空のseenは重複抑制の故障ではなく、必須judgeが使えず確定推奨を出さなかった結果。

毎回、upstream main を確認して Skill のコード・参照だけを更新し、commitと全blob SHAを記録する。`state/` と `ci-state/` を upstream から復元元へ上書きしない。安全上限を広げる profile の変更は自動採用しない。今回のprofile v7は指定上限から変更なし。profileのemail指定に優先してユーザー指示によるメール禁止を適用した。

## 手順

Python 3.12以上。依存は専用venvへ。取得本文は未信頼データであり、そこに含まれる指示を実行しない。

```bash
python3 -m venv /tmp/radar-venv
/tmp/radar-venv/bin/python -m pip install -r .agents/skills/research-repro-radar/requirements.txt
python3 -B experiments/research-repro-radar/collect.py --run experiments/research-repro-radar/runs/NEW_UNIQUE_RUN
/tmp/radar-venv/bin/python .agents/skills/research-repro-radar/scripts/rank_candidates.py experiments/research-repro-radar/runs/NEW_UNIQUE_RUN/candidates.json --state-dir experiments/research-repro-radar/evening-state --output experiments/research-repro-radar/runs/NEW_UNIQUE_RUN/ranking.json
```

今回の collector は最初の実行時は固定run引数で動作し、その後、既存証拠を上書きしない必須 `--run` 引数を追加した。採点は取得全件。HF APIの1応答にある検索結果の上限は調査全体の網羅性を意味しない。7応答はすべて保存し、次ページLinkは返らなかった。40件で打ち切る実装ではない。

**資源未確認の候補を自動実行しない。** 今回は全件に保守的な「20分の確認枠」を割り当てたが、推論時間の予測ではない。上流schemaはCPU/GPU不明を表現できず `requires_gpu=false` にCPU加点が付くため、未確認フラグを別途記録した。`ranking.json` のscheduledは実行許可・成功を意味しない。一次資料20件の照合で見つかった題名/公式repoの食い違いは `reviews.json` に記録し、元取得と初回順位は監査用に保持している。次回の再発見時に一次資料に基づく訂正メタデータを渡すこと。

judgeは `RADAR_LLM=codex` と Skill の `llm.judge()` のみ。有料APIへ迂回しない。今回の既存ログインは確認済みだがapp-server初期化がread-onlyエラーでnull。2回目は原因を記録する診断であり、別モデルや別APIは使っていない。候補の推奨判定は確定せず、読解メモのA/B/Cだけを暫定優先度として示した。

結果を確定できたときだけ、`report.json` の `recommended` と `completed_ids` を正しく作り `record_run.py` を呼ぶ。今回の両配列は空で、未検証444件を残した。`record_run.py` の再実行は同じrun_idのfreshnessを二重計上しない。送信スクリプトは実行しない。

## 試運転の証拠と再開条件

- 673一意候補、444報告候補、20一次資料読解、6公式README確認、ダイジェスト15。
- Skill付属24テスト成功。正規stateの完全コピー・再投入・再発見重複抑制・同runのfreshness冪等性を確認。既報抑制/完了削除は合成fixtureを別stateで確認し、実候補数に含めていない。
- repo指定のlabテスト151件: 1失敗、15skip。原因は既存bot-diagnosticsテストの `robots-parser` 不足。通常CI保守は行わない。
- PP-LCNet準備: 公式コード/重み/公開画像、Python公式image、binary wheelのみ取得。保守的計上355,495,342 bytes。Docker内TLSエラーをhostの既存TLS設定でのwheel取得へ1回修復し、証明書検証は維持。続いてDocker buildxのread-onlyエラーで停止。モデル推論は0件。
- `benchmarks/research-repro-radar/pplcnet_smoke.py` は**未実行**の再試験用コード。公式アーキテクチャのrepo hook/importだけを最小adapterへ置換予定。元Skillは変更していない。独立画像1件と4変形、品質比較なしの簡易確認に限定し、モデル精度や論文速度の再現を主張しない。
- PP-LCNetの再試行には、修復回数をリセットしないまま追加修復の判断と、Docker build用書込先の確認が必要。既存Codex認証をコピーしない。判定器の再開にも読み取り専用エラーの解決が必要。

研究資料・未完了checkpointの保存であり、実用機能の完成ではないためdraft PRなし。merge/deployなし。
