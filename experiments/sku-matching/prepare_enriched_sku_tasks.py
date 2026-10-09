"""Prepare another cited real-data trial with page-to-SKU attribute crosswalks."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import evaluate_structured_skus as evaluation
import structured_sku_task as base


def prepare(args):
    from enrich_page_sku_attributes import enrich_task
    original_builder = base.build_task
    resolved_cache = {}

    def enriched_builder(case, dossier):
        task = original_builder(case, dossier)
        for side, entity in [("rakuten", task["rakuten"]), *[("au", c) for c in task["au_candidates"]]]:
            # The base builder already handles width-conditional curtain
            # contents. The generic table parser is a separate non-curtain
            # experiment; do not combine its interpretations of those lists.
            if entity["attrs"].get("category") == "curtain":
                entity["required_fields"] = sorted(entity["attrs"])
                continue
            key = (dossier["dossier_id"], side, json.dumps(entity, ensure_ascii=False, sort_keys=True))
            if key not in resolved_cache:
                minimal = {"rakuten": entity if side == "rakuten" else None,
                           "au_candidates": [entity] if side == "au" else []}
                enriched = enrich_task(minimal, dossier)
                result_entity = enriched["rakuten"] if side == "rakuten" else enriched["au_candidates"][0]
                resolved_cache[key] = result_entity["resolved_identity"]
            resolved = deepcopy(resolved_cache[key])
            entity["raw_attrs"] = entity["attrs"]
            entity["attrs"] = resolved["attrs"]
            entity["required_fields"] = sorted({resolved.get("replaced_fields", {}).get(k, k)
                                                if resolved.get("replaced_fields", {}).get(k, k) in entity["attrs"] else k
                                                for k in entity["selected_fields"]})
            entity["required_fields"] = sorted(set(entity["required_fields"]) | set(resolved.get("additional_required_fields", [])))
            entity["unknown_fields"] = sorted(set(resolved.get("unknown_fields", [])))
            entity["unresolved_axes"] = resolved.get("unresolved_axes", [k for k in entity["attrs"] if k.startswith("axis:")])
            entity["evidence"].update(resolved.get("evidence", {}))
            resolutions = resolved.get("resolved_conflicts", [])
            cleared = {r["field"] for r in resolutions if r.get("evidence")}
            entity["evidence"]["resolved_conflicts"] = resolutions
            entity["source_conflicts"] = [c for c in resolved.get("source_conflicts", [])
                                          if c["field"] not in cleared]
            for field in cleared:
                entity["attrs"].pop(field, None)
            if (resolved.get("needs_review") and not entity["unknown_fields"]
                    and not entity["source_conflicts"] and not cleared):
                entity["source_conflicts"].append({"field": "unresolved_page_crosswalk", "values": [True]})
        task["task_version"] = "structured-sku-task-v3-page-enriched"
        return task

    base.build_task = enriched_builder
    try:
        evaluation.prepare(args)
    finally:
        base.build_task = original_builder
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs_dir": str(args.inputs_dir.resolve()),
        "tasks_sha256": evaluation.sha256(args.output / "tasks.jsonl"),
        "evidence_sha256": evaluation.sha256(args.output / "evidence.jsonl"),
        "code_sha256": {p.name: evaluation.sha256(p) for p in (
            Path(__file__), evaluation.HERE / "enrich_page_sku_attributes.py",
            evaluation.HERE / "structured_sku_task.py", evaluation.HERE / "evaluate_structured_skus.py")},
        "model_labels_used_for_preparation": False, "synthetic_data_included": False,
        "unique_non_curtain_resolutions": len(resolved_cache),
        "note": "Non-curtain explicit page crosswalks; base curtain-scoped composition unchanged. Original selected attributes retained in raw_attrs; unknown mappings abstain."}
    evaluation.write_new(args.output / "enrichment-manifest.json", manifest)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inputs-dir", type=Path, default=evaluation.DEFAULT_INPUTS)
    p.add_argument("--au-arrays", type=Path, default=evaluation.DEFAULT_AU_ARRAYS)
    p.add_argument("--output", type=Path, required=True)
    prepare(p.parse_args())


if __name__ == "__main__":
    main()
