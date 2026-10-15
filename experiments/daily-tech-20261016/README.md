# LightOnOCR-3 出力契約の限定検証

モデル推論ではなく、公開commit `36755d461be079737860a5f03ae0c803501269e9` の
標準ライブラリだけを使う `grounding.py` / `models.py` を検証する。
原ファイルは `benchmarks/daily-tech-20261016/sources/` にbyteのまま保存、Apache-2.0本文も同梱。
実行前に両moduleの全体を読んだ。候補のREADME記載コマンドやserverは実行しない。

```bash
# 一次資料を再取得する場合だけ。可変APIの再取得は既存観測を上書きするので別checkoutで行う。
python3 -B experiments/daily-tech-20261016/collect.py
# 初回だけ公式Python imageを取得。実行時はdigest固定、networkなし。
docker pull python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
python3 -B experiments/daily-tech-20261016/run_probe.py
python3 -B experiments/daily-tech-20261016/audit.py
```

`protocol.json` は実行前に固定。parser11入力・model名6入力。
`documented` 6例は仕様の通常動作。`adoption_guard` 5例と未知model名は、
こちらの採用条件（原文保持・座標検証・mode明示）であってupstreamの保証ではない。
不一致をモデル精度・upstream regression・実世界の失敗率と解釈しない。
標本は自作合成文字列のみ。モデル生成、画像OCR、性能の版比較、ネット巡回なし。

runnerは公開2module・自作probe・入力JSONだけをstdin tarで渡す。
host mountなし、認証なし、nonroot、read-only root、network none、1CPU、256MiB、
swap追加なし、32pids、16MiB tmpfs、30秒timeout。入力tarは20,480bytes。
今回exit0、stderr空、修復0。全入力・出力・期待値・SHA・実行コマンドはbenchmarkに保存。
実行成功exit0は全採用条件passを意味しない。

`audit.py` は保存hash・期待値・集計の整合を確認するだけで候補を再実行しない。
本体や新実験基盤の変更がないため、lab全体のCI修復・モデル導入は行わない。
