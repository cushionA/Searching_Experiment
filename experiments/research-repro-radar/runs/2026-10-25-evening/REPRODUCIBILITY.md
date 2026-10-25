# 2026-10-25 evening run reproduction materials

This is a reading-only research run, not a candidate-model reproduction.

- Python 3.12.14; jsonschema 4.26.0; native validator 38 tests passed.
- Upstream Skill `fe6925cd75838a9b52130f284cb3b931bfaf36db`; per-file hashes in start-manifest.json.
- Canonical parent `2709554fb84c0a16bce4fd4ba035dfe6cd251f95`; four state hashes and 9,375 pending candidates verified before restoration.
- `RADAR_LLM=native`; requested host model gpt-6-luna; provider service model ID unknown.
- Real host probe envelope identity and result in probe-ledger.json. Native request/response envelopes are private temporary evidence; hashes and host task IDs are retained publicly. A replacement host run must prepare fresh IDs and obtain real responses, never reuse this run's successful probe as a new call.

## Methods and inputs

1. Restore four canonical JSON files into the Skill-local `state/` directly, compare bytes/hashes. No morning or weekly state.
2. Use `native_judge.py prepare --purpose probe`, authorized native host spawn, wait for completion, `native_judge.py validate`; only exit0 allows further actions. The existing collector gate validates paths supplied through RADAR_NATIVE_PROBE_REQUEST and RADAR_NATIVE_PROBE_RESPONSE. No CLI or paid fallback.
3. Existing collect.py fetches trending/new and ten interest queries. collect_supplements.py fetches primary feeds/APIs. collect_arxiv_supplement.py and paginate_arxiv.py preserve arXiv metadata separately, merged by canonical DOI/arXiv/id/URL key after paginated GitHub collection. 40 arXiv pages and three GitHub coding search pages completed.
4. normalize_dates.py preserves raw date components, pads complete known dates, and treats partial dates as unknown. All 5,753 records were scored by `rank_candidates.py <all-candidates.json> --state-dir state --today 2026-10-25 --output <ranking.json>` from the Skill directory. The preview used no state and did not increment carry. Final pool20 is covered by review-targets/reviews.
5. fetch_review/followup/model_details/licenses/project scripts only read public documents. Native envelopes use the upstream candidate schema with exact candidate IDs. Eight host batches produced32 judgments over27 unique candidates. Parent checked50 quotations against payload sources and all62 primary files against hashes. One initial quotation was repaired once; final accepted responses were within180seconds.
6. Preserve prior unfinished records dropped by ranking relevance/resource cutoffs. Keep original first-carry dates. Add newly collected, nonhistorically-completed records as pending; neither digest nor a native test/defer/reject label alone completes a candidate. retention-ledger.json lists nine new pending IDs and zero lost old IDs. Oldest carry2026-10-12 is inside28days.
7. `write_report.py` builds report, reading ledger and benchmark metadata. From Skill-local directory, `record_run.py <report.json> --state-dir state --today 2026-10-25` records a valid zero-recommendation run. It recorded zero completed IDs. Copy all four state files to canonical evening-state.
8. Run `python3 -B experiments/research-repro-radar/runs/2026-10-25-evening/verify.py`. It verifies hashes, counts, pool coverage, preservation, freshness uniqueness and exact state equality. A clean checkout must first restore canonical files to Skill-local state before this verifier.

No candidate dependencies or weights were installed/downloaded. PDF text extraction used the existing pdftotext; its font mismatch warning is not an inference error. Weight sizes are metadata only; CPU time, RAM, quality and throughput remain unmeasured. No candidate execution retry, Kaggle submission, training, auth change, mail, cron, PR19 or Moli/Lightpanda work.
