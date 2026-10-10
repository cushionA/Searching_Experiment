"""Assemble latest reviewed links for the 250 distinct source-selected pairs."""
import json
from collections import Counter, defaultdict
from pathlib import Path
import sys
HERE = Path(__file__).resolve().parent
EXP = HERE.parents[1]
sys.path.insert(0, str(EXP))
import trial_luna_sku_matching as core


def main():
    dest = HERE / "links-cumulative-250.jsonl"
    if dest.exists(): raise RuntimeError("refusing overwrite cumulative links")
    paths = [EXP / "results" / x for x in (
        "20261010T160225Z-luna-task-improvement/r02-reference-development",
        "20261010T160225Z-luna-task-improvement/r03-reference-holdout",
        "20261010T174640Z-luna-expanded50", "20261010T183000Z-luna-improvement-v3/improved",
        "20261010T183754Z-luna-output-shape-v4/improved", "20261010T203800Z-luna-current-row-dimensions-v10/derived", "20261010T205900Z-luna-closed-review-v12/derived")]
    cases, links, final, source_hashes = {}, [], [], {}
    for path in paths:
        for c in core.read(path / "inputs.json"):
            if c["case_id"] in cases: raise ValueError("overlapping evaluated cases")
            cases[c["case_id"]] = c
        summary = core.read(path / "reviewed-summary.json")
        final += summary["cases"]
        rows = [json.loads(line) for line in (path / "links.jsonl").read_text().splitlines()]
        for row in rows:
            if row["case_id"] not in {c["case_id"] for c in summary["cases"] if c["decision"] == "adopt"}: raise ValueError("non-adopted link")
        links += [{**row, "source_round": str(path.relative_to(EXP))} for row in rows]
        source_hashes[str(path.relative_to(EXP))] = {name: core.sha((path / name).read_bytes()) for name in ("inputs.json", "links.jsonl", "reviewed-summary.json")}
    if len(cases) != 250 or len({c["case_id"] for c in final}) != 250 or len({c["case_id"] for c in links}) != len(links): raise ValueError("cumulative coverage mismatch")
    adopted = {c["case_id"] for c in final if c["decision"] == "adopt"}
    if {c["case_id"] for c in links} != adopted: raise ValueError("adopted link coverage mismatch")
    dest.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in links), encoding="utf-8")
    targets = defaultdict(list)
    for link in links: targets[link["rakuten_sku_key"]].append({"case_id": link["case_id"], "au_row_key": link["au_row_key"], "au_product_id": link["au_product_id"]})
    core.save(HERE / "cumulative-link-manifest.json", {"case_count": len(cases), "rakuten_sku_count": len({c["rakuten_sku_key"] for c in cases.values()}),
        "au_product_count": len({c["au_product_id"] for c in cases.values()}), "candidate_row_count": sum(len(c["au_rows"]) for c in cases.values()),
        "decisions": dict(Counter(c["decision"] for c in final)), "exported_links": len(links),
        "linked_rakuten_sku_count": len(targets), "multi_au_links_for_same_rakuten_sku": {sku: rows for sku, rows in targets.items() if len(rows)>1},
        "sources_sha256": source_hashes, "links_sha256": core.sha(dest.read_bytes()), "human_verified": False,
        "note": "row and column coordinates retained; multiple AU sellers may legitimately share the same physical SKU; no inferred deduplication by product label"})
    print(json.dumps({"processed_pairs": 250, "exported_links": len(links), "linked_rakuten_skus": len(targets)}))
if __name__ == "__main__": main()
