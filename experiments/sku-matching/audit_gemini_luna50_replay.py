#!/usr/bin/env python3
"""Verify completed replay artifacts and derive per-case provider statistics."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import trial_gemini_luna50_replay as base
import sku_gemini_replay_stages as stages
import sku_gemini_replay_transport as transport


def audit(root: Path):
    for name in ("frozen-protocol.json", "signature-freeze.json", "review-freeze.json", "grading-freeze.json"):
        base.verify(root, name)
    cases = {c["case_id"]: c for c in base.read(root / "derived/inputs.json")}
    summary = base.read(root / "derived/reviewed-summary.json")
    final = {c["case_id"]: c for c in summary["cases"]}
    if len(final) != 50 or set(final) != set(cases) or len(summary["cases"]) != 50:
        raise ValueError("case coverage mismatch")
    context_bytes = (root / "derived/source-contexts.json").read_bytes()
    if context_bytes != (stages.V13 / "derived/source-contexts.json").read_bytes():
        raise ValueError("paired source contexts changed")
    if (root / "derived/inputs.json").read_bytes() != (stages.V13 / "derived/inputs.json").read_bytes():
        raise ValueError("paired selected inputs changed")
    records = {cid: [] for cid in cases}
    source_bodies_preserved = 0
    decoded_schemas_adapted = 0
    for stage, manifest in (("matching", "matching-dispatch-manifest.json"),
                            ("signature", "signature-dispatch-manifest.json"),
                            ("review", "review-dispatch-manifest.json")):
        base.verify_provider_stage(root, stage)
        for index, entry in enumerate(base.read(root / manifest)["entries"], 1):
            cid = entry.get("original_case_id", entry["case_id"])
            receipt = base.read(root / "provider" / stage / f"{index:03}.receipt.json")
            records[cid].append({"stage": stage, "task_index": index, **receipt})
            if stage == "matching":
                source_folder = stages.V13 if entry["source_round"] == "v13-fresh" else stages.V9
                original = base.read(source_folder / entry["source_request"])["body"]
                actual = base.read(root / entry["request"])["body"]
                restored = json.loads(json.dumps(actual))
                restored["generationConfig"]["responseSchema"] = original["generationConfig"]["responseSchema"]
                if restored != original:
                    raise ValueError("matching semantic task body differs")
                source_bodies_preserved += 1
                decoded_schemas_adapted += actual != original
    matching = base.read(root / "matching-chunk-evaluation.json")
    dimensions = base.read(root / "derived/signature-v3-evaluation.json")
    review = base.read(root / "review-collection-audit.json")
    details = []
    for cid, case in cases.items():
        usage = Counter()
        for receipt in records[cid]:
            usage.update({k: v for k, v in receipt.get("usageMetadata", {}).items()
                          if isinstance(v, int) and not isinstance(v, bool)})
        details.append({"case_id": cid, "au_candidate_rows": len(case["au_rows"]),
                        "decision": final[cid]["decision"], "adopted_row_key": final[cid].get("row_key"),
                        "served_model_versions": sorted({r.get("modelVersion", "unreported") for r in records[cid]}),
                        "task_calls_including_reused": dict(Counter(r["stage"] for r in records[cid])),
                        "usage_including_reused_once": dict(usage),
                        "api_elapsed_seconds_sum_including_reused_once": round(sum(r["elapsed_seconds"] for r in records[cid]), 6),
                        "semantic_review": final[cid].get("reason"),
                        "dimension_check": {k: dimensions.get(cid, {}).get(k) for k in ("passed", "validation_ok", "guard_issues")}})
    links_path = root / "derived/links.jsonl"
    links = [json.loads(line) for line in links_path.read_text().splitlines() if line.strip()]
    if len(links) != summary["exported_links"] or len(links) != sum(x["decision"] == "adopt" for x in final.values()):
        raise ValueError("exported link count mismatch")
    for link in links:
        if final[link["case_id"]]["row_key"] != link["au_row_key"]:
            raise ValueError("exported AU cell differs from adopted row")
        if link["au_sku_id_semantics"] != "skuInfo.skuId identifies the grid; row_index and column_index identify the selected SKU cell":
            raise ValueError("AU grid identifier semantics changed")
    result = {"auditedUTC": transport.utc(), "protocol_hashes_match": True,
              "case_count": 50, "candidate_rows": sum(len(c["au_rows"]) for c in cases.values()),
              "matching_tasks": matching["task_count"], "matching_structural_pass": matching["validation_pass"],
              "matching_cases_structural_pass": matching["cases_valid"],
              "same_matching_prompt_and_input_verified": source_bodies_preserved,
              "decoder_schema_only_changes": decoded_schemas_adapted,
              "dimension_checks": len(dimensions), "dimension_structural_pass": sum(x["validation_ok"] for x in dimensions.values()),
              "dimension_semantic_pass": sum(x["passed"] for x in dimensions.values()),
              "review_structural_pass": sum(x["status"] == "validated" for x in review["reviews"]),
              "decisions": summary["counts"], "exported_links": len(links),
              "served_model_versions": sorted({r.get("modelVersion", "unreported") for rows in records.values() for r in rows}),
              "ground_truth_read": False, "per_case": details}
    transport.save_new(root / "provider-and-structure-audit.json", result)
    print(json.dumps({k:v for k,v in result.items() if k != "per_case"}, ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir",type=Path,required=True)
    args=parser.parse_args()
    audit(args.run_dir.resolve())
