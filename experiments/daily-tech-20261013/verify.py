"""Offline evidence integrity checks; never imports downloaded source code."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "benchmarks/daily-tech-20261013"


def main():
    manifest = json.loads((DATA / "primary-manifest.json").read_text())
    checked = 0
    errors = []
    for item in manifest["sources"]:
        if "error" in item:
            errors.append({"name": item["name"], "error": item["error"]})
            continue
        content = (DATA / item["file"]).read_bytes()
        assert len(content) == item["bytes"], item["name"]
        assert hashlib.sha256(content).hexdigest() == item["sha256"], item["name"]
        checked += 1
    discovery = json.loads((DATA / "discovery.json").read_text())
    assert len(discovery["repos"]) == 8
    renamed = next(r for r in discovery["repos"] if r["repo"] == "kreuzberg-dev/kreuzberg")
    assert renamed["metadata"]["full_name"] == "xberg-io/xberg"
    assert renamed["releases"][0]["tag_name"] == "v1.3.8"
    # Static source evidence only: these assertions are NOT a Docling execution.
    source = (DATA / "docling-html.txt").read_text()
    assert 'tag.get("shadowrootmode", "")' in source
    assert '"closed",' in source and '"open",' in source
    series = json.loads((DATA / "smollm-series.txt").read_text())
    ids = {item["id"] for item in series}
    assert "HuggingFaceTB/SmolLM3-3B" in ids
    print(json.dumps({"source_hashes_verified": checked, "recorded_fetch_errors": errors,
                      "repo_snapshots": 8, "model_series_entries": len(series),
                      "runtime_poc_executed": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
