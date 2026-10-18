# 2026-10-18 evening evidence

This is literature reading and metadata verification, not successful candidate inference.
Canonical base: efd3c909d2d729cfc0e8f189a01dd213bd400257.
Upstream skill: 55ceed634506c5942d6a8e84c71d7e80acc103ff.

- `report.json` / reports/2026-10/2026-10-18-evening-radar.md: final decisions, pending-only digest and continuation stops.
- `all-candidates.json`, `ranking.json`: all 2,516 discovered artifacts, 6,049 processed including carried records, review_pool20.
- `review-targets.json`, `review-evidence.json`, `reviews.json`: 29 reading reviews, exact source text/URLs/timestamps/hashes, verified verdicts.
- `native-host-ledger.json`: host-requested gpt-6-luna, serving model unknown, official probe and 49 candidate requests; actual private handoff files are not published.
- `enrichment-ledger.json`, `model-details-ledger.json`: follow-up primary documents and missing license errors.
- `blocked-continuations.json`: previous stopped experiments remain stopped.
- `benchmarks/research-repro-radar/2026-10-18-evening/metadata-audit.json`: file-size arithmetic and metadata audit timing, never model performance.

Run offline verification from repository root:

```bash
python3 -B experiments/research-repro-radar/runs/2026-10-18-evening/verify_run.py
```

For exact staged byte preservation add `--check-index`. The local `.gitattributes` keeps original source bytes, including CRLF. The initial queue and intermediate rank are private local scratch, recoverable from the base Git commit and saved collection; no existing raw artifacts were removed.

Collectors are not an autonomous scheduler. Before any future collection, the host must use the current upstream skill, official native probe, and the current canonical evening state. Never blindly run this dated directory again or reset repair limits. Both request preparation helpers use exclusive creation and refuse existing request files; they are dated-run helpers, not automatic resume commands. The command `record_run.py report.json --state-dir state --today 2026-10-18` was run once from the skill root; recommended/completed lists are empty and all prior pending IDs are preserved.
