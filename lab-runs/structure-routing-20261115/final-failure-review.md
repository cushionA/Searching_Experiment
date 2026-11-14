# Final C1 Failure Attribution and Model Value

Metadata-only final review, 2026-11-14. Sources include `score-final-main/`, `b0-results.jsonl`, `recovery-results.jsonl`, `challenge-results.jsonl`, `stage-c-score-v4.json`, and `artifacts/stage-c-transport-diagnostic/result.json`. No live requests or HTML/DOM inspection was done; primary results were not changed.

## Main comparison and failure attribution

The main comparison has 480 rows across 80 sites (240 rows per method). All 80 sites have operational records, but this is **not a full healthy-environment comparison**: results include upstream 503s, access conditions, and incomplete trials.

Current C1 reached 3 unique gold targets of 88. Page-level records show:

| Category | Count | Attribution |
|---|---:|---|
| HTTP 503 pages | 54 | Upstream/environment transport failure; not evidence of site block or ranking failure |
| Click timeouts | 15 | UI/navigation errors; overlap HTTP 200 page records |
| Other navigation error | 1 | Document budget error |
| Saved-path link missing | 0 | No such C1 category appears in scored metadata |
| HTTP 200, raw HTML saved, no navigation error | 113 | Eligible observations, not proven candidate-selection misses |
| Missing status and raw HTML | 27 | Evidence unavailable |
| Access-condition rows | 14 | 12 skipped after access stop and 2 robots-disallowed |
| Interrupted budget-reserved rows | 8 | Incomplete execution, retained separately |

These counts have overlap: the 15 click-timeout records are among the 129 HTTP 200 records. The 113 eligible page records are observations, not the full candidate alternatives. The data cannot establish how often C1 chose the wrong candidate. The 85 unreached targets cannot be assigned to ranking failure.

## B0, recovery, challenges, and Stage C

B0 contains 80 site rows: 45 completed fixed-URL runs, 33 rows with no valid manifest targets, and 2 access-stopped rows. Its 84 page records were HTTP 503. This does not establish B0 performance in a healthy target-page environment.

The recovery run has 12 records on sites 61 and 62, under a shared 15-document budget. Four page records returned 503. The 20 production challenge rows yielded 0 successes: 8 completed with `content_unchanged`, and 12 were skipped due to access conditions. The 20 accidental preflight rows are excluded.

Stage C v4 froze 100 targets per site for sites 01 and 08 (manifest SHA256 `56e0834d4ace84cb3d984ad77f3c72de66ae875b76dc209974a4dc0470aa2a1e`). Both bulk attempts returned 503: 0 useful HTML documents and 0 independent targets reached. Stage C reserved 120 selection documents, plus 2 bulk and 2 transport-diagnostic documents, for 124/400 total. The 10/30/100 success milestones were unmet; cumulative and incremental document, request, and time costs remain **null**, not zero.

The two-document transport diagnostic returned 503 for both inherited-proxy HTTP/1 and browser-direct arms. Curl received 404 for robots.txt and Git connectivity succeeded, while browser target-page requests still returned 503. Connectivity was partly restored, but the target-page comparison was not healthy. Do not attribute these 503s to site-side blocking or to the C1 algorithm.

## Recommendation

**Do not adopt C1 as a production Skill.** Volume and G/R benefit are not established, and the operational results do not represent a full healthy-environment comparison. Keep infrastructure failures separate from algorithm performance.

**Skip C2 for this experiment.** The observed transport, UI/navigation, access, and evidence failures are not model-fixable. Actual selection errors remain unknown because scored metadata does not preserve all available alternatives at each decision. Local ranking usage is **0**, external API usage **0**, and native model usage **unknown**. Reconsider a small offline comparison only if an audit of frozen candidate sets first shows viable alternatives that C1 ranked poorly.

Keep B2 conditional rather than rejecting it as universally unsuccessful: primary C1 reached 3 unique targets. Any comparison should wait for a healthy and adequately covered evaluation.

## Skill integration and candidate audit

The source Skill's `parser_flow` produces representative path hints from static AST analysis. A minimal integration proposal can pass those start, candidate, and transition hints to a budgeted non-LLM runner that retains frozen graph/rules/path provenance, raw HTML/DOM, state and cost records, and independent G/R scoring. This is a design proposal, not measured production performance.

Reuse the existing `candidate-audit` for candidate revision, dependencies, distribution conditions, and installation checks. It audits candidate assets; it is not a product-performance score.
