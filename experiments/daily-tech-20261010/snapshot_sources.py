"""Read-only, bounded primary-source snapshot; uses existing gh authentication."""
import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request

REPOS = ["docling-project/docling", "RapidAI/RapidOCR", "apify/crawlee",
         "lightpanda-io/browser", "datalab-to/marker", "unclecode/crawl4ai"]

def fetch(item):
    name, endpoint, kind = item
    start = time.perf_counter()
    result = {"name": name, "url": endpoint if kind == "url" else "https://api.github.com/" + endpoint,
              "observed_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        if kind == "url":
            with urllib.request.urlopen(endpoint, timeout=30) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("source body exceeds 2 MB limit")
            value = raw.decode("utf-8")
        else:
            p = subprocess.run(["gh", "api", endpoint], capture_output=True, timeout=45, check=True)
            value = json.loads(p.stdout)
            if kind == "repo":
                fields = ["full_name", "html_url", "created_at", "updated_at", "pushed_at",
                          "stargazers_count", "forks_count", "archived", "license", "default_branch"]
                value = {key: value.get(key) for key in fields}
            elif kind == "releases":
                fields = ["tag_name", "published_at", "created_at", "html_url", "prerelease", "body"]
                value = [{key: row.get(key) for key in fields} for row in value]
            elif kind == "prs":
                value = [{key: row.get(key) for key in ["number", "title", "body", "html_url", "state", "draft", "updated_at"]}
                         | {"head_sha": row["head"]["sha"], "head_ref": row["head"]["ref"]} for row in value]
        result["data"] = value
        result["sha256"] = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["seconds"] = time.perf_counter() - start
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--supplement", action="store_true")
    args = parser.parse_args()
    items = []
    for repo in REPOS:
        items += [(repo, "repos/" + repo, "repo"),
                  (repo + "/releases", "repos/" + repo + "/releases?per_page=5", "releases")]
    items += [("open_prs", "repos/cushionA/Searching_Experiment/pulls?state=open&per_page=50", "prs"),
              ("homes_robots", "https://www.homes.co.jp/robots.txt", "url")]
    if args.supplement:
        items = [("NuExtract_code", "repos/numindai/nuextract", "repo"),
                 ("NuExtract_model", "https://huggingface.co/api/models/numind/NuExtract3", "url"),
                 ("NuExtract_GGUF", "https://huggingface.co/api/models/numind/NuExtract3-GGUF", "url"),
                 ("RapidOCR_model_licenses", "https://raw.githubusercontent.com/RapidAI/RapidOCR/v3.10.0/python/MODEL_LICENSES.md", "url"),
                 ("Docling_OCR_change", "repos/docling-project/docling/commits/c6ef3c413b5bdb98bd2ae81a0a3d62ce711f58ec", "json"),
                 ("Docling_RapidOCR_compatibility", "repos/docling-project/docling/commits/0e1567bbb8a543b364aec234fc1abe2ff9aff7ba", "json"),
                 ("pypi_old", "https://pypi.org/pypi/docling-slim/2.135.0/json", "url"),
                 ("pypi_new", "https://pypi.org/pypi/docling-slim/2.137.0/json", "url")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(fetch, items))
    with args.output.open("x") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps({"sources": len(results), "errors": sum("error" in r for r in results)}))

if __name__ == "__main__":
    main()
