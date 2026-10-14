# BitNet CPU PoC（準備済み・推論未実行）

2026-10-14、独立依頼「A試してみて」。分類・構造化抽出を各言語6件、計24件の独自合成入力で検査する。正解は事前に手書きし、モデル生成や学習を使わない。日本語・英語は別集計。seed=42、temperature=0、CPU4 threads、context=2048、最大96生成tokens、各3反復。厳密JSON（余分なキー・コードフェンスを拒否）、分類exact、抽出全項目exact／field accuracyを測る。各反復を独立した正解件数として水増ししない。

`benchmark.py` は標準ライブラリのみ。スコア処理と入力検証はテスト済みだが、llama-serverとの統合は未検証。Dockerfileは依存用ベースの準備段階のみで、完成した再現環境ではない。依存解決・コンパイル・モデルダウンロードは開始していない。

## 固定対象

- engine: microsoft/BitNet `0b341e582afbf9e1011f24744b554c96a3477eb5`
- model: microsoft/bitnet-b1.58-2B-4T `04c3b9ad9361b824064a1f25ea60a8be9599b127`
- CPU用公式GGUF: microsoft/bitnet-b1.58-2B-4T-gguf `a1f2f1c765812aa8af3f6eda4a313707064bba15`
- `ggml-model-i2_s.gguf`: 1,187,801,280 bytes、SHA256 `4221b252fdd5fd25e15847adfeb5ee88886506ba50b8a34548374492884c2162`（API宣言値、未取得）
- input SHA256 `63bb56cc62dfa05b5367e8f3e5f8b25d6170e92926d52ff42c694db641b64afe`

## 再開条件と手順

まず通常の承認済みDocker build環境をプラットフォーム側で利用可能にする。現在の `/home/agent/.docker/buildx` 読み取り専用エラーの迂回、設定移設、認証コピー、権限変更はしない。本記録の同一環境でリトライしない。

その後、2GB総ダウンロード枠にベースイメージ・依存・source・重みが収まることを取得前に確認。公式requirements全体は変換用途を含むため、フル導入が枠内とは確認できていない。容量を超えるなら停止。公式READMEのrecursive clone、依存導入、上記revisionのGGUF取得、`python setup_env.py -md models/BitNet-b1.58-2B-4T -q i2_s` を基本とする。submoduleは公式.gitmodulesが参照する isHuangXin/llama.cpp（Microsoft組織外）なので、固定gitlinkと内容を検査してから実行。未知の任意ソースを追加して動かさない。

ビルド後にengine/submodule SHA、compiler/CMake/Python/pip freeze、重み実SHA、ダウンロード実bytesを保存。現状はこれらを測定済みと扱わない。

推論はネットワークなし、nonroot、root read-only、cap-drop ALL、no-new-privileges、RAM8GiB・swap無効・CPU4・pids上限、専用 `/tmp` のコンテナ内で行う。全workspaceやHOME、資格情報、個人ファイルをマウントしない。必要な公開source/binary/model、合成入力、評価コード、空の出力フォルダーだけを渡す。そこで例えば:

```sh
python3 -B benchmark.py --server /opt/BitNet/build/bin/llama-server \
  --model /models/ggml-model-i2_s.gguf --out /output/run-001 --threads 4 --repeats 3
```

serverは127.0.0.1のみにbindし、GPU layers=0。起動ready時間、最初の要求、明示的warmup、定常要求を区別。最初の要求はOSキャッシュ完全coldを意味しない。prompt cache=false。生JSON／server timings／wall時間を保存。Linux child peak RSSとcgroup memory.peakを区別して保存し、重みのファイルサイズからRAMを推定しない。20分の全体締切、各HTTP最大120秒、起動最大180秒を設定。失敗時もログとsummaryを残す。新規出力先のみ許可。

24件はAPI・書式・簡単な品質のsmokeに限る。採用判断には別の未使用テスト集合（日本語の曖昧表現、否定、欠損、多品番を含む）、実ワークロードを反映した件数、同条件baseline、反復・thread sweepが必要。今回は既存baselineなし、比較未確認。

```sh
python3 -B -m unittest discover -s experiments/bitnet-cpu-20261014 -p 'test_*.py'
python3 -B experiments/bitnet-cpu-20261014/benchmark.py --validate-only
```
