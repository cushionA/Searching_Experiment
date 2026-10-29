# Tactical intent snapshot benchmark v1

A small **consistency diagnostic**, not a game, policy rollout, win-rate evaluation, or proof of strategy/fun.

48 outputs = eight recorded history series × two personalities × three snapshots. `dev_lure` and `dev_condition` are development; six other series are prospectively held evaluation. The same author designed rules and labels: this is not a blinded holdout. No output-based prompt, rule, fixture, or label changes are allowed. No training, forced diversity, handicap, or difficulty adjustment.

All methods receive identical English observations, personality, goal, resource state, question and six preset descriptions. English isolates this English-only model's decision behavior from Japanese-language transfer. Observations contain past visible behavior only; there is no hidden enemy policy or future outcome. Gold, reasons, source code defining future snapshots, and scores stay outside the model container. Sequence IDs/split/version bookkeeping are not in the model's textual state.

`eval_guard` and `eval_dodge` share the current state but differ in history. Sparse evidence, recent reversal, recovered/depleted resources, clock pressure, bait→reaction→follow-up/abandon and pressure→guard→switch are represented. Two personalities intentionally may choose the same action: diversity is not rewarded. Only one ambiguous sparse-evidence snapshot restricts the patient personality more. Personality sensitivity is weakly covered and must not be overclaimed.

The six presets change the same execution coefficients in `presets.json`. The numbers are illustrative, not calibrated game physics and not a uniquely correct regression target. Gold records multiple acceptable options and a separately defined clearly-bad set. Anything else is unendorsed, not necessarily a clear mistake. `hold` preserves a balanced plan, not an unconditionally safe optimum. All six fixed strategies are reported, plus one selected on development only and a stronger adaptive baseline using recency, repeated evidence, health, stamina, clock and personality. The baseline's text checks intentionally match this synthetic vocabulary; its score is not general language understanding.

## Reproduce

Python 3.12+. From repository root:

```sh
python3 -B -m unittest discover -s experiments/tactical-intent -p 'test_*.py'
python3 experiments/tactical-intent/bench.py benchmarks/tactical-intent-20261030
# Use saved inputs/gold; do NOT rebuild or edit after reading results.
mkdir -p /tmp/tactical-trial/model /tmp/tactical-trial/wheels
python3 experiments/tactical-intent/download_model.py
python3 -m pip download --only-binary=:all: --require-hashes --dest /tmp/tactical-trial/wheels -r experiments/tactical-intent/requirements.lock
docker pull python:3.12-slim@sha256:6b25cbe6577518889d553cdd5a5da97d259348d8f6e45b527dd731caaba27a07
python3 experiments/tactical-intent/launch.py
python3 experiments/tactical-intent/score_model.py
```

The download script uses pinned saved HF metadata, size checks and LFS hashes. Resume/re-download must stay within the user's cumulative budget; these commands are documentation, not permission to reset any existing trial. Vendor prompt is unchanged from official `opendecider==0.9.0` wheel (Apache-2.0; LICENSE/NOTICE adjacent). Native ONNX CPU inference follows the official model card without installing the SDK or Torch. ONNX q8 revision `5137ae7b4f1cf8c55f1d05d3f5cdd3694d92da3e`; nano tokenizer/config revision `1e1f0a7322e446c5ae9bbe840df11680f18a752a`, verified byte-identical to exported base revision `beeeb640f3333aec4ef78ed3b7bd0605f973a590`.

The candidate runs in a nonroot, 2-CPU, 8GiB, read-only, network-none Docker container. Only public wheels/model/runner and synthetic observations enter via stdin tar; no workspace, secrets, gold, or personal files are mounted. We inspect the actual execution path, not assume all external models need new permission. Standard PyPI wheels only. No pickle model, trust_remote_code, external API, accelerator, or training.

## Timing and output contract

Predeclared illustrative decision deadline: **250ms**, intended as a low-frequency decision budget, not a universal game requirement. Initial import/load and first inference are separate; warm statistics describe 47 subsequent *different* inputs, not repeated microbenchmarks. Each request measures JSON preparation, tokenization, inference, conversion and output-contract check. Minor instrumentation overhead and final serialization are not delivery/network latency. First-available cold metric sums import/load and first request; a short comparator loop between them is excluded. First output may already be expired; metric naming refers to produced output availability, not gate acceptance.

Accept outputs only with matching observation timestamp and state version, a known preset, and age ≤250ms. A new state version invalidates pending results even when deadline remains. On expiry continue previous low-level coefficients and request a new high-level decision; do not block animation/control. Tests simulate 60Hz continuing ticks and delayed/stale/invalid rejection. There is **no actual asynchronous Unity integration**; the static runner has no external changing world, so naturally occurring stale-version errors are not measured. Expiry uses measured computation time added to the synthetic timestamp. Do not call the 48-output p95 a stable population estimate.

The stored histories have predetermined reactions. A model choosing a different first action still receives the recorded continuation; hence even a perfect score would not establish actual誘導/read-and-counterplay or causal policy quality. Proceed to a short closed loop only if quality and responsiveness justify it.
