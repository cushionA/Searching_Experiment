# THX-01 fixed diagnostic (2026-10-29)

Completed one isolated CPU run, repair0. See reports/2026-10/2026-10-29-evening-radar.md.

Artifacts in benchmarks/research-repro-radar/2026-10-29: inputs/gold/manifest, complete predictions, scores, time/RSS, command and dependency hashes. Model weights/wheels are not committed. Prepare a disposable trial directory with those public inputs and scripts; obtain only files listed in model-download.json at HF revision b323afcef149f9288a9d0daef4badd67b9b87c61, verify hashes, and obtain binary wheels listed in trial-files.json (Torch2.7.1+cpu from official CPU registry, Transformers5.17.0 and thx01 1.0.0 from PyPI). Downloads must remain within the active run budget; do not silently restart a completed trial. The exact pinned Python image is in launch_trial.py.

launch_trial.py streams only public model/wheels/input/adapter into nonroot, network-none, readonly Docker with resource caps; gold stays outside. run_baseline.py uses stdlib and must run under the same isolation. score.py consumes predictions and gold outside the candidate container. Do not import external model code into the host. One run is not representative or a general superiority claim; the original fixture was fixed October28, and prior Japanese/Laya results are stored comparisons only.
