"""Make one search against the local 4get API and preserve the response."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="Python documentation")
    parser.add_argument("--scraper", default="ddg")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run = Path(__file__).resolve().parents[2] / "lab-runs" / f"search-fourget-selfhost-{stamp}-{uuid.uuid4().hex[:6]}"
    run.mkdir(parents=True, exist_ok=False)
    url = "http://127.0.0.1:8084/api/v1/web?" + urllib.parse.urlencode({"s": args.query, "scraper": args.scraper})
    request = urllib.request.Request(url, headers={"User-Agent": "4get-local-smoke/1.0"})
    local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.monotonic()
    conditions = {"query": args.query, "scraper": args.scraper, "url": url, "time_utc": stamp, "timeout_seconds": 45}
    (run / "conditions.json").write_text(json.dumps(conditions, ensure_ascii=False, indent=2) + "\n")
    try:
        try:
            response = local.open(request, timeout=45)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read()
            http_status = response.status
        (run / "response.json").write_bytes(body)
        data = json.loads(body)
        results = data.get("web", [])
        success = http_status == 200 and data.get("status") == "ok" and isinstance(results, list)
        summary = {
            "http_status": http_status, "api_status": data.get("status"),
            "result_count": len(results) if isinstance(results, list) else None,
            "search_succeeded": success, "elapsed_seconds": round(time.monotonic() - started, 3),
            "results_observed": success and bool(results),
            "sha256": hashlib.sha256(body).hexdigest(), "evidence_path": str(run),
            "top_results": [{"title": item.get("title"), "url": item.get("url")} for item in results[:5]] if isinstance(results, list) else [],
        }
    except (OSError, ValueError) as error:
        summary = {"search_succeeded": False, "error": str(error), "evidence_path": str(run), "elapsed_seconds": round(time.monotonic() - started, 3)}
    output = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    (run / "summary.json").write_text(output)
    print(output, end="")
    if not summary["search_succeeded"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
