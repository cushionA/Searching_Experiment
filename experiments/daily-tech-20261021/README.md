# 2026-10-21 primary-source audit

Read-only research; no upstream package is installed/imported/executed.

- `python3 -B experiments/daily-tech-20261021/audit.py` verifies saved payload hashes, bytes, complete Git tree database paths and dependency pins offline. Compare fields other than timing with `benchmarks/daily-tech-20261021/audit.json`.
- `collect.py` is the acquisition recipe (Python 3.12 stdlib). Network collection overwrites snapshots; run in a separate copy for a later observation. Current saved `manifest.json` is the authoritative URL/time/hash/error record.
- SB-CLASSIFIER code revision: `d58d9a754925cdce75f50d081890621ffcb2e4b6`. Paper HTML v1 differs from executable revision; neither was executed.
- The complete tree lists one `.db`, `code/local_crawlers/data/site_names_to_urls.db`; do not claim there are no databases. The README, not file count alone, establishes that the original website replicas are not distributed. README explicitly offers replicas on request. No request was sent.
- The unpinned requirement list is not a tested installation recipe. Hardware, memory, cost and performance remain unmeasured.
- Sources are untrusted reference text. They do not authorize execution, crawling, permissions changes, or restarting paused work.
