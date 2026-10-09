"""Recompute and validate saved evidence without downloads or model inference."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parent

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    protocol_bytes = (ROOT / "protocol.json").read_bytes()
    protocol = json.loads(protocol_bytes)
    for source in protocol["files"]:
        assert hashlib.sha256((ROOT / "inputs" / source["file"]).read_bytes()).hexdigest() == source["sha256"]
    reports = [json.loads((args.results / f"docling-{v}.json").read_text()) for v in protocol["versions"]]
    assert reports[0]["dependencies"] == reports[1]["dependencies"]
    assert all(r["protocol_sha256"] == hashlib.sha256(protocol_bytes).hexdigest() for r in reports)
    result = {"scope": protocol["scope"], "unique_fixture_regions": 13,
              "case_backend_combinations": 39, "versions": [], "transitions": []}
    for report in reports:
        rows = report["rows"]
        assert len(rows) == 39 and all("error" not in row for row in rows)
        by_key = {(r["backend"], r["id"]): r for r in rows}
        assert len(by_key) == 39
        assert all(len(r["warm_selection_ms"]) == protocol["warm_repeats"] for r in rows)
        result["versions"].append({
            "version": report["version"], "ocr_eligible": sum(r["ocr_eligible"] for r in rows),
            "expected_eligible": sum(r["expected_ocr_eligible"] for r in rows),
            "unexpectedly_eligible": sum(r["ocr_eligible"] and not r["expected_ocr_eligible"] for r in rows),
            "unexpectedly_excluded": sum(not r["ocr_eligible"] and r["expected_ocr_eligible"] for r in rows),
            "expectation_matches": sum(r["matches_upstream_expectation"] for r in rows),
            "warm_selection_median_ms": statistics.median([v for r in rows for v in r["warm_selection_ms"]]),
            "process_seconds": report["elapsed_seconds"], "peak_rss_mib": report["peak_rss_mib"],
            "import_seconds": report["import_seconds"],
        })
    old = {(r["backend"], r["id"]): r for r in reports[0]["rows"]}
    new = {(r["backend"], r["id"]): r for r in reports[1]["rows"]}
    assert old.keys() == new.keys()
    for key in old:
        a, b = old[key], new[key]
        assert a["native_text"] == b["native_text"]
        assert a["bbox_ltrb"] == b["bbox_ltrb"] and a["pdf"] == b["pdf"]
        if a["ocr_eligible"] != b["ocr_eligible"]:
            result["transitions"].append({"backend": key[0], "id": key[1],
                                          "old": a["ocr_eligible"], "new": b["ocr_eligible"]})
    result["validation"] = "input hashes, fixed protocol, matching dependencies/case keys/native text, 78 error-free records"
    with args.output.open("x") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps({"verified": True, "transitions": len(result["transitions"]), "versions": result["versions"]}))

if __name__ == "__main__":
    main()
