#!/usr/bin/env python3
"""Save current GitHub repo/release metadata for the Oct 12 daily-tech review."""
import json, time, urllib.request
from datetime import datetime, timezone

REPOS = [
    "microsoft/playwright", "docling-project/docling.rs",
    "commoncrawl/web-graph-embeddings", "fastino-ai/GLiNER2",
    "unclecode/crawl4ai", "webrecorder/browsertrix-crawler",
    "RapidAI/RapidOCR", "adbar/trafilatura",
]

def get(url):
    req = urllib.request.Request(url, headers={"Accept":"application/vnd.github+json", "X-GitHub-Api-Version":"2022-11-28", "User-Agent":"Searching-Experiment-daily-tech"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)

out = {"collected_at_utc": datetime.now(timezone.utc).isoformat(), "source":"GitHub REST API", "repos":{}, "errors":[]}
for repo in REPOS:
    try:
        meta = get(f"https://api.github.com/repos/{repo}")
        releases = get(f"https://api.github.com/repos/{repo}/releases?per_page=3")
        out["repos"][repo] = {
            "stars":meta.get("stargazers_count"), "license":(meta.get("license") or {}).get("spdx_id"),
            "pushed_at":meta.get("pushed_at"), "default_branch":meta.get("default_branch"),
            "description":meta.get("description"), "html_url":meta.get("html_url"),
            "latest_releases":[{"tag":x.get("tag_name"),"published_at":x.get("published_at"),"name":x.get("name"),"url":x.get("html_url"),"body":x.get("body")} for x in releases]
        }
    except Exception as e:
        out["errors"].append({"repo":repo,"error":type(e).__name__+": "+str(e)})
    time.sleep(.15)
with open("benchmarks/daily-tech-20261012/github-sources.json","w") as f:
    json.dump(out,f,ensure_ascii=False,indent=2)
print(json.dumps({"repo_count":len(out["repos"]),"errors":out["errors"],"collected_at_utc":out["collected_at_utc"]},ensure_ascii=False))
