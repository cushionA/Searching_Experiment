# 引き継ぎ

作業ブランチ: `codex-structure-routing-20261115`。元commit: `822d19d43aa8246107fb9b289a9145bf2fc45965`。今回の状態は `STATUS.json`、数値は `summary.json`、判断と全80サイト表は `report.md`。

80サイトの代表ページコード方式を完了。有効HTML204件/43サイト、目的型101件/23サイト。cold/warmを同じコード・別PIDで実行したのは48サイト。新規74サイトと先行6サイトの再利用を分離した。実行時LLM・ローカル推論0、初期native token数は不明。

## クラウドへ移す

1. Gitからこのブランチを取得する。チェックポイントは `lab-runs/structure-routing-representative-code-all80-20261116-checkpoint.zip`、SHA256等は同じ接頭辞のexport JSONを参照。
2. ZIPがGitのサイズ制限を超える場合は `.zip.partNNN` とparts manifestを配布する。manifest順に結合してZIPを復元し、ZIPのSHA256を照合する。元ZIPや過去runを削除しない。
3. repository rootでZIPを展開する。保存HTML・DOM・状態ログはZIPに含む。Gitに見えるコード・集計ファイルだけで原HTMLが永続化できたと扱わない。
4. 展開前の確認や空フォルダーへの検証は `inventory.json` の全ファイルSHAを照合する。ZIP展開はPython `zipfile` 等で行える。完了済みの公開ページを再取得して照合しない。

## ネット接続なしで再集計

Python 3.12以上と `lxml` を用意し、repository rootから実行する。

```powershell
python -X utf8 -B lab-runs/structure-routing-representative-code-all80-20261116/summarize.py
python -X utf8 -B lab-runs/structure-routing-representative-code-all80-20261116/audit.py
python -X utf8 -B -m jse.lab structure-routing --method representative-code --run lab-runs/structure-routing-representative-code-all80-20261116 --action verify
```

`summary.json` / `evaluation.json` / `operational-results.jsonl` / `item-evaluation.jsonl` は決定的な固定判定器で再生成する。policy・候補集合は生成/取得完了後にrunへ移した `*-frozen.json` を使う。候補選定をやり直して分母を変えない。

`audit.py` は費用・ハッシュ・同一性検査で、公開ページを取得しない。Nodeと実行器のimport依存が必要な `verify` は、既存のstructure-routing環境を用意する。Node 22系、Playwright 1.63.0、Chrome 157.0.8103.51で測定済み。Linuxで新しくライブ取得する場合はChrome executable pathが異なるため、同条件の継続とは扱わず新しいrunを作る。

## 状態の扱い

- `results.jsonl`: 実行できた107 phaseの生結果。`sites/ID/cold|warm/result.json` と一致。
- `operational-results.jsonl`: 実行できなかったものを含む80×2=160行。`verification_status` とHTML保存数・目的型確認数を見る。生の `html_saved` statusだけで成功扱いしない。
- `cost-ledger.jsonl`: 303セッション、647文書予約・1030操作。先行76文書/125操作と失敗・独立評価の取得費用を含む。`evaluation-cost.jsonl` は内訳なので足し直さない。
- `adopted-pilot/trial-results.jsonl`: 先行16試行の履歴。12 canonical phaseに重ねて成功数へ加算しない。
- `model-jobs/`: 初期調査だけの入力・出力SHA。初期観察以外の本文や採点結果を生成担当へ渡さない。
- `source-final.json`: 共通実行器の修正と測定上の限界。`crawl.js` は観測後に修復していない。

`import-pilot.py` の通常実行は既存台帳を拒否する。今回のrunに再importしない。`--finalize-paths` は参照パスの補正用に一度実施済み。状態を消したり、失敗分だけ削除したり、結果に合わせて判定器や固定候補を変更しない。

## 検証

Node専用テスト4/4、CLI6/6成功。全 `test_lab*.py` は87件中2失敗・5skip。失敗は既存Windows Camoufoxパス検査とNO_PROXY環境期待値。今回のHTML取得・ハッシュ検査は `verification.json`、`integrity-audit.json` ともok。詳細は `representative-code-tests.log`、`structure-cli-tests.log`、`test-lab.log`。

次の実装候補は一覧/目的型の確認工程と生成APIの事前検査。現runの再実行や小型モデル追加は今回の完了条件に含めていない。既存のユーザー作業 `experiments/bot-diagnostics/` と旧raw・索引データには触れない。
