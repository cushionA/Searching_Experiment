# クラウド調査・検証基盤

## 0. 全体概要

### 0.1 目的

網羅性を主指標とする情報収集実験をLinuxコンテナで実行する。GPT-6 Lunaが調査と探索を担当し、取得・予算・保存・検証は固定コードで制御する。人は具体的な実験案と結果を確認し、承認後に保存地点から再開する。

### 0.2 全体作業マップ

| 工程 | 主な作業 | 確認事項 |
|---|---|---|
| 現行把握 | 既存実験の再利用範囲を整理 | Windowsの外部ドライブと400M索引に依存せず動くか |
| 実行環境 | Linuxコンテナを準備 | 秘密情報と巨大な既存データをイメージへ含めないか |
| 実行制御 | 再開可能な状態管理を実装 | 二重実行と途中停止で予算や取得済み結果が失われないか |
| 調査 | GPT-6 Lunaの調査経路を接続 | モデルを固定し、資料中の指示や採点用正解を実行命令にしないか |
| 判断 | 実験案の承認を実装 | 人が承認した対象と予算だけが実行されるか |
| 実験 | 予算内の探索比較を実装 | 失敗・robots・転送量・取得経路を記録し、同条件を比較できるか |
| 検証 | 根拠と実績の検査を実装 | 未取得や通信障害を未収録と混同しないか |
| 統合検証 | コンテナの実利用経路を検証 | 調査から承認待ち、再開、結果確認まで動くか |
| 運用移管 | 起動と停止の手順を整備 | 配置先・認証・費用上限の未確定事項と復旧手順が明示されているか |

### 0.3 要注意事項・人間判断

- 配置先はCodexクラウド。GitHubは `cushionA/Searching_Experiment`。標準クラウド環境のsetup scriptと、ローカル検証用Dockerfileを用意する。
- 調査担当は `gpt-6-luna`。Cloud側のモデル選択が外部依存。今回の経路はAPIキー不要。実モデルIDをPythonから検証できたとは扱わない。
- ユーザーは対象・予算内の小さな試行を自律的に進めることを許可した。新しいサービス契約や大幅な範囲拡大は具体案を示して判断を求める。
- 判断点は未承認の実験範囲、対象・予算の変更、結果を受けた次段階への移行。既存の明示的な許可内の計画は許可内容を記録して進める。取得ごとの確認は求めない。
- 過去の400M索引と既存実験データは移行・削除しない。新基盤の保存先を独立させる。

### 0.4 重要な依存・分岐

- オフライン実行で状態管理と検証を確かめてから、モデル接続と外部取得を有効にする。
- 承認待ち・予算到達・モデル利用不能は保存して終了する。予算をリセットして繰り返さず、保存地点から再開する。
- 取得成功の主張は保存本文で検証する。独立した正解集合がない場合、網羅率は未測定と記載する。
- 初期比較はライブHTMLのBFSとLunaによるリンク選択。CC取得は既存資産として接続方針を文書化し、新基盤への未接続機能を完成扱いにしない。

# Phase 1: 実行環境と移管

### 作業と接続

- Python 3.12以上の標準ライブラリだけで `jse.lab` を実装。従来の検索エンジンやWindows外部索引への依存を切り離す。
- Codex Cloudは `scripts/cloud_setup.sh` をSetup / Maintenanceから実行する。Dockerfileはテスト用stageを経て、最終imageへ実行コードだけを入れる。
- `.dockerignore` と `scripts/package_cloud.py` の許可リストでビルド・公開対象を限定する。巨大な過去データの移行はしない。
- 指定GitHubへ必要ファイルを配置し、CIでLinuxテスト・Docker build・オフライン起動・別コンテナからの検証・imageサイズを確認する。

### 分岐と復旧

- SSH認証は未設定だが、既存のHTTPS認証で指定repoへ接続可能。既存LICENSEを維持する。
- ローカルDocker DesktopのLinux engine pipeへ接続できない。ローカルでビルド成功とは報告せず、CIで確認する。
- 運用開始にはユーザー側Cloud環境のrepo接続、モデル選択、対象ドメインのGET許可が必要。Secretsは不要。

### 確認事項と完了条件

- [x] Windows / Linux CIでクロール22項目、Kaggle helper15項目のテストを実行。
- [x] 生データと秘密情報を配布・buildの許可リストから除外。
- [x] GitHub CIで最終imageの起動を確認。imageは119,452,677 bytes、build contextは75.56kB、実行userは10001:10001。[検証run](https://github.com/cushionA/Searching_Experiment/actions/runs/36594100732)
- [ ] Codex Cloudの環境設定・実モデル経路・liveサイト疎通を確認。

コード準備とCloud実接続を別々に記録し、未接続を完成扱いにしない。

# Phase 2: 調査・承認・取得

### 作業と接続

`init → run → plan request → answer → plan gate → BFS / Luna探索 → verify → review request → answer → result gate` をCLIへ接続した。active_requestはモデルへ渡す観測と回答形式を持つ。ゲートIDは設定と判断内容のハッシュへ結び付く。

### 境界と復旧

- モデルが出したURLは提示済み候補に限定する。HTTP失敗・robots・リダイレクトも予算を消費する。
- HTTP前に上限を予約し、中断時の不明な消費量を勝手に返却しない。再開しても予算・証拠・失敗台帳を引き継ぐ。
- ファイルロックで二重実行を拒否。pauseは取得境界、強制停止後は不明な試行を保持してresumeする。
- 同一workspaceを編集できるCloud Agentへの敵対的な隔離・人間認証は未実装。承認は会話上の許可と結び付ける運用であり、署名検証ではない。
- proxy経由のprivate IP拒否はこの実行器だけでは保証しない。直接接続はpublic DNS検査とIP固定を行う。

### 確認事項と完了条件

- [x] stale判断・重複承認・設定変更・観測外URL・架空引用を拒否。
- [x] 失敗消費、要求上限、クラッシュ後の消費保持、再開、二重実行をテスト。
- [x] GPT-6 Lunaが実際に要求JSONへ回答するオフライン運転を実施。4要求、両armとも4ページ、検証PASS、結果判断待ちで保存。

オフライン運転は既知fixture上の機構確認。実サイトの品質差やCodex Cloudの実モデル経路の証明には使わない。

# Phase 3: 検証と継続

### 作業と接続

- HTMLと抽出本文のハッシュ、抽出再計算、原文引用の位置、モデルに渡した観測、回答ハッシュ、台帳を照合する。
- 正解なしの網羅率はnull。意味的正しさ、実モデルID、tokens/costは未測定として出力する。
- goldは別評価環境へ置き、探索・総括後のgradeで注釈URL到達率を測る。
- exportは許可された成果物パスだけを扱い、内部整合性を検査してZIPを作る。クラウドのcacheに永続保存を期待せず、checkpointまたはGit差分を明示的に保管する。

### 確認事項と完了条件

- [x] 本文・回答の改変、exportパス逸脱を検出。
- [x] checkpoint展開後も同じ検証を通過。
- [x] Luna 6のレビュー所見を反映し、proxy・承認・意味検証の制約を文書化。
- [x] 人が読む設定・初回依頼文・承認・再開・保存手順をREADMEとdocs/cloud-lab.mdへ接続。

### 次の未接続項目

複数サイトの独立評価、sitemap、Common Crawl、PDF、本文の追加観測、索引への投入。これらを既存armへ混ぜず、仮説・同じ上限・同じ評価集合を決めて比較する。

# Plan Review

結果: コード準備とオフライン運転はPASS。Cloud実接続は未検証。

- 作業順序: 環境・境界・再開を検証した後に公開とCloud接続へ進む。
- 回帰・移行: 既存検索コードとコーパスは変更せず、新しいrunを分離。既存データの変換や撤去は不要。
- 未検証条件: Cloudのモデル選択と対象サイトへの接続、実GPUジョブを使ったログ・出力の回収、終了済みCloudタスクの自動再起動。
- 人間判断: 当該runの対象と全体予算、対象や目的の変更。事前許可内で同じ質問を繰り返さない。

# Phase 4: Kaggleの任意利用

### 作業と接続

ユーザー指定のkaggle_ops.pyとauto_resume.pyを参考にAPIスキルを作り、ref・version・再開工程を保存する。通常imageからGPU依存を除外し、Secretsを持つGitHub Actionsで必要時のみ実行する。成功は出力の回収後に検証へ渡し、失敗はログと残予算を見て判断する。

### 復旧と確認事項

- [x] access tokenで実際の残量APIへ接続。GPU残量30時間を取得。quotaの実測値を使い、固定保証にしない。
- [x] Repository Secretへtokenを登録。Gitと配布物には含めない。
- [x] version固定、再送抑止、曖昧送信の照合、ログの秘匿、出力パスをテスト。
- [x] ActionsからSecretを使ったquota照会とartifact回収を確認。GPU残量108,000秒、起動したGPUジョブは0。[検証run](https://github.com/cushionA/Searching_Experiment/actions/runs/36594156042)
- [ ] 実GPUジョブの完了・出力回収とCloudの継続機構を接続。GPUが必要な実験が決まった時点で行う。

継続ファイルの生成と、終了済みCloudタスクの起動は別の責務。後者を未接続のまま完成と扱わない。

# Phase 5: 取得ツールの事前配備

### 範囲と接続

この工程は必要時に使える既製基盤の導入。Crawlee Python＋Impit＋SessionPoolとPatchright＋Chromiumを任意のDocker target / Cloud setupとして配備する。通常イメージは軽量構成を維持する。ブロック対策の調整・Camoufoxは追加しない。Adaptiveの接続は次工程で扱う。

### 確認事項

- [x] WindowsでCrawlee＋ImpitによるローカルHTTP取得・SessionPool・Patchrightの起動とJavaScript実行を確認。
- [x] Linuxコンテナでも外部通信なしで同じ確認に成功。[CI run](https://github.com/cushionA/Searching_Experiment/actions/runs/36596324607)
- [x] イメージ実測: core 119,452,677 bytes、HTTP tools 168,227,120 bytes、browser tools 1,348,329,422 bytes。いずれもuser 10001:10001。
- 実サイトでの突破性能は未検証。Impit/Patchrightを既存実験器へつなぐ作業は必要時に進める。

# Phase 6: Adaptiveによる描画方式の選択

### 接続と検証

ユーザーの追加指示により `transport=adaptive` を実装。共通の本文・タイトル・リンクを比較し、HTTPとブラウザを選択する。判定履歴はarm別に保存・再構成し、取得回数・転送量・robots・許可originの制御は既存の取得器へ集約する。DOMと原HTMLを区別して保存し、検証・exportへ接続する。

- [x] Windowsの実ブラウザで静的判定の再開、JSリンク、比較・リソース予算、scope/robots/POST、初期リダイレクト、DOM上限・証拠改変を検証。
- [x] coreの22テストに回帰なし。
- [x] LinuxコンテナでAdaptive 8項目、core 22項目、Kaggle 15項目に成功。[CI run](https://github.com/cushionA/Searching_Experiment/actions/runs/36600414413)
- [x] core 119,469,889 bytes、Adaptive 1,757,096,415 bytes。追加依存は任意環境のみ。非root・read-only・外部通信なしで実ブラウザを検証。
- [ ] 実サイトの取得改善・取りこぼし・資源消費を同条件で評価。

ブラウザの直接通信を止めて台帳経由で返す。HTTP通信は次工程でImpitへ接続する。共通の待機時間を超える描画、ログイン、クリック、無限スクロール、POST/WebSocket、追加リソースのリダイレクトは未対応。取得不能を網羅性の低さと混同せず失敗へ記録する。coreへ戻す場合は新しいrunでtransport=liveを選び、実行中の設定・上限は書き換えない。

# Phase 7: Impit接続と実サイトの動作確認

### 接続と上限

ImpitのHTTPを既存取得器へ接続し、HTTP単独はtransport=impit、ブラウザ併用はadaptiveとする。直接通信は1要求専用CONNECTトンネルで検査済みIPへ固定し、TLS証明書検証を保つ。環境proxy、HTTPクライアント名、プロトコルを台帳で区別する。

実サイト試験は青空文庫の作家一覧と気象庁の天気予報の2seed・2origin。2run×2arm、各arm最大2ページ・30 HTTP・保存本文3MB、追加探索なし。全体最大120 HTTP・12MBを使い切ったら止める。Lunaは計画・総括を担当し、今回は探索選択性能や網羅率を評価しない。

- [x] CONNECTの固定先・別authority拒否・期限、Impit stream上限・途中エラー時の予約保持を7テストで確認。
- [x] Adaptiveの8テスト、coreの22テストを維持。
- [ ] 実サイト取得・台帳・verify・exportを確認。
- [ ] Linuxコンテナで追加したImpit経路を検証。
