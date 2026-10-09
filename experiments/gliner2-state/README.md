# GLiNER2.5-multi-Decide: CPU state classification PoC

検索とは独立したNLP分類の実験。本文から現在の販売状態を4択で分類し、型を検証した `state` と `can_ship_now: bool | None` を返す。実サイト・購入処理との接続はない。TypeSafeJevの実装でも代替性の実証でもない。`unknown` と低スコアの棄却は別物で、スコアを正解確率に読み替えない。

結果: [日次レポート](../../reports/2026-10/2026-10-08-gliner2-state.md)。入力96件は手作り合成データでtrain/test各48件、各言語・各クラス6件。日英は翻訳ペアなので96個の独立観測ではない。testを見てプロンプトや閾値を調整していない。ルールとTF-IDFは同じtest、TF-IDFだけtrainを利用。GLiNERはzero-shotであり学習条件は同一ではない。

## Reproduce (Python 3.12, Linux CPU, internet for installation/download only)

```bash
python3 -m venv /tmp/gliner-state-venv
/tmp/gliner-state-venv/bin/pip install --extra-index-url https://download.pytorch.org/whl/cpu -r experiments/gliner2-state/requirements.lock
HF_HOME=/tmp/gliner-state-cache HF_HUB_DISABLE_XET=1 /tmp/gliner-state-venv/bin/python experiments/gliner2-state/download_model.py --cache /tmp/gliner-state-cache --manifest /tmp/gliner-state-results/model-manifest.json > /tmp/gliner-state-model-path
```

Run each engine in a separate process (same CPU/thread limits). Models remain outside the repository.

```bash
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 /tmp/gliner-state-venv/bin/python experiments/gliner2-state/benchmark.py --engine rules --output /tmp/gliner-state-results/rules.json
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 /tmp/gliner-state-venv/bin/python experiments/gliner2-state/benchmark.py --engine tfidf --output /tmp/gliner-state-results/tfidf.json
HF_HUB_OFFLINE=1 HF_HOME=/tmp/gliner-state-cache OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 /tmp/gliner-state-venv/bin/python experiments/gliner2-state/benchmark.py --engine gliner --model-dir "$(cat /tmp/gliner-state-model-path)" --output /tmp/gliner-state-results/gliner.json
python3 -B -m unittest discover -s experiments/gliner2-state -p 'test_*.py' -v
```

`make_fixture.py` regenerates the fixed fixture exactly. Protocol/fixture/benchmark hashes taken before inference are in `benchmarks/gliner2-state-20261008/`. Saved metrics include every prediction, score, latency, confusion matrix, startup time and process peak RSS. CPU scheduling can change timings; compare labels/metrics first. Baseline hyperparameters, model schema and score gate are fixed in `benchmark.py` and `protocol.json`. Model revision and all downloaded file hashes are in `model-manifest.json`.

`test_experiment.py` uses only the standard library and recomputes committed metrics; CI does not download or rerun the 1.15GB model. Full model execution was done locally with offline mode enabled after download. `requirements.lock` records the measured environment, including tokenizer dependencies `sentencepiece` and `protobuf` omitted from the upstream local extra.
