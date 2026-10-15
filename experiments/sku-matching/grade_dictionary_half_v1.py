#!/usr/bin/env python3
"""Grade the frozen EVAL half of the TRAIN-dictionary experiment.

All prediction, dictionary, split, plan, and grader-policy pins are checked
before this program opens the independent reference archives.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any

import grade_final_test_subset_v1 as references
from grade_fresh_dev_sku_gate_v1 import V2_ARCHIVE, V3_ARCHIVE
from grade_library_axis_domains_v1 import pruning

ROOT = Path(__file__).resolve().parents[2]
SPLIT_DEFAULT = ROOT / ".lab-output/sku-dictionary-half-20261015-v1/split-v1"
VARIANTS = (
    "baseline_raw", "current_pipeline", "exact_dictionary1", "exact_dictionary2",
    "exact_dictionary1_current", "exact_dictionary2_current",
    "exact_current_dictionary1", "exact_current_dictionary2",
)
PRIMARY = "exact_dictionary2_current"
PINNED_POLICY_CODE = (
    "run_dictionary_half_v1.py", "library_train_dictionary_v1.py",
    "run_installed_library_selection_v1.py", "run_library_axis_domains_v1.py",
    "replay_dev_nli_policies_v1.py",
)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    values = []
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError(f"{path}:{line_no}: expected JSON object")
            values.append(item)
    return values


def write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def write_jsonl(path: Path, values) -> None:
    with path.open("x", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False) + "\n")


def _resolve(path_text: str, relative_to: Path | None = None) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return (relative_to / path) if relative_to else (ROOT / path)


def _check(path: Path, expected: str, verified: dict[str, str]) -> None:
    if not path.is_file() or sha(path) != expected:
        raise ValueError("frozen pin mismatch: " + str(path))
    verified[str(path.resolve())] = expected


def _check_dictionary_freeze(directory: Path, split_dir: Path, plan_sha: str,
                            verified: dict[str, str]) -> dict:
    freeze_path = directory / "DICTIONARY_FREEZE.json"
    freeze = read_json(freeze_path)
    if freeze.get("schema") != "sku-train-dictionary-freeze-v1":
        raise ValueError("unsupported dictionary freeze schema")
    if freeze.get("split_freeze_sha256") != sha(split_dir / "SPLIT_FREEZE.json"):
        raise ValueError("dictionary was built against a different split freeze")
    if freeze.get("experiment_plan_sha256") != plan_sha:
        raise ValueError("dictionary freeze points to a different experiment plan")
    for path_text, expected in freeze.get("input_pins", {}).items():
        _check(Path(path_text), expected, verified)
    here = Path(__file__).resolve().parent
    for name, expected in freeze.get("code_pins", {}).items():
        _check(here / name, expected, verified)
    for name, item in freeze.get("output_files", {}).items():
        _check(directory / name, item.get("sha256"), verified)
    if freeze.get("min_support_values") != [1, 2]:
        raise ValueError("dictionary support variants differ from frozen protocol")
    return freeze


def _validate_outputs(run: Path, freeze: dict, verified: dict[str, str],
                      plan_sha: str) -> dict[str, dict[str, list[dict]]]:
    if freeze.get("schema") != "sku-dictionary-half-output-freeze-v1" or freeze.get("split") != "eval":
        raise ValueError("unsupported EVAL output freeze")
    if freeze.get("eval_labels_read") is not False or freeze.get("grades_read") is not False:
        raise ValueError("prediction freeze already records EVAL labels or grades read")
    if freeze.get("experiment_plan_sha256") != plan_sha:
        raise ValueError("EVAL output freeze points to a different experiment plan")
    for path_text, expected in freeze.get("input_pins", {}).items():
        _check(Path(path_text), expected, verified)
    here = Path(__file__).resolve().parent
    for name, expected in freeze.get("code_pins", {}).items():
        _check(here / name, expected, verified)
    outputs = freeze.get("output_files", {})
    for name, item in outputs.items():
        _check(run / name, item.get("sha256"), verified)
    if tuple(freeze.get("variants", ())) != VARIANTS or freeze.get("primary_variant") != PRIMARY:
        raise ValueError("EVAL output variant order or primary differs from protocol")
    result = {}
    for variant in VARIANTS:
        result[variant] = {kind: read_jsonl(run / f"{variant}-{kind}.jsonl")
                           for kind in ("requests", "rows", "cases")}
    return result


def _validate_plan(plan: dict, verified: dict[str, str]) -> None:
    if tuple(plan.get("variants", ())) != VARIANTS or plan.get("primary") != PRIMARY:
        raise ValueError("variant order or primary differs from predeclared experiment plan")
    for name, expected in plan.get("fixed_policy_code_pins", {}).items():
        _check(Path(__file__).resolve().parent / name, expected, verified)
    if plan.get("fixed_policy_code_pins") and set(plan["fixed_policy_code_pins"]) != set(PINNED_POLICY_CODE):
        raise ValueError("predeclared policy code pin set differs from experiment code")


def grade(run: Path, split_dir: Path = SPLIT_DEFAULT, plan: Path | None = None,
          dictionaries: Path | None = None, output: Path | None = None) -> dict:
    run, split_dir = run.resolve(), split_dir.resolve()
    plan = (plan or ROOT / ".lab-output/sku-dictionary-half-20261015-v1/EXPERIMENT_PLAN.json").resolve()
    dictionaries = (dictionaries or ROOT / ".lab-output/sku-dictionary-half-20261015-v1/dictionaries-v1").resolve()
    output = (output or run / "grade-artifacts-v1").resolve()
    if output.exists():
        raise FileExistsError(f"refusing existing grade output: {output}")
    verified: dict[str, str] = {}

    protocol = read_json(plan)
    _validate_plan(protocol, verified)
    split_freeze_path = split_dir / "SPLIT_FREEZE.json"
    split_freeze = read_json(split_freeze_path)
    if split_freeze.get("schema_version") != "sku_dictionary_half_split_v1":
        raise ValueError("unsupported split freeze schema")
    if protocol.get("split_freeze_sha256") != sha(split_freeze_path):
        raise ValueError("experiment plan points to a different split freeze")
    for name, expected in split_freeze.get("artifact_sha256", {}).items():
        _check(split_dir / name, expected, verified)
    if split_freeze.get("artifact_sha256", {}).get("membership.jsonl") is None:
        raise ValueError("split freeze does not pin membership")
    plan_sha = sha(plan)
    dict_freeze = _check_dictionary_freeze(dictionaries, split_dir, plan_sha, verified)

    eval_freeze_path = run / "EVAL_OUTPUT_FREEZE.json"
    eval_freeze = read_json(eval_freeze_path)
    _check(eval_freeze_path, sha(eval_freeze_path), verified)
    outputs = _validate_outputs(run, eval_freeze, verified, plan_sha)
    # Freeze pins for the corpus and TRAIN-built dictionary are part of the EVAL
    # run receipt. Check the cross-reference before loading any reference data.
    if eval_freeze.get("dictionary_freeze_sha256") != sha(dictionaries / "DICTIONARY_FREEZE.json"):
        raise ValueError("EVAL output points to a different dictionary freeze")

    membership_rows = read_jsonl(split_dir / "membership.jsonl")
    eval_members = [m for m in membership_rows if m.get("split") == "EVALtest"]
    if len(eval_members) != len({m.get("case_id") for m in eval_members}):
        raise ValueError("duplicate EVAL case in frozen split membership")
    eval_ids = {m["case_id"] for m in eval_members}
    source_version_by_case = {}
    for member in eval_members:
        source_version = member.get("source_version")
        version = {"eval-v2": "v2", "eval-v3": "v3"}.get(source_version)
        if version is None:
            raise ValueError("EVAL membership has unsupported source_version: " + repr(source_version))
        source_version_by_case[member["case_id"]] = version
    cases = read_jsonl(run / "eval-case-plans.jsonl")
    if {c.get("case_id") for c in cases} != eval_ids or len(cases) != len(eval_ids):
        raise ValueError("EVAL case plans differ from frozen EVAL membership")
    membership = []
    for case in cases:
        membership.append({"case_id": case["case_id"],
                           "source_version": source_version_by_case[case["case_id"]]})

    requests = read_jsonl(run / "eval-requests.jsonl")
    rows = read_jsonl(run / "eval-row-plans.jsonl")
    request_ids = [r["request_id"] for r in requests]
    case_ids = [c["case_id"] for c in cases]
    row_keys = [(r["case_id"], r["au_row_key"]) for r in rows]
    if len(request_ids) != len(set(request_ids)) or len(case_ids) != len(set(case_ids)):
        raise ValueError("duplicate EVAL request or case ID")
    baseline = {r["request_id"]: r for r in outputs["baseline_raw"]["requests"]}
    current = {r["request_id"]: r for r in outputs["current_pipeline"]["requests"]}
    for variant, records in outputs.items():
        reqs, variant_rows, variant_cases = (records[k] for k in ("requests", "rows", "cases"))
        if [r.get("request_id") for r in reqs] != request_ids:
            raise ValueError("request membership/order differs: " + variant)
        if [(r.get("case_id"), r.get("au_row_key")) for r in variant_rows] != row_keys:
            raise ValueError("row membership/order differs: " + variant)
        if [c.get("case_id") for c in variant_cases] != case_ids:
            raise ValueError("case membership/order differs: " + variant)
        reqmap = {r["request_id"]: r for r in reqs}
        for rid, base in baseline.items():
            if base.get("raw_baseline_state", base["state"]) != base["state"]:
                raise ValueError("raw baseline state is mutable")
            if base["state"] != "unknown" and reqmap[rid]["state"] != base["state"]:
                raise ValueError("known raw state overwritten: " + variant)
        if variant in {"current_pipeline", "exact_current_dictionary1", "exact_current_dictionary2"}:
            for rid, cur in current.items():
                if cur["state"] != "unknown" and reqmap[rid]["state"] != cur["state"]:
                    raise ValueError("known current state overwritten: " + variant)

    # All freezes, memberships, and predictions are now validated. Only below
    # this point may the grader open the two reference archives.
    refs = references.read_label_archives(V2_ARCHIVE, V3_ARCHIVE, membership,
                                           run / "eval-row-plans.jsonl")
    if set(refs) != eval_ids:
        raise ValueError("selected EVAL references do not exactly cover frozen EVAL cases")

    selected_refs = []
    for case_id in sorted(refs):
        reference = refs[case_id]
        label, allowed = references.canonical_reference(reference)
        selected_refs.append({"case_id": case_id, "source_version": next(
            m["source_version"] for m in membership if m["case_id"] == case_id),
            "reference": reference, "canonical_label": label,
            "canonical_allowed_row_keys": allowed})

    fields = ("direction", "axis_name", "option_values", "target_document_id", "target_selected_axes")
    def context(r):
        return json.dumps({k: r[k] for k in fields}, ensure_ascii=False, sort_keys=True)
    all_contexts = {context(r) for r in requests}
    case_plans = {c["case_id"]: c for c in cases}
    grade_result = {
        "schema": "sku-dictionary-half-grade-v1", "independent_test": False,
        "historical_data_previously_evaluated_by_other_methods": True,
        "reference_kind": "Claude Haiku5.5 machine annotation, not human gold",
        "prediction_freeze_sha256": sha(eval_freeze_path), "plan_sha256": sha(plan),
        "dictionary_freeze_sha256": sha(dictionaries / "DICTIONARY_FREEZE.json"),
        "split_freeze_sha256": sha(split_freeze_path), "grader_code_sha256": sha(Path(__file__)),
        "verified_before_reference_read": verified,
        "source_counts": {"cases": len(cases), "rows": len(rows), "requests": len(requests),
                          "bindings": sum(len(r.get("bindings", [])) for r in rows)},
        "reference_counts": dict(Counter(references.canonical_reference(r)[0] for r in refs.values())),
        "rules_thresholds_or_dictionaries_changed_after_reference_read": False,
        "production_adoption_allowed": False, "actual_AI_calls_tokens_or_cost_measured": False,
        "variants": {}}
    for variant, records in outputs.items():
        reqs, variant_rows, variant_cases = (records[k] for k in ("requests", "rows", "cases"))
        states = {r["request_id"]: r["state"] for r in reqs}
        row_states = {(r["case_id"], r["au_row_key"]): r["state"] for r in variant_rows}
        decisions = {c["case_id"]: c for c in variant_cases}
        good, bad, unknown_accept, correct_reject, bad_reject, unknown_reject = [], [], [], [], [], []
        for cid, decision in decisions.items():
            label, allowed = references.canonical_reference(refs[cid])
            if decision["disposition"] == "accept":
                if label not in ("matched", "unmatched"):
                    unknown_accept.append(cid)
                elif label == "matched" and decision.get("au_row_key") in (allowed or []):
                    good.append(cid)
                else:
                    bad.append(cid)
            elif decision["disposition"] == "reject":
                (correct_reject if label == "unmatched" else bad_reject if label == "matched"
                 else unknown_reject).append(cid)
        remaining = {b["request_id"] for row in rows
                     if decisions[row["case_id"]]["disposition"] == "review"
                     and row_states[row["case_id"], row["au_row_key"]] != "conflict"
                     for b in row.get("bindings", []) if states[b["request_id"]] == "unknown"}
        grade_result["variants"][variant] = {
            "counts": {"requests": dict(Counter(states.values())),
                       "rows": dict(Counter(row_states.values())),
                       "cases": dict(Counter(c["disposition"] for c in variant_cases))},
            "correct_accept_known": len(good), "correct_reject_known": len(correct_reject),
            "wrong_accept_known_case_ids": bad, "wrong_reject_known_case_ids": bad_reject,
            "accept_reference_unknown_case_ids": unknown_accept,
            "reject_reference_unknown_case_ids": unknown_reject,
            "pruning": pruning(variant_rows, refs),
            "workload_estimate": {"all_contexts": len(all_contexts),
                                  "residual_contexts": len({context(r) for r in requests
                                      if r["request_id"] in remaining}),
                                  "nonconflict_rows": sum(s != "conflict" for s in row_states.values())}}

    current_cases = {c["case_id"]: c for c in outputs["current_pipeline"]["cases"]}
    deltas = {}
    for variant, records in outputs.items():
        decisions = {c["case_id"]: c for c in records["cases"]}
        changed = []
        for cid in case_ids:
            now, before = decisions[cid], current_cases[cid]
            if (now.get("disposition"), now.get("au_row_key")) != (
                    before.get("disposition"), before.get("au_row_key")):
                changed.append({"case_id": cid,
                                "current": {"disposition": before.get("disposition"),
                                            "au_row_key": before.get("au_row_key")},
                                "variant": {"disposition": now.get("disposition"),
                                            "au_row_key": now.get("au_row_key")}})
        deltas[variant] = {"changed_case_count_vs_current": len(changed), "cases": changed}
    # Expose requests where dictionary and current both make non-unknown,
    # conflicting claims and where dictionary-first differs from current-first.
    request_conflicts = {}
    reqmaps = {name: {r["request_id"]: r for r in recs["requests"]}
               for name, recs in outputs.items()}
    for variant in ("exact_dictionary1_current", "exact_dictionary2_current",
                    "exact_current_dictionary1", "exact_current_dictionary2"):
        diffs = []
        for rid in request_ids:
            row = reqmaps[variant][rid]
            dstate, cstate = row.get("dictionary_state"), row.get("current_pipeline_state")
            if dstate is not None and cstate is not None and dstate != "unknown" and cstate != "unknown" and dstate != cstate:
                diffs.append({"request_id": rid, "dictionary_state": dstate,
                              "current_pipeline_state": cstate,
                              "variant_state": row["state"], "decision_source": row.get("decision_source")})
        request_conflicts[variant] = diffs
    grade_result["case_deltas_vs_current_pipeline"] = deltas
    precedence_disagreements = {}
    for support in (1, 2):
        dict_first, current_first = f"exact_dictionary{support}_current", f"exact_current_dictionary{support}"
        changed = [rid for rid in request_ids
                   if reqmaps[dict_first][rid]["state"] != reqmaps[current_first][rid]["state"]]
        precedence_disagreements[f"support{support}"] = {
            "dictionary_first_variant": dict_first,
            "current_first_variant": current_first,
            "different_request_state_count": len(changed),
            "different_request_ids": changed}
    grade_result["request_dictionary_current_conflicts"] = request_conflicts
    grade_result["dictionary_first_vs_current_first_request_disagreements"] = precedence_disagreements

    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "grade.json", grade_result)
    write_jsonl(output / "selected_eval_references.jsonl", selected_refs)
    for variant, value in deltas.items():
        write_json(output / f"{variant}-case-deltas-vs-current.json", value)
    print(json.dumps({name: {"correct_accept_known": value["correct_accept_known"],
                             "correct_reject_known": value["correct_reject_known"],
                             "changed_case_count_vs_current": deltas[name]["changed_case_count_vs_current"]}
                      for name, value in grade_result["variants"].items()}, ensure_ascii=False))
    return grade_result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path, help="frozen EVAL predictions directory")
    parser.add_argument("--split-dir", type=Path, default=SPLIT_DEFAULT)
    parser.add_argument("--plan", type=Path, required=True, help="predeclared EXPERIMENT_PLAN.json")
    parser.add_argument("--dictionaries", type=Path, required=True, help="frozen dictionary directory")
    parser.add_argument("--output", required=True, type=Path, help="new grade artifact directory")
    args = parser.parse_args()
    grade(args.run, args.split_dir, args.plan, args.dictionaries, args.output)


if __name__ == "__main__":
    main()
