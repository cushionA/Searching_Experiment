# 4play browser integration

4play exposes a Firefox browser through its HTTP bridge. The bot-diagnostics integration adds it as a `ToolAdapter`, so an existing experiment can select it with `tools: ["4play"]`. It supports `goto` and `followLink`, keeps the browser session, and writes the returned response bodies and captured DOM into the normal Evidence run. It does not provide `fetch`, selector operations, challenge actions, screenshots, extension control, timezone emulation, session replacement, or budgeted navigation.

## Install and start

The self-hosted runtime pins `@lawlers/4play` 1.2.5 and extension 1.10. The recorded installation used Firefox 153.4.0esr; its built image ID and the full extension checksum are saved in [conditions.json](search-services/results/20261006-fourplay-integration/conditions.json). Rebuilding can update Firefox through the Debian package repository. `experiments/fourget-selfhost/start.py` prepares the proxy, CA, credentials, Firefox profile, and bridge. Start it in an environment where Docker and the required network access are already available:

```bash
python3 -B experiments/fourget-selfhost/start.py
```

The bridge listens on `http://127.0.0.1:3004/`; `/health` reports browser connection and runtime metadata. Keep generated credentials and runtime files private. The start procedure and shutdown command are documented in [the self-hosted runtime README](../experiments/fourget-selfhost/README.md).

Navigation first opens `about:blank` in the session's container, waits for that blank tab to complete, then runs ordinary `location.assign` in the page's MAIN world through a temporary script. The bridge and native controller, including Camoufox + 4play, share this helper. Firefox generates the request headers; the helper does not add input events. Target completion requires the same tab/container's HTTP document response and completed DOM, so the initial blank document cannot finish a target navigation. An HTTP 403 remains a captured 403 response.

The [2026-10-07 Joshin investigation](../reports/2026-10/joshin-navigation-fix.md) records the navigation comparison, successful retrieval without wheel operations, later root-URL 403s shared by Camoufox, and separate Cloud proxy 503s. It includes a full evidence checkpoint and a loopback fixture for redirects, real same-tab links, cookies, referrers, and GET/POST 403 responses.

The [Joshin グローブ search comparison](../reports/2026-10/joshin-glove-search.md) reached the top page with both ordinary 4play and Camoufox + 4play, but both search submissions returned 403 before product extraction or pagination. These observations used the managed Cloud proxy and do not reproduce a local direct-network success.

## Use from an experiment

Add `"4play"` to the experiment manifest's `tools` array, then use the standard framework plan and run commands to create a new run. For legacy CLI selection, set `BOT_DIAGNOSTICS_CLIENTS=4play`. The adapter connects to the bridge selected by the runtime configuration and records setup metadata before target navigation. A local integration smoke and a browser-search smoke are available:

```sh
node experiments/bot-diagnostics/fourplay-smoke.mjs .lab-output/fourplay-fixture
node experiments/bot-diagnostics/fourplay-search-smoke.mjs .lab-output/fourplay-brave --engine brave --query 'Python documentation'
node experiments/bot-diagnostics/fourplay-search-smoke.mjs .lab-output/fourplay-google --engine google --query 'Python documentation'
```

The fixture checks navigation, JavaScript-executed DOM capture, and `followLink`. Search smoke opens one fixed-origin search URL and does not visit result links. It distinguishes a visible citation from a resolved destination: only an observed result URL whose host is `docs.python.org` qualifies as a confirmed official-docs destination. Opaque Google `/goto?url=…` links retain their displayed citation and raw redirect URL, but the destination is unresolved and success remains unverified.

## Recorded findings

The 2026-10-06 integration report is [here](search-services/results/20261006-fourplay-integration/results.md), with conditions, aggregate data, target snapshot, and hashes alongside it. The Brave run returned HTTP 200 and captured the search result DOM with three resolved `docs.python.org` links; its search check passed. Direct Google browser navigation also returned HTTP 200 and a result DOM, but its one Python documentation citation used an opaque Google redirect, leaving zero resolved official URLs and `search_succeeded: null`. This is distinct from the separate 4get Google renderer attempt, which reached its configured failure page and observed no results; that run is linked from the report. The local fixture verified both navigation and `followLink`.

Git contains the run summaries and verification records. Full browser response/DOM blobs and ledgers remain at their original local run paths. Rechecking their byte hashes requires those local payloads or a fresh smoke run.

In the recorded container Firefox ran headful on Xvfb at 1280×720. Hardware GPU use was not verified. 4play exposes bridge response events and captured DOM, but not response headers, full request or failed-request coverage, Playwright events, or CDP. It does not preserve referrer across tabs. A completed DOM observation therefore does not establish complete network visibility, and a provider hint or absence of a challenge does not establish the site's policy or the cause of a response.

## Google parser behavior

Google can expose results as `/goto?url=<opaque token>`. The parser keeps the raw `href` and normalized URL as separate fields, and expands only known wrappers containing an explicit HTTP(S) destination, such as `/url?q=https://…`. It does not infer a destination from an opaque token. Displayed `<cite>` domains are recorded separately from resolved destinations and cannot alone establish search success.

The parser's representative HTML checks can be run with:

```sh
node --test experiments/bot-diagnostics/fourplay-search-parser.test.mjs
```
