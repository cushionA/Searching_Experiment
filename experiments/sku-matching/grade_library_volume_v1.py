#!/usr/bin/env python3
"""Score a frozen historical volume run, retaining original multiple-row references."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

import grade_final_test_subset_v1 as references
from grade_fresh_dev_sku_gate_v1 import V2_ARCHIVE, V3_ARCHIVE
from grade_library_axis_domains_v1 import pruning
from run_library_volume_v1 import METHODS, read_json, read_jsonl, sha, write_json

ROOT = Path(__file__).resolve().parents[2]
PRIMARY = "mutual_unique_raw_components"


def grade(run: Path, plan: Path) -> None:
    if (run / "grade.json").exists():
        raise FileExistsError(run / "grade.json")
    freeze_path = run / "RUN_FREEZE.json"
    freeze, protocol = read_json(freeze_path), read_json(plan)
    checked = {}

    def check(path: Path, expected: str) -> None:
        if sha(path) != expected:
            raise ValueError("Frozen file changed: " + str(path))
        checked[str(path.resolve())] = expected

    for name, expected in protocol["fixed_policy_code_pins"].items():
        check(Path(__file__).with_name(name), expected)
    if protocol["primary"] != PRIMARY or tuple(protocol["variants"]) != METHODS:
        raise ValueError("variant/primary differs from the predeclared plan")
    for path, expected in freeze["input_pins"].items():
        check(Path(path), expected)
    for name, expected in freeze["code_pins"].items():
        check(Path(__file__).with_name(name), expected)
    for name, expected in freeze["output_pins"].items():
        check(run / name, expected)
    source, original = Path(freeze["source_path"]), Path(freeze["original_path"])
    cases = read_jsonl(source / "case-plans.jsonl")
    native_cases = read_jsonl(original / "cases.jsonl")
    rows = read_jsonl(source / "row-plans.jsonl")
    requests = read_jsonl(source / "requests.jsonl")
    case_ids = {c["case_id"] for c in native_cases}
    if len(cases) != len(case_ids) or {c["case_id"] for c in cases} != case_ids:
        raise ValueError("native and prepared case membership differs")
    source_counts = (len(cases), len(rows), len(requests), sum(len(r["bindings"]) for r in rows))
    if source_counts != tuple(freeze["counts"][k] for k in ("cases", "rows", "requests", "tasks")):
        raise ValueError("whole input coverage differs from freeze")
    outputs = {mode: {kind: read_jsonl(run / f"{mode}-{kind}.jsonl")
                      for kind in ("requests", "rows", "cases")} for mode in METHODS}
    baseline = {r["request_id"]: r["state"] for r in outputs["baseline_raw"]["requests"]}
    row_keys = [(r["case_id"], r["au_row_key"]) for r in rows]
    for mode, output in outputs.items():
        if ([r["request_id"] for r in output["requests"]] != [r["request_id"] for r in requests]
                or [(r["case_id"], r["au_row_key"]) for r in output["rows"]] != row_keys
                or {c["case_id"] for c in output["cases"]} != case_ids
                or len(output["cases"]) != len(case_ids)):
            raise ValueError("whole original membership/order differs: " + mode)
        if any(baseline[r["request_id"]] != "unknown" and r["state"] != baseline[r["request_id"]]
               for r in output["requests"]):
            raise ValueError("known raw state overwritten: " + mode)

    membership = []
    for case in native_cases:
        path = case.get("source_record", {}).get("file", "").replace("\\", "/")
        version = "v2" if "/eval-v2/" in path else "v3" if "/eval-v3/" in path else None
        if version is None:
            raise ValueError("cannot route reference source version")
        membership.append({"case_id": case["case_id"], "source_version": version})
    # Nothing above opens reference archives, previous grades, or model outputs.
    refs = references.read_label_archives(V2_ARCHIVE, V3_ARCHIVE, membership, source / "row-plans.jsonl")
    result = {"schema": "sku-library-volume-grade-v1", "primary": PRIMARY,
        "independent_test": False, "historical_data_previously_evaluated_by_other_methods": True,
        "reference_kind": "Claude Haiku5.5 machine annotation, not human gold",
        "prediction_freeze_sha256": sha(freeze_path), "plan_sha256": sha(plan),
        "grader_code_sha256": sha(Path(__file__)), "verified_before_reference_read": checked,
        "source_counts": dict(zip(("cases", "rows", "requests", "bindings"), source_counts)),
        "reference_counts": dict(Counter(references.canonical_reference(r)[0] for r in refs.values())),
        "rules_thresholds_or_dictionaries_changed_after_new_reference_read": False,
        "production_adoption_allowed": False, "actual_AI_calls_tokens_or_cost_measured": False,
        "variants": {}}
    fields = ("direction", "axis_name", "option_values", "target_document_id", "target_selected_axes")

    def context(r):
        return json.dumps({k: r[k] for k in fields}, ensure_ascii=False, sort_keys=True)

    all_contexts = {context(r) for r in requests}
    plans = {c["case_id"]: c for c in cases}
    for mode, output in outputs.items():
        states = {r["request_id"]: r["state"] for r in output["requests"]}
        row_states = {(r["case_id"], r["au_row_key"]): r["state"] for r in output["rows"]}
        decisions = {c["case_id"]: c for c in output["cases"]}
        good, bad, unknown_accept, reject, bad_reject, unknown_reject = [], [], [], [], [], []
        for cid, decision in decisions.items():
            label, allowed = references.canonical_reference(refs[cid])
            if decision["disposition"] == "accept":
                if label not in ("matched", "unmatched"):
                    unknown_accept.append(cid)
                elif label == "matched" and decision["au_row_key"] in allowed:
                    good.append(cid)
                else:
                    bad.append(cid)
            elif decision["disposition"] == "reject":
                (reject if label == "unmatched" else bad_reject if label == "matched" else unknown_reject).append(cid)
        remaining = {b["request_id"] for row in rows
                     if decisions[row["case_id"]]["disposition"] == "review"
                     and row_states[row["case_id"], row["au_row_key"]] != "conflict"
                     for b in row["bindings"] if states[b["request_id"]] == "unknown"}
        by_product = defaultdict(Counter)
        for cid, decision in decisions.items():
            by_product[plans[cid]["dossier_id"]][decision["disposition"]] += 1
        result["variants"][mode] = {
            "counts": dict(Counter(c["disposition"] for c in decisions.values())),
            "correct_accept_known": len(good), "correct_reject_known": len(reject),
            "wrong_accept_known_case_ids": bad, "wrong_reject_known_case_ids": bad_reject,
            "accept_reference_unknown_case_ids": unknown_accept,
            "reject_reference_unknown_case_ids": unknown_reject,
            "pruning": pruning(output["rows"], refs),
            "products_with_known_correct_accept": len({plans[c]["dossier_id"] for c in good}),
            "product_decision_counts": dict(by_product),
            "workload_estimate": {"all_contexts": len(all_contexts),
                "residual_contexts": len({context(r) for r in requests if r["request_id"] in remaining}),
                "nonconflict_rows": sum(s != "conflict" for s in row_states.values())}}
    write_json(run / "grade.json", result)
    print(json.dumps({k: {n: v[n] for n in ("correct_accept_known", "correct_reject_known",
        "wrong_accept_known_case_ids", "wrong_reject_known_case_ids", "workload_estimate")}
        for k, v in result["variants"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--plan", type=Path, required=True)
    grade(**vars(p.parse_args()))
