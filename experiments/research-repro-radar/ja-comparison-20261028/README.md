# Japanese decision model: bounded comparison

The October28 trial ran32 synthetic inputs once with pinned `argos1111/modernbert-ja-310m-jev@07cda23579443e7a33c0f474114279fa032340d6`. It is separate from the stopped TinyJev trial. Data, hashes, raw outputs and metrics live in `benchmarks/research-repro-radar/2026-10-28/`.

For a separately authorized replay, copy these Python scripts into a dedicated scratch directory with the frozen `inputs.json` and `gold.json`. `score.py` expects those files plus `baseline.json` and `model-result.json` beside it. `launch_trial.py` expects the pinned model files in `model/` and exact wheel files from `trial-files.json` in `wheels/`; download verification and budget checks must be repeated. It streams only model/wheels/inputs/run_model to an isolated Docker container; it does not send gold, credentials, or host mounts. The committed launcher is the successful single-repair version using `/work/pip-tmp`. Do not treat this README as permission to repeat a stopped trial or reset its repair count.

`make_fixture.py` documents fixture construction before inference. Do not regenerate/adjust gold from model outputs. `run_baseline.py` only reads inputs. `score.py` checks identity coverage and independently computes accuracy/MacroF1 separately for NLP and game.

Source card: https://huggingface.co/argos1111/modernbert-ja-310m-jev . Model weights CC BY-SA4.0, base MIT, training data attribution/license details retained in the saved card. Standard Transformers5.17.0 and CPU Torch2.7.1+cpu, safetensors, `trust_remote_code=False`. These scripts do not reproduce Jev itself or author benchmark scores. No model weights or wheels are committed.
