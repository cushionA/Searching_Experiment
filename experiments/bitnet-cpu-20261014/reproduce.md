# 実行経路（2026-10-14）

これは認証・Docker接続先・権限を変更しない、公開データだけの検証手順。

1. `BUILDX_CONFIG` は公式にbuild configuration/state/logsの保存先を指定する変数（https://docs.docker.com/build/building/variables/#buildx_config）。今回のread-onlyエラー後、ユーザーがこの用途の空ディレクトリ指定を追加で明示許可。修復は1回で成功した。

```sh
bitnet_buildx_state=$(mktemp -d /tmp/bitnet-buildx-state.XXXXXX)
BUILDX_CONFIG="$bitnet_buildx_state" docker build --tag bitnet-cpu-poc:20261014 experiments/bitnet-cpu-20261014
```

2. 専用の新規runtime directoryに公開sourceとGGUFだけを取得する。`fetch_public.py` は実測で使った取得処理を整理したもの。最終スクリプト全体をもう一度ダウンロード実行することは予算節約のためしていない。実測中のsource初回展開はsubmoduleをdestinationにして内部相対linkで安全フィルタが停止したため、全projectをdestinationにして標準data filterのまま展開した。project外へのlink許可やセキュリティフィルタ解除はしない。重複source取得分も予算へ含めた。

```sh
python3 experiments/bitnet-cpu-20261014/fetch_public.py /workspace/bitnet-new-runtime
```

3. ビルド用containerはimageから起動し、ネットワークnone、memory=8g、memory-swap=8g、cpus=4、pids-limit=256、cap-drop ALL、no-new-privileges、user1000:1000。bind mountは一切なし。root filesystemはimage標準のwritable layerで、nonrootが `/tmp` 内だけに作業する。read-only-rootを今回実測したとは主張しない。hostの全workspace、HOME、認証、個人ファイルは渡さない。`docker exec -i ... tar` で必要な公開sourceだけを `/tmp/BitNet` に転送し、同directoryで `sh compile.sh` 相当を実行した。

4. ビルド後に新規の測定用containerを同じ制限で起動。build/binとGGUF、benchmark.py、cases.jsonlだけをtar転送。ビルドcontainerは停止。新規container内で以下を実行：

```sh
python3 -B /tmp/poc/benchmark.py --server /tmp/BitNet/build/bin/llama-server \
  --model /tmp/models/ggml-model-i2_s.gguf --out /tmp/result \
  --threads 4 --repeats 3 --budget-seconds 600
```

5. `/tmp/result` を `docker cp` でbenchmarksへ取り出す。`analyze_results.py <result-directory>` でgeneration token/s、serial requests/s、初回と定常のwall timeを集計。モデル生出力を正解や学習へ反映しない。

## 公式推奨との差分

- 公式setup_env.pyが呼ぶcodegen/CMakeを直接使用し、GGUF変換をしないためtorch/Transformers/gguf-py等の変換用Python依存は未導入。
- 公式指定clang>=18に対しclang-18を明示、CMake3.28.3、Python3.12.3。全dpkg versionを保存。
- serverだけをtarget指定、CPU-only、OpenMP OFF（GGMLの4threadを使用）。UI/App/testsをOFFに指定。ただし現行sourceのUI asset取得処理は走り、network noneのため接続できず、公式のUIなしfallbackでビルド完了。ネットワークを開けて再試行していない。
- sourceは固定commitのarchiveなのでbuild infoがunknownになる警告あり。engine/submodule SHAとbinary SHA256を別manifestで補う。
- 通常のGGUFに変更は加えていない。警告 `missing pre-tokenizer type` が出ており、結果はこの互換性問題を含む組合せの観測。モデル単体の能力評価や最良性能とは扱わない。

## 追加のtemplate診断

主run終了後、同じ隔離containerで別server processを起動し、次の条件を独立runとして保存した。主runの最中にmetadata `/props` と `/apply-template` を照会したが、追加の生成要求は同時実行していない。

```sh
python3 -B /tmp/poc/benchmark.py --server /tmp/BitNet/build/bin/llama-server \
  --model /tmp/models/ggml-model-i2_s.gguf --out /tmp/result-gguf \
  --threads 4 --repeats 1 --budget-seconds 120 --prompt-style gguf --smoke
```

smokeは元24件のindex 0,6,12,18（各言語・課題から先頭1件）を固定選択。出力を見て良い例だけを選んでいない。warmupと初回要求は別。二つ目のrunのcgroup peakは同containerの累積なので独立RAM測定とは扱わず、主runの終了時点のpeakを報告する。実際に動いたrunner snapshotとhashを各runに残す。
