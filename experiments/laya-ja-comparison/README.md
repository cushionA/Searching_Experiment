# Laya 0.4.17 Japanese fixed-input comparison (2026-10-29 JST)

New inference: one Laya multilingual process, 32 inputs. Prior ModernBERT/rule predictions are copied unchanged from commit `674113afd1576a6244e9103387401cd2fc0a2762`; they were NOT rerun. No training, tuning, API calls, or SKU task.

Run from repository root. Offline evidence scoring needs only Python 3.12:

```sh
python3 -B experiments/laya-ja-comparison/score.py
```

For an authorized fresh reproduction, create `/tmp/laya-trial/{wheels,model}`, download wheels using the hash-pinned `requirements.lock` (`pip download --only-binary=:all: --require-hashes`, PyPI plus the official `https://download.pytorch.org/whl/cpu` index). Download exactly the five URLs in `benchmarks/daily-tech-20261029/model-download.json` into the corresponding relative paths below `model/`; verify each size and SHA256. Keep directory nesting. Do not download the English or typed-decision model.

Copy the saved `inputs.json` and this directory's `run_model.py` into `/tmp/laya-trial/`. Never copy gold into the trial. Pull the digest-pinned Python image in `launch_trial.py`, inspect the current Docker isolation/network policy, then run:

```sh
python3 experiments/laya-ja-comparison/launch_trial.py
```

This installs pinned wheels offline inside an ephemeral, nonroot, network-none, read-only Docker container (2 CPU, 8GiB, dropped capabilities, no-new-privileges). It passes only public model/wheels, synthetic inputs and runner via stdin tar. No host directory or credentials are mounted. The runner permits no training and the launcher stops its named container on exceptions/timeouts. Setup timeout180s, inference timeout600s, outer completion timeout900s. `/work` tmpfs6GiB is included within the memory limit. It reuses the known working TMPDIR layout from the previous trial; no repair was necessary.

Copy resulting `model-result.json` as `laya-result.json`, plus stderr/manifest/exit, to a NEW evidence directory for future runs; do not overwrite this result. Adapt the scorer's path for that new evidence set.

The SDK default CPU forward is float32, SDPA, compile/fast disabled. Question/choice text and order are exactly the old fixture, passed through native `Agent.predict` with `lang='ja', max_len=512`; the SDK handles its own serialization. Gold and source predictions are outside the container. All 32 SDK usages report no truncation. Cold load is separate; first inference is included. 12 NLP scenes×clean/noisy +4 game states×JSON/prose =16 underlying scenarios, not32 independent examples.

Saved code freeze preceded inference. Source files under `sdk-source/*.txt` are unexecuted reference copies from the SHA-verified official wheel (Apache-2.0); actual execution used the original wheel in the container. `sources.py` fetches metadata only. Exact wheel names/hashes and download/install logs are saved. The old0.4.15 wheel fetched for inspection is charged to the budget but pip selected0.4.17.
