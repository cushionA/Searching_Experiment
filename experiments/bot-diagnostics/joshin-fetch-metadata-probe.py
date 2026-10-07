"""Compare Joshin entry responses with the observed navigation metadata headers."""

import argparse
import gzip
import hashlib
import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


BASE = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
}
SCHEDULE = [("same-origin", None), ("none", "?1"), ("none", None), ("same-origin", "?1")]
KEPT = {"content-type", "content-encoding", "server", "x-akamai-transformed", "x-cache", "via",
        "x-blocked-by", "x-denied-reason"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="A new evidence directory; existing runs are never overwritten")
    args = parser.parse_args()
    if not (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")):
        parser.error("Use the inherited environment HTTPS proxy")
    args.directory.mkdir(parents=True, exist_ok=False)
    write = lambda name, value: (args.directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    write("conditions.json", {
        "url": "https://joshinweb.jp/", "schedule": SCHEDULE,
        "scope": "4 entry-page probes, ordinary redirects, fresh cookie jar each, inherited proxy, verified TLS",
        "factor": "Sec-Fetch-Site x Sec-Fetch-User",
        "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "limitations": ["urllib uses HTTP/1.1 and its own TLS/proxy request path",
                        "redirects retain selected headers", "actual proxy egress IP not measured",
                        "no browser JavaScript or challenge actions", "comparison probe; no production header override"],
    })
    rows = []
    for index, (site, user) in enumerate(SCHEDULE, 1):
        headers = {**BASE, "Sec-Fetch-Site": site}
        if user:
            headers["Sec-Fetch-User"] = user
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        start = time.monotonic()
        try:
            try:
                response = opener.open(urllib.request.Request("https://joshinweb.jp/", headers=headers), timeout=30)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                raw = response.read()
                body = gzip.decompress(raw) if response.headers.get("Content-Encoding") == "gzip" else raw
                text = body.decode("cp932", errors="replace")
                (args.directory / f"{index}-body.bin").write_bytes(raw)
                row = {"index": index, "site": site, "user": user, "request_headers": headers,
                       "url": response.url, "status": response.status,
                       "headers": {key.lower(): value for key, value in response.headers.items() if key.lower() in KEPT},
                       "body_sha256": hashlib.sha256(raw).hexdigest(), "body_bytes": len(raw),
                       "search_input_present": 'id="suggest_input"' in text,
                       "access_denied_visible": "Access Denied" in text,
                       "elapsed_ms": (time.monotonic() - start) * 1000}
        except (OSError, ValueError) as error:
            # Keep the failed cell and stop instead of silently issuing a replacement request.
            rows.append({"index": index, "site": site, "user": user, "error_type": type(error).__name__})
            write("results.json", rows)
            raise SystemExit("Probe failed; inspect the retained cell and environment readiness") from None
        rows.append(row)
        write("results.json", rows)
        print(json.dumps({key: row[key] for key in ["index", "site", "user", "status",
                                                   "search_input_present", "access_denied_visible"]}), flush=True)


if __name__ == "__main__":
    main()
