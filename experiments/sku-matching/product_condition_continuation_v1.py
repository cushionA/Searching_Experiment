#!/usr/bin/env python3
"""Export only original cases/rows continuing after a frozen condition mask.

This queue is input to the remaining SKU comparison, never a final acceptance
result. No model or label reader is involved. Potential future skipped checks
are kept separate from the historically executed trial's zero new savings.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import copy
import hashlib
import json
from pathlib import Path

from product_condition_mask_v1 import VERSION as MASK_VERSION
from product_condition_sharing_v1 import digest

VERSION = "product-condition-continuation-v1"


def _index(values, field):
    result = {}
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("original record must be an object")
        identity = value.get(field)
        if not isinstance(identity, str) or not identity or identity in result:
            raise ValueError("missing or duplicate " + field)
        result[identity] = value
    return result


def _verify_record_hash(value, field):
    if value.get(field) != digest({k: v for k, v in value.items() if k != field}):
        raise ValueError("immutable hash differs: " + field)


def _validate_mask(mask):
    if not isinstance(mask, dict) or mask.get("schema_version") != MASK_VERSION:
        raise ValueError("unsupported frozen product condition mask")
    _verify_record_hash(mask, "mask_sha256")
    if (mask.get("labels_read") is not False or mask.get("model_inference_run") is not False or
            mask.get("production_adoption_allowed") is not False or
            mask.get("final_accept_from_mask_allowed") is not False or
            mask.get("runtime_savings_measured") is not False or
            mask.get("saved_remaining_symmetric_check_count_is_potential") is not True):
        raise ValueError("mask must remain a label-free continuation policy without final acceptance or measured savings")
    for field in ("condition_rows", "case_rows"):
        values = mask.get(field)
        if not isinstance(values, list) or mask.get(field + "_sha256") != digest(values):
            raise ValueError("immutable mask row collection differs: " + field)
        hash_field = "condition_sha256" if field == "condition_rows" else "case_sha256"
        for value in values:
            _verify_record_hash(value, hash_field)
    counts = mask.get("counts")
    if (not isinstance(counts, dict) or counts.get("measured_additional_avoided_inference_count") != 0):
        raise ValueError("historical trial has zero newly avoided inference")
    for name, value in counts.items():
        if type(value) is not int or value < 0:
            raise ValueError("mask counts must be nonnegative integers")


def build_continuation(mask, case_plans, row_plans):
    """Keep continue-case and candidate-row originals in their original order.

    Source semantic digests must match the frozen mask, all mask case/proof
    hashes must verify, and each original case/row must be covered exactly.
    Returned case_plans/row_plans contain no added product-flow fields.
    """
    _validate_mask(mask)
    source_cases, source_rows = list(case_plans), list(row_plans)
    cases = _index(source_cases, "case_id")
    case_hash, row_hash = digest(cases), digest(source_rows)
    if (mask.get("input_sha256", {}).get("case_plans") != case_hash or
            mask.get("input_sha256", {}).get("row_plans") != row_hash):
        raise ValueError("original case/row semantic SHA differs from frozen mask")
    mask_cases = _index(mask["case_rows"], "case_id")
    if list(mask_cases) != list(cases):
        raise ValueError("mask must cover original case identities and order exactly")
    conditions = _index(mask["condition_rows"], "request_id")
    condition_case_ids = defaultdict(set)
    for identity, condition in conditions.items():
        if condition.get("condition_decision") not in {"keep", "drop"}:
            raise ValueError("condition mask must be binary keep/drop")
        bindings = condition.get("source_task_bindings")
        if not isinstance(bindings, list):
            raise ValueError("complete source condition bindings required")
        for binding in bindings:
            if binding.get("request_id") != identity:
                raise ValueError("source condition task binds another request")
            case_id = binding.get("case_id")
            if case_id in cases:
                condition_case_ids[case_id].add(identity)
    by_case, row_keys = defaultdict(list), set()
    for row in source_rows:
        if not isinstance(row, dict):
            raise ValueError("original row must be an object")
        identity, key = row.get("case_id"), row.get("au_row_key")
        if identity not in cases or not isinstance(key, str) or not key or (identity, key) in row_keys:
            raise ValueError("original candidate row identity/coverage differs")
        row_keys.add((identity, key))
        by_case[identity].append(row)
    dropped, continued = [], []
    for identity, case in cases.items():
        row = mask_cases[identity]
        if row.get("source_case_plan") != case or row.get("source_row_plans") != by_case[identity]:
            raise ValueError("mask source originals or row order differ")
        candidates = case.get("candidate_row_keys")
        actual = [r["au_row_key"] for r in by_case[identity]]
        if (not isinstance(candidates, list) or len(set(candidates)) != len(candidates) or set(candidates) != set(actual)):
            raise ValueError("original case candidate AU row coverage differs")
        eligible = row.get("eligible_request_ids")
        rejected = row.get("dropped_request_ids")
        if (not isinstance(eligible, list) or len(set(eligible)) != len(eligible) or
                set(eligible) != condition_case_ids[identity] or not isinstance(rejected, list)):
            raise ValueError("case mask lost or added original eligible conditions")
        expected_rejected = [v for v in eligible if conditions[v]["condition_decision"] == "drop"]
        if rejected != expected_rejected:
            raise ValueError("case drop list differs from binary condition mask")
        refs = row.get("condition_refs")
        if not isinstance(refs, list) or [r.get("request_id") for r in refs] != eligible:
            raise ValueError("case condition proof coverage/order differs")
        for ref in refs:
            proof = conditions[ref["request_id"]]
            if any(ref.get(field) != proof.get(field) for field in
                   ("request_sha256", "group_id", "group_sha256", "condition_sha256")):
                raise ValueError("case condition immutable proof reference differs")
        early_drop = bool(expected_rejected)
        expected_decision = "early_drop" if early_drop else "continue_to_remaining_sku_comparison"
        if (row.get("early_drop") is not early_drop or row.get("case_decision") != expected_decision or
                row.get("final_accept") is not False or row.get("source_sku_key") != case.get("source_sku_key") or
                row.get("au_product_id") != case.get("au_product_id") or
                row.get("canonical_au_url") != "https://wowma.jp/item/" + case["au_product_id"]):
            raise ValueError("case mask binary decision/fixed SKU/URL must preserve continuation semantics")
        (dropped if early_drop else continued).append(identity)
    if mask.get("dropped_case_ids") != dropped or mask.get("continued_case_ids") != continued:
        raise ValueError("mask dropped/continued identities or order differ")
    for name, actual in (("selected_original_case_count", len(cases)), ("early_dropped_case_count", len(dropped)),
                        ("continued_case_count", len(continued)), ("eligible_condition_count", len(conditions))):
        if mask["counts"].get(name, 0) != actual:
            raise ValueError("mask declared count differs: " + name)
    continue_ids = set(continued)
    kept_cases = copy.deepcopy([value for value in source_cases if value["case_id"] in continue_ids])
    kept_rows = copy.deepcopy([value for value in source_rows if value["case_id"] in continue_ids])
    queue = {
        "schema_version": VERSION, "labels_read": False, "model_inference_run": False,
        "production_adoption_allowed": False, "final_accept_from_queue_allowed": False,
        "next_stage": "remaining_sku_comparison", "source_mask_sha256": mask["mask_sha256"],
        "input_sha256": {"mask": digest(mask), "case_plans": case_hash, "row_plans": row_hash},
        "continued_case_ids": continued, "removed_case_ids": dropped,
        "case_plans": kept_cases, "row_plans": kept_rows,
        "case_plans_sha256": digest(kept_cases), "row_plans_sha256": digest(kept_rows),
        "counts": {"input_case_count": len(source_cases), "input_candidate_row_count": len(source_rows),
                   "continued_case_count": len(kept_cases), "removed_case_count": len(dropped),
                   "continued_candidate_row_count": len(kept_rows),
                   "removed_candidate_row_count": len(source_rows) - len(kept_rows),
                   "measured_additional_avoided_inference_count": 0},
        "potential_future_check_savings": {name: value for name, value in mask["counts"].items()
                                            if name.startswith("potential_")},
        "runtime_savings_measured": False,
        "savings_note": "Historical baseline comparisons were already executed; this queue enables potential future early exit and adds zero measured avoided inference in this trial.",
    }
    queue["queue_sha256"] = digest(queue)
    return queue


def _file_sha(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _read_rows(path):
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_json(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def _write_rows(path, values):
    with path.open("x", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False) + "\n")


def export_continuation(mask_path, case_plans_path, row_plans_path, output_dir):
    """Validate immutable files before creating a new JSON/JSONL export."""
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError("refusing to overwrite continuation output: " + str(output_dir))
    inputs = {"mask": Path(mask_path), "case_plans": Path(case_plans_path), "row_plans": Path(row_plans_path)}
    before = {name: _file_sha(path) for name, path in inputs.items()}
    mask = json.loads(inputs["mask"].read_text(encoding="utf-8"))
    queue = build_continuation(mask, _read_rows(inputs["case_plans"]), _read_rows(inputs["row_plans"]))
    if before != {name: _file_sha(path) for name, path in inputs.items()}:
        raise ValueError("source files changed during continuation validation")
    output_dir.mkdir(parents=True, exist_ok=False)
    _write_rows(output_dir / "case_plans.jsonl", queue["case_plans"])
    _write_rows(output_dir / "row_plans.jsonl", queue["row_plans"])
    _write_json(output_dir / "removed-case-ids.json", queue["removed_case_ids"])
    if before != {name: _file_sha(path) for name, path in inputs.items()}:
        raise ValueError("source files changed during continuation export")
    outputs = ("case_plans.jsonl", "row_plans.jsonl", "removed-case-ids.json")
    manifest = {
        **{key: copy.deepcopy(value) for key, value in queue.items() if key not in {"case_plans", "row_plans"}},
        "phase": "continuation_export", "input_file_sha256": before,
        "input_files": {name: str(path.resolve()) for name, path in inputs.items()},
        "input_files_sha256_unchanged": True,
        "output_sha256": {name: _file_sha(output_dir / name) for name in outputs},
        "code_sha256": _file_sha(Path(__file__)),
    }
    manifest["manifest_sha256"] = digest(manifest)
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mask", required=True, type=Path)
    parser.add_argument("--case-plans", required=True, type=Path)
    parser.add_argument("--row-plans", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    manifest = export_continuation(args.mask, args.case_plans, args.row_plans, args.output_dir)
    print(json.dumps({"output_dir": str(args.output_dir), "counts": manifest["counts"],
                      "source_mask_sha256": manifest["source_mask_sha256"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
