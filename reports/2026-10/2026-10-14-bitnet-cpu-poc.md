# BitNet.cpp CPU PoC — build環境で停止

## 結論・導入判断

ユーザーの「A試してみて」に基づき2026-10-14 UTCに独立して実行準備したが、Docker buildが `ERROR: mkdir /home/agent/.docker/buildx: read-only file system`、exit1で停止した。候補コード実行0、推論0。精度・速度・peak RAMは未測定で、CPUで実用的という判断はまだできない。導入保留。これは自動承認審査の拒否ではなく実ファイルシステムエラー。修復・同一失敗の再試行・設定移設・認証コピー・別経路の迂回は行っていない。

最小の次対応は、通常の許可済みDocker buildが動く実行環境をプラットフォーム側で提供すること。ユーザーのセキュリティ設定を変更しての回避は提案しない。現金支出0、重み取得0、pip/apt依存取得0。Dockerベースと小さい公開metadataのみ取得した。実wire転送量は未計測（2GBを使ったとの主張はしない）。推論計算0分、RAM8GiBを指定した隔離helloのみ成功。

## 技術概要・最新同系列・ライセンス

[公式BitNet](https://github.com/microsoft/BitNet) はternary重みのCPU推論を最適化するフレームワーク。確認したheadは `0b341e582afbf9e1011f24744b554c96a3477eb5`。公式READMEの要件はPython>=3.10、CMake>=3.22、clang>=18。recursive clone、Python依存導入、公式GGUF、setup_env.pyのi2_sビルドが推奨経路。今回はrepo規約に従いPython3.12を使用する計画。ホストはPython3.12.14、clang/CMakeはない。

[生成モデル](https://huggingface.co/microsoft/bitnet-b1.58-2B-4T) は引き続き `04c3b9ad9361b824064a1f25ea60a8be9599b127`。公式説明は2.4Bパラメータ／4T学習tokens。model cardは英語を示しており、日本語品質は独立検証が必要。MicrosoftのBitNet検索APIで新しいembedding 270M/0.6BとVibeVoice-ASR-BitNetも確認したが、生成モデルの後継として代替しない。API検索結果の範囲では新しい同用途の生成モデルは確認できなかった（全世界のモデルの不存在証明ではない）。

CPU公式手順が使う[GGUF配布](https://huggingface.co/microsoft/bitnet-b1.58-2B-4T-gguf)は別repo、revision `a1f2f1c765812aa8af3f6eda4a313707064bba15`。ファイル1,187,801,280 bytesで、前調査のsafetensors 1,178,623,988 bytesとは配布形式が違う。API宣言SHA256とrevisionをstatus.jsonへ保存したが、未取得なので実ファイル検証ではない。重みサイズから実行RAMを断定しない。

[engine LICENSE](https://github.com/microsoft/BitNet/blob/0b341e582afbf9e1011f24744b554c96a3477eb5/LICENSE) と [model LICENSE](https://huggingface.co/microsoft/bitnet-b1.58-2B-4T/blob/04c3b9ad9361b824064a1f25ea60a8be9599b127/LICENSE) はMIT、実体を保存。依存全体のライセンス監査は未実施。公式.gitmodulesは isHuangXin/llama.cpp を参照するため、その固定commitと内容も実行前確認が必要。今回は取得コードを実行していない。

## 方法・結果・比較

独自合成24件：日本語／英語 × 分類／抽出 × 各6件。正解はモデル実行前に固定しモデル出力を使わない。shipping/billing/account分類とitem/quantity抽出。固定seed42、temperature0、4threads、2048context、96tokens上限、初回→warmup→3反復を計画。入力hash、機械可読条件、ソースAPI応答、失敗ログをbenchmarksへ保存。評価コードはexperimentsに保存した。

|項目|結果|
|---|---|
|CPU|AMD EPYC 9V74、5 vCPU表示、実験上限4、AVX2/AVX512対応|
|Docker|28.4.0、daemon応答成功|
|隔離hello|network none・RAM8GiB・CPU4・cap-drop ALL・no-new-privileges、exit0、mountなし|
|候補build|exit1、buildxディレクトリread-only、候補イメージ未作成|
|初回／定常 latency|未測定|
|tokens/s・throughput|未測定|
|peak RSS／全体RAM|未測定|
|分類精度／抽出exact／形式成功率|未測定|
|baseline|同条件で実行可能な既存baselineなし、比較未確認|

helloはコンテナの基本起動の確認だけでBitNetのsmoke成功ではない。24件の設計自体も短いsmokeであり、有意な性能・導入判断には不足する。モデルserver連携は未検証、Dockerfileは依存ベース準備のみ。評価器のテスト成功を推論成功とは扱わない。

## 問題・次の検証

1. 通常のDocker build利用可能な環境を提供後、依存を含む総2GB枠を先に見積もる。公式の全requirementsは変換用途も含み、今回枠内に収まるとは確認していない。上限超過なら停止する。
2. engine/submodule・依存version・重み実SHAを固定し、資格情報／個人ファイルを渡さずCPUのみでsmoke。初回、warmup後、RSSとcgroupのpeakを別記録する。
3. 成功時に別の未使用入力で日本語否定・曖昧性・欠損・複数項目を検証し、同条件baselineと比較して初めて実用性を判断する。

## 保存範囲・チェック

最新main `7460493cc3f7242748c95f711e71a59537ef34ea` と既存branch一覧を確認し、独立branch `research/2026-10-14-bitnet-cpu` を作成。夕方branch `f197d9d181e59998cfb61040ed763d308e512fa5` のAGENTS.mdはmainと一致。既存SKU作業・朝夕自動研究は再開／変更せず、canonical 1057→1072件のstateへ書込みなし。

採点テスト4件と24入力のhash確認成功。repo指定labテストは151件中135成功・15skip・1失敗。既存bot-diagnosticsの `robots-parser` 未導入による失敗（`test_offline_diagnostics_guards`）で、今回対象外の依存は追加しなかった。詳細は同benchmarks/checks.txt参照。実用機能完成ではないためdraft PRなし。merge/deployなし。
