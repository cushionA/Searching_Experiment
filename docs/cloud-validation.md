# Cloud・Kaggle検証記録

2026-10-01、個人用保管庫のネットワークシークレットでCloudからKaggleへ直接接続した。トークン値はGit・Notebook・ログ・設定ファイルへ保存していない。

## 実GPUの確認

- Notebook: `superbigzabuton/codex-cloud-gpu-smoke-c257f73b`、version 1。
- 非公開、外部通信なし、生成データだけ、実行上限300秒。
- Tesla T4 2基、各15360 MiB、CUDA 12.8、PyTorch 2.10.0+cu128。
- `cuda:0` と `cuda:1` の256×256行列積が正解と一致。
- Kaggleのstatusはcomplete。出力を回収し、SHA256を再計算して一致を確認。
- 結果ファイルSHA256: `c203b4e4ffa952d028a94f1b4ed737dd276c94a58265396f081c7a497e5d2529`。
- GPU使用量は21.666秒、予約残量0秒。テスト計算は約1秒。これは長時間学習の性能測定ではない。

## 再発防止

Kaggleはimport時に設定フォルダを作るため、helperが書き込み可能な `.deps/kaggle-config` を自動設定する。明示済みの `KAGGLE_CONFIG_DIR` は維持する。

未作成NotebookへのGetKernelは403になることがある。helperは認証済みの自分のNotebook一覧を全ページ確認し、対象が存在しない場合だけ新規名として扱う。既存や他人の403を無視しない。

送信応答のrefは実APIで `/code/owner/slug` となるため、`owner/slug` へ正規化して照合する。別のrefは拒否する。応答が曖昧な場合は再送せず、保存された新しいversion・元ソース・非公開設定・GPU/Internet設定を照合する。一致しなければsubmission_unknownを保持する。回帰テストには実API形式、誤照合、二重送信を拒否するケースを含める。

Codex CLIの自動導入・独立CLI backend・Kaggle用GitHub Actions経路は削除した。標準SetupにはPython 3.12以上だけが必要で、Kaggleは専用venvへ任意導入する。

Cloud製品側のexecutor起動失敗は、このリポジトリの修正で防げる問題ではない。実行環境の復旧後に同じチェックを再実行する。設定案の保存、製品UIによる公開、次回タスクでの検証は別の操作として確認する。

## 修正後の回帰検証

- 標準テスト42件、環境テスト7件、Kaggle helperテスト23件が成功。
- Codex CLI・Node.js/npmをPATHから除いた状態で標準Setupを実行するテスト1件も成功。CIで同じ検査を実行する。
- 修正済みhelperで superbigzabuton/codex-helper-regression-bf2e1f4a のversion 2を通常手順で送信し、complete・出力回収・SHA256照合まで成功。CPUのみ、非公開、外部通信なし、実行上限60秒。追加のGPU実行なし。
- 修正後の出力SHA256: 48bad0904bdfa10777c5394e7a2933329634fbb074b126b51cc6df003ce43ece。
- 配布ZIPのファイル一覧と各SHA256を検証し、削除したCLI導入script・Kaggle Actions workflow・認証・生成データが含まれないことを確認。
