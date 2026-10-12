"""Refetch public evidence into a NEW directory; stdlib, no install or execution.

Usage: python3 -B experiments/daily-tech-20261013/collect.py /tmp/daily-tech-new
Reads the saved URL manifest. Changing upstream responses is expected.
"""
import datetime
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
SAVED = ROOT / "benchmarks/daily-tech-20261013"


def main():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=False)
    sources = json.loads((SAVED / "primary-manifest.json").read_text())["sources"]
    discovery = json.loads((SAVED / "discovery.json").read_text())
    for repo in discovery["repos"]:
        stem = repo["repo"].replace("/", "__")
        for suffix, path in [("metadata", ""), ("releases", "/releases?per_page=3")]:
            sources.append({"name": stem + "-" + suffix,
                            "url": "https://api.github.com/repos/" + repo["repo"] + path})
    rows = []
    for item in sources:
        row = {"name": item["name"], "url": item["url"]}
        row["observed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            with urllib.request.urlopen(row["url"], timeout=30) as response:
                body = response.read()
                row["resolved_url"] = response.url
            row.update(file=row["name"] + ".txt", bytes=len(body),
                       sha256=hashlib.sha256(body).hexdigest())
            (output / row["file"]).write_bytes(body)
        except Exception as error:
            row["error"] = str(error)
        rows.append(row)
        (output / "manifest.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
