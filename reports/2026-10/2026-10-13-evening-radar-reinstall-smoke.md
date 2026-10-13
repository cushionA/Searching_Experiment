# 夕方Radar 修正版再導入・試運転 — 2026-10-13

## 結論

**判定器は未復旧。再導入・ソース一致・付属36テストは成功したが、実Codex接続確認は失敗した。** 指定 `gpt-6-luna` の実呼出しは約4.04秒、親exit 1、子exit 1、verdict null。実行段階のstderrに `read_only` / `appserver` 信号がある。根本原因の書込先や認証・モデル利用権までは確定できない。

実候補schema判定0、候補モデル推論0。未完了1,057件と保存済み1,679 artifactを保持。判定器成功を条件とする少数検証には進まなかった。

## 概要・版

- 上流main: [`f986e0c185898bf8f3f3184adcad91738664441e`](https://github.com/cushionA/zabuton_skills/commit/f986e0c185898bf8f3f3184adcad91738664441e)。前回 `7abebd12` から2コミット、6ファイル359行追加/46行削除。修正本体 `b4bf3f3` とPR5のmerge。
- 正規夕方branchの取得時SHA: `58f01b4d518d038a0c273be85e28a34eeed823c2`。前回確認 `39f7dfb` 以後に別作業のPR27が取り込まれ、修正済みSkillはすでに存在した。
- 上流のコード・参照20ファイルをコピーし直し、全SHA256一致。再導入前とも20ファイルすべて一致。state/ci-stateは上流から上書きしていない。既存profile v7の予算変更なし。
- 実行条件: Python 3.12.14、jsonschema 4.26.0、Codex CLI 0.159.0、RADAR_LLM=codex、指定model gpt-6-luna、timeout 180秒。実際に応答したmodel ID/revisionは取得できていない。

## 方法

AGENTS.md、導入Skill、profile、OpenAI platform、candidate-schema、quick-check、既存夕方報告と修復手順を確認。独立worktreeに正規夕方remoteを取得し、上流mainをclone。依存は専用venvへ `python -m pip install -r requirements.txt` で導入した。

正規state4JSONをSkill直下stateへ直接コピーしてhash一致を検算。上流 `scripts/llm.py --model gpt-6-luna --timeout 180` をSkill直下から1回だけ実行した。実呼出しのstdinは接続確認用の固定指示と空データであり、保存済み実候補を判定した結果ではない。version/help/execを診断器自身が実行し、既存のread-only sandbox・権限関連フラグを維持した。

## 結果・比較

| 項目 | 結果 |
|---|---|
| ソース一致 | 20/20、state除外 |
| 付属unit tests | 36/36成功。モックのtransport/schemaテストを含み、実LLM成功ではない |
| repo指定labテスト | 151件、1 failure、15 skip。既存robots-parser不足。通常CI保守は実施せず |
| 実version/help | exit 0 / 0、必須フラグ不足なし |
| 実exec | exit 1、stdout 0文字、stderr 197文字 |
| 親probe | exit 1、verdict null、wall 4.04秒、内部elapsed 3.558秒 |
| エラー信号 | 実行stderr: read_only, appserver |
| 実候補judge / モデル推論 | 0 / 0、probe失敗で停止 |
| state | 4JSONのbefore/after一致、1,057件、重複なし、公式load_queue再読込1,057件 |
| 過去保存資料 | 元commit39f7dfbで166 hash一致 |

前回はexit 0 / verdict nullで子失敗が表に出なかった。今回は失敗段階・子終了コード・stream hashが取得でき、親もexit 1となった。**診断と失敗通知の改善を確認したのであって、judge機能の復旧ではない。** version段階のread-only警告とexec失敗は別記録。help本文に含まれるmodel_unavailable信号は、成功したヘルプの文字列照合でありモデル利用拒否の証拠ではない。

## 問題・停止理由

最新版SKILLの基本フロー0は、接続確認非0なら通常ループへ進まず、既存stateを変更せず、候補を完了扱いにしないと定めている。これに従いrecord_run、実候補判定、候補コード実行、モデル重み取得を行っていない。候補の効果・速度・RAM・比較品質は未測定。GPU不足を観測しておらずKaggle移行の条件も成立していない。

既存 `verify_evening.py` は現在のREADME hashで停止した。修復PR27がREADME/collector 2本を変えたため、過去runのSHA256 manifestと現在の可変ファイルが異なる。元commit39f7dfbでは166件すべて一致し、正規state4JSONも一致する。過去manifestや元検証器を成功に合わせて書き換えていない。新しいread-only検算器で元commitの証拠と現在stateを別々に検証した。

## 導入判断・次

修正版Skillはmainと一致した状態で導入済み。研究のA/B/C判定、採用判断は保留。次は既存認可のCodex環境でread-only/appserverの実行失敗を解消し、同じmodelのprobe成功後に保存済み資料1件のschema判定へ進む。原因の書込先は未確定で、認証移設・権限緩和・別backend/model・有料APIでの迂回は実施していない。

現金0円、モデルdownload0、GPU0秒、自動修復0、実probe1回。朝/weekly、GLiNER SKU、PR19/26、メール/SMTP/cron、merge/deployは変更なし。実用機能の追加ではないためdraft PRなし。

## 証拠・再実行

- `experiments/research-repro-radar/runs/2026-10-13-reinstall-smoke/`: installation.json、commits.json、outcome.json、上流diff、hash manifest。
- `benchmarks/research-repro-radar/2026-10-13-reinstall-smoke/`: judge-preflight.json、skill-tests.log、dependencies.txt、state-verification.json。
- 診断は上流の公開用形式のみ保存。stdout/stderr原文、環境変数、認証内容は保存しない。入力は上流PROBE_SCHEMA/preflight固定内容で、コード版を固定している。
- 検算: `python -B experiments/research-repro-radar/verify_reinstall_smoke.py --ref <取得済み夕方commit>`。正規stateは `experiments/research-repro-radar/evening-state/`。

遠隔push後のSHA・CI・復元検算は最終応答に記載する。現在の既存artifact1,679／今回モデル実測0／未完了1,057。
