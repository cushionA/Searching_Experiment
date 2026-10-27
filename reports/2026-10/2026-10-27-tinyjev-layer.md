# TinyJev：承認済み2回目の追加配置修復

2026-10-27。前回の記録 ac1ff335b201aa8b2d931331b6ae5a8e48528e4d と夕方canonical stateは変更していない。

今回の結果は **イメージ作成前のDockerクライアントエラーで停止、推論0/48件**。NLP24件・gameAI24件とも候補の品質、無効出力率、推論時間、推論peak RSSは未測定。既存baselineは48/48だが、単純な合成問題であり一般化性能を示さない。

修復方式：非root UID1000、network none、read-only rootfs、cap-drop ALL、no-new-privileges、RAM8GB、CPU2・CPU時間1200秒制限を維持した準備コンテナで既存wheelを一時領域に導入。アーカイブした依存だけをCOPYするDockerfileを用意し、runtimeでは `/opt/deps` を読み取り専用イメージ層として使用、一時ファイルは `/work` に分離する設計。DockerfileにRUNはなく、ビルドで候補コードを実行しない。ホスト権限・mount policy・認証・ネットワーク設定は変更していない。

事前検証で元イメージの `libpython3.12.so.1.0` 読み込みと一時ファイル書き込みが成功。これは元イメージの実行可能性の検証であり、まだ作成できていない依存イメージのPyTorch検証ではない。オフラインpip導入も成功し、依存アーカイブを作成できた。

`docker build --network none --pull=false ...` は次でexit1：

```
ERROR: mkdir /home/agent/.docker/buildx: read-only file system
```

これはDockerクライアントのbuildx設定ディレクトリ作成失敗。新イメージは作成されず、モデルimport・48問実行は開始していない。別の環境修正になるため、設定先変更・権限変更・代替ビルド経路・再試行は実施せず停止。自動承認拒否ではなく実行時エラー。稼働中の当該コンテナや不確実な外部ジョブはない。

入力・独立正解・モデル実行コード・baseline・scorerは元のハッシュと一致。新規ダウンロード0バイト、費用0、Kaggle・学習なし。既存モデル版・コード版・入力ハッシュはmanifestに保存。`run.sh` とbootstrapは準備済みだが未実行。

証拠と再現手順：`benchmarks/research-repro-radar/2026-10-27-tinyjev-layer/` のprepare.py、prepare.sh、Dockerfile、prepare.log、build.log、manifest.json。元の証拠は別ディレクトリに保持。再実行にはこの新しい停止点を扱う承認が必要。
