# Browser comparison: Lightpanda, Patchright, and Obscura

This page summarizes selected results from local fixture runs and bounded site observations. It contains aggregate metrics only; raw run records and archived evidence remain in the original workspace and are not part of this public change.

## Local fixture performance

The core benchmark used the same loopback pages, Cookie continuity, and DOM extraction, with five runs of eight pages per configuration. It excluded the SessionPool and ToolAdapter pipeline. Process-tree PSS was sampled every 50 ms; medians are shown. Different drivers were used for official Obscura (Playwright) and patched Obscura (Puppeteer), so the difference does not isolate the native patch.

| Configuration | Median PSS (MiB) | Fetch + DOM extraction (ms) |
|---|---:|---:|
| Lightpanda 1.0.0, compatibility shim | 107.88 | 15.88 |
| Patchright | 487.68 | 29.21 |
| Official Obscura no-render | 159.81 | 13.27 |
| Patched Obscura no-render | 117.74 | 10.82 |

All 160 measured core pages succeeded. The benchmarks used a 12-second CDP protocol timeout. Before completing the site observations, this was raised to 36 seconds to exceed the existing navigation and observation deadlines; it adds no fixed wait. The document-request guard was added after performance and site measurements and verified separately with six local ToolAdapter checks.

The pipeline comparison also used five runs of eight pages, through the shared ToolAdapter, SessionPool, robots checks, 100 ms observation wait, evidence accounting, and request-budget checks. This table reports median aggregate process-tree RSS; RSS counts shared pages in each process and should not be compared with the PSS figures above.

| Configuration | Median aggregate RSS (MiB) | Page processing (ms) |
|---|---:|---:|
| Lightpanda compatibility + Pool | 174.45 | 122.26 |
| Patchright + Pool | 1,208.64 | 140.13 |
| Patched Obscura + Pool | 183.03 | 120.06 |

All 120 measured pipeline pages succeeded.

All measured pipeline fixture cells succeeded. These results were recorded before the CDP timeout change described above.

These short fixture measurements characterize the tested setup only. Host load, sampling interval, process startup, shared memory accounting, and driver differences limit small-difference interpretation. They do not establish real-site speed, long-run memory use, or WAF performance.

The Lightpanda SessionPool check compared otherwise identical ToolAdapter fixture runs, seven repetitions of ten measured pages per condition. Both included the same robots checks, evidence handling, and 100 ms observation wait.

| Condition | Median aggregate RSS (MiB) | Fetch, observation, and extraction (ms) |
|---|---:|---:|
| Without Pool | 175.35 | 121.28 |
| With Pool | 176.11 | 122.05 |

The observed differences (+0.43% RSS, +0.64% time) are small in this fixture and do not establish a persistent Pool cost.

## Bounded site observations

The same configured four sites were observed under existing per-site budgets. These were collected at different times and with different failure histories, so they are outcomes rather than a controlled ranking.

| Site | Lightpanda with Pool | Patchright after TLS repair | Patched Obscura |
|---|---|---|---|
| Amazon | Home page 200; target page 503 | 202 challenge | 202 challenge |
| Joshin | Home page and two configured pages 200 | Same | Same |
| HOME'S | Home page and two configured pages 200 | Same | Same |
| Indeed | 403 challenge | Home page and configured search page 200 | Same |

These observations concern configured main-document pages. Some subresources were stopped by the existing evidence budget. They do not establish complete page functionality, generalized block avoidance, or a causal engine comparison.

## Validation and limits

- The final Python regression suite passed 63 tests.
- The patched Obscura interception smoke passed 37 checks; the redirect smoke passed 6 checks, including rejection before a fourth redirect target reached the local server.
- Upstream Obscura validation passed 336 related release tests and 1,778 full-suite tests (4 skipped). The obstacle course passed 32 of 33 checks; `observer-intersection` remained unsupported, matching the official no-render result.
- Lightpanda's table API shim resolves the tested `insertRow`/`insertCell` compatibility gap. Detector outputs still include red and unevaluated items; `bot:false` is not evidence of detection evasion.
- Patched Obscura supports unchanged GET continuation or rejection in the tested budgeted path. Request rewriting, fulfillment, and stealth transport are outside its support. CDP redirect history is unavailable, so document navigation is conservatively capped at the initial request plus three redirects per observation. The saved-body budget is not a hard cap on bytes transferred.
- CDP `awaitPromise` cannot wait for a fetch that is paused pending CDP approval; the tested workaround waits in Node and reads state with synchronous `evaluate`. The document-request cap counts navigations from all frames and JavaScript, not only HTTP redirects. Obscura's table API remains incomplete: `HTMLTableRowElement.insertCell` is unavailable, so the Lightpanda compatibility shim does not complete the Rebrowser detector on Obscura.
- SessionPool behavior is intentionally small and is not Crawlee API compatibility. Its 403/429/robots behavior was exercised with local fixtures; Cookie values are kept in memory during a run.

## Evidence provenance

The measurements and site observations were summarized from internal lab runs 009 and 012. Raw runs 008–012 and their ZIP snapshots remain in the original workspace; they are not included in this public PR. No raw response bodies, Cookie values, or request/response headers are reproduced here.
