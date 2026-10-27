# October 27 evening evidence

No model inference completed. Do not count fixed fixture baseline success or archived-answer scoring as TinyJev/Jev/TavernAgent inference.

## TinyJev

`tinyjev/eval/inputs.json` and `gold.json` were frozen before any inference, with hashes in `fixture-manifest.json`. The fixture has separate NLP classification and game action-choice groups. `make_fixture.py` documents the synthetic construction. `run_baseline.py INPUTS OUTPUT` uses only input; `score.py INPUTS GOLD BASELINE OUTPUT` is independent of candidate code. Baseline 48/48 is a diagnostic over deliberately simple cases, not generalization evidence. Cold/warm candidate latency, RSS, macro-F1, throughput and cost remain unknown.

Candidate source: https://github.com/ankit-aglawe/tinyjev at `fec868b6b44ac5fddac64ff87d318ccceaff27ea` (MIT). Model: https://huggingface.co/AnkitAI/TinyJev-0.6B at `c559c2f7ea95069f92af8b999f512345bbb87d22`. Download hashes and metadata are saved, weights and third-party packages are not committed. Torch CPU 2.7.1+cpu / Transformers4.57.1 wheels were downloaded for Python3.12; installation did not succeed. `run_model.py` is **unexecuted** preparation.

PyPI 0.2.0 was unavailable (returned versions0.1.0–0.1.3). The one repair switched to pinned official source. Docker offline installation then exited1 with Permission denied `/wheels`. Stop at this repair cap. Do not retry or use Kaggle to bypass it without separate authorization for further repair. No credential change or automatic approval-review rejection occurred.

## AutoTavern scorer audit

Source: https://github.com/MCG-NJU/AutoTavern at `fb61201a557ddd36a411cf44aac51ce4d9b606ce` (MIT; third-party game rights remain). Only `tavernbench/{__init__,report,score,chance}.py`, public eval labels/prompts and four public saved-answer files were supplied, with `autotavern_audit.py`. No simulator, live game, training, API credentials or model calls.

A1.9MB tar of those public files was streamed on stdin into an empty tmpfs in `python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f`. Container flags: `--network none --read-only --cap-drop ALL --security-opt no-new-privileges --memory 1g --cpus 2 --pids-limit 64 --tmpfs /work:rw,size=64m --tmpfs /tmp:rw,size=16m`, outer timeout120s. No host filesystem was mounted. Extraction used Python tarfile `filter="data"`. `audit.py` runs the official report and independently scores the232 exact-label cases. Success requires368 labels/prompts, all four systems plus chance, score range[0,1], and JSON output. The container exited0.

`autotavern_audit_result.json` contains both scores computed from authors' archived outputs and our **scorer-only** wall time/RSS. All archived latency fields belong to the authors; never present them as this run's model measurements. Scenario-average play and micro exact-label accuracy measure different subsets/weighting.

Kaggle: latest helper doctor exited2 PackageNotFoundError, quota unknown, no submit. SMTP, schedules and credentials unchanged.
