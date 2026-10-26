# PageSift static source audit (2026-10-27 JST morning)

Candidate: `YOLOW92/pagesift` v0.73.1, commit `6982f9ea4ebd9989a861e1b9c4b0c5dc223e7de5`.

This experiment executes only our Python standard-library collector/auditor. Candidate Rust, JS, Python packages, installation scripts, benchmark runners and models are **not executed**. Source snapshots retain their upstream licenses and are evidence, not vendored runtime dependencies.

From repository root, Python >=3.12:

```sh
python3 -B experiments/pagesift-source-audit/collect.py
python3 -B experiments/pagesift-source-audit/audit.py
```

`collect.py` makes read-only requests to official GitHub/registry endpoints and saves URL, UTC timestamp, bytes, SHA256, errors and elapsed time. Code files are pinned; registry/latest observations are time-dependent. Recollecting intentionally replaces this date's snapshot: use a new output directory for a later run. `audit.py` works offline on the saved inputs, verifies hashes and version correspondence, extracts constants without importing candidate code, and recalculates denominators from the author's reported counts. The hypothetical 1M-seed calculation is not an observation of the actual input CSV. No throughput, quality, coverage or memory benchmark was run locally.

Results: `benchmarks/daily-tech-20261027/static-audit.json`; report: `reports/2026-10/2026-10-27-morning-tech.md`. Local repository tests are reported separately and do not certify PageSift.
