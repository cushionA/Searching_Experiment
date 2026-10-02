# 共通フレームワークとDockerの検証

2026-10-01、共通メインループを実際のwreq-js、impit、Patchright、rebrowser-patches＋Lightpanda、対照用Playwrightで検証した。ホストと専用Dockerコンテナの両方でローカルfixtureを使い、全チェックを通過した。実サイトのブロック結果・CAPTCHA突破率を追加測定する実験ではない。

## 検証結果

| 項目 | 結果・範囲 |
|---|---|
| Python単体テスト | ホスト44件成功。標準Dockerビルド44件、Nodeなしの任意wrapper 2件skip |
| Node単体テスト | runner 6件＋framework 13件、ホスト・診断用Dockerビルドで成功 |
| Cloud環境チェック | 標準Dockerビルドで7件成功 |
| 共通リンク移動 | 全5方式でトップ→内部URL、Cookie継続、302/200を確認。35台帳レコード |
| fill / click / hover | 3ブラウザで入力値・DOM・属性の実変化を確認。HTTP方式は未対応を記録 |
| 操作速度・ポインタの比較 | 同じ3ブラウザでseed付き入力を確認 |
| 簡単なボタン回復 | fixtureの403→ボタン→Cookie→通常本文→トップ1回→元URL1回再訪を確認 |
| 拡張機能 | Patchright・Playwrightで実読み込みを確認。Lightpanda・HTTP方式は未対応を記録 |
| 再開 | 完了した条件を再開しても台帳が変わらず、追加取得なし |
| 操作に伴うrobots制限 | native clickの禁止URLを3ブラウザで阻止。fixtureサーバへの到達なし |
| 全オプションのCompose入口 | Cloud用CA mount・一時NSS初期化を含めplan成功。指定4方式・Googleを末尾にした5サイト・3比較条件を確認 |
| 既存Pythonイメージ | 外部ネットワークなしのdemoとverify成功 |
| 証拠の保存と取り出し | コンテナ内export→ホストへdocker cp→ZIP CRC・全ファイルSHA256を再照合 |

オプションfixtureの台帳はnative 50件、humanlike 42件、extension 8件、robots-operation 9件。セッションfixtureと合わせ、5台帳・144レコードがverify成功。robotsの否定ケースではリンク移動のstateと任意操作の成否を分けて確認した。`navigation_completed`だけを操作成功として数えていない。

## コンテナで確認した条件

- UID/GID `10001:10001`、root filesystem読み取り専用。
- `--network none`、HTTP/HTTPS/ALL proxyは空。fixtureは同一コンテナのloopbackのみ。
- `cap_drop=ALL`、`no-new-privileges=true`、init有効。
- `/tmp` 512MiB、`/home/lab` 32MiBのtmpfs、shm 256MiB。
- メモリ2GiB、CPU 2、結果は名前付きvolumeの`/data`。
- Node 22、Chrome for Testing 153.0.8010.12、Lightpandaはversions.jsonの固定SHA256。Chromium条件は同じ実行ファイルを使用。

検証した診断用イメージは`sha256:b038ca3025b4a267d5955c1c4a151f3afb11ae22aed6960dc0fbbf825e4d2e41`、標準Pythonイメージは`sha256:11970d215e0bec314cb4b00322c9b8b99ecd61daefd0aadda65c8be42ec23bf1`。

## Dockerで必要だった変更

標準イメージにはNode・診断用依存・Chromium・Lightpandaがないため、専用Dockerfileと任意の`bot-diagnostics` Composeプロファイルを追加した。既存の標準イメージはPythonのみで、追加ブラウザ依存を導入しない。

`.dockerignore`に診断用ソースと既存Dockerテストが必要とするチェック用スクリプトを追加した。`.deps`、資格情報、実験結果、既存コーパスはビルドコンテキストに含めない。コピー元がmode 0600でも非rootが読めるよう、コンテナ内のコードの権限を正規化し、入口スクリプトの実行権限を明示した。

ブラウザの一時データを依存フォルダから`BOT_DIAGNOSTICS_STATE`へ移し、読み取り専用の依存配置で動作させた。実サイトのHTTPS_PROXY必須条件を維持しつつ、localhost fixtureはプロキシなしで起動できるようにした。

setupは固定ハッシュのLightpandaファイル再利用と相対Chromium symlinkに対応。Cloud用overrideは公開CAをBuildKit secretと実行時の読み取り専用mountで受け取る。CAをイメージに保存せず、Chromium用の信頼追加は一時ホーム内のNSSだけに限定する。今回のCompose検証は設定展開とCA初期化までで、コンテナから実サイトへのHTTPS取得は追加測定していない。

exportは`BOT_DIAGNOSTICS_RUN_ROOT=/data`の外部runとGitなしのイメージに対応し、ZIP内のrun配置をCHECKPOINT.jsonへ記録する。

最初の起動でソース/入口の権限不足、続くビルドで管理Dockerのvfs方式による容量不足を検出した。権限を修正し、ブラウザ依存を入れた後のコピー層を集約した。今回の未使用ビルドキャッシュと旧試作イメージを整理して再検証を完了した。実験データとvolumeは保持した。

## 保存物

ホスト側の機械可読集計: `.lab-output/framework-validation-summary.json`。コンテナから取り出した台帳・原HTML・DOM・画面・設定は`.lab-output/framework-docker-results/`、単体テストとDockerのログは`.lab-output/framework-*.log`に保存した。

独立したコンテナ検証ZIP: `.lab-output/framework-docker-checkpoint.zip`、216ファイル、717,941 bytes、5台帳。SHA256: `274a2eb1c55eda3898e7cf061e0a5c7fedc1780cf43fc99168c3be395e608022`。

このZIPはコンテナからホストへ取り出して照合済み。Dockerイメージ内のGit commitはnullで、同梱ソースとSHA256を根拠とする。依存バイナリ・ブラウザCookieストア・資格情報は含まない。再実行とexportのコマンドは[診断用README](../experiments/bot-diagnostics/README.md#dockerで実行する)を参照。
