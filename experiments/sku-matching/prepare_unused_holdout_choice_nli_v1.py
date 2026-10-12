#!/usr/bin/env python3
"""Freeze and prepare an unused, source-only TEST choice-NLI handoff.

The policy is written before any product/case draw. This wrapper only adapts
the population guard/metadata around the frozen NLI preparation module; source
fact, body, payload, and response semantics remain in that unchanged module.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = ROOT / ".lab-output/sku-unused-holdout-choice-20261013-v1"
TEST_SOURCE = ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2/test"
DEV_SOURCE = ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2/dev"
SOURCE_BUNDLE = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
OLD_MEMBERSHIP = ROOT / ".lab-output/sku-final-composition-subset-20261013-v1/plan/subset-membership.jsonl"
NLI_PATH = HERE / "sku_directional_choice_nli_v1.py"
SELECTED_PRODUCTS = 20
CASES_PER_PRODUCT = 2


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(Path(path).read_bytes())


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_bytes().splitlines() if line.strip()]


def identity_map(path: Path, key: str) -> dict[str, dict[str, Any]]:
    rows = read_jsonl(path)
    result = {}
    for row in rows:
        ident = row.get(key)
        if not isinstance(ident, str) or not ident or ident in result:
            raise ValueError(f"malformed or duplicate {key} in {path.name}")
        result[ident] = row
    return result


def load_source(source_dir: Path) -> tuple[dict, dict, dict, list, dict, dict, dict, dict]:
    """Verify a full-condition split and its original case-source identities."""
    split = source_dir.name
    if split not in {"dev", "test"}:
        raise ValueError("source directory must identify dev or test")
    root_manifest = read_json(source_dir.parent / "manifest.json")
    manifest = read_json(source_dir / "manifest.json")
    inventory = read_json(SOURCE_BUNDLE.parent / "inventory.json")
    if (root_manifest.get("schema_version") != "full-condition-adapter-v2"
            or root_manifest.get("missing_referenced_sources") != []
            or root_manifest.get("labels_read") is not False
            or root_manifest.get("model_inference_run") is not False
            or not isinstance(root_manifest.get("source_span_count_verified"), int)
            or inventory.get("status") != "source_bundle_ready"
            or inventory.get("source_provenance", {}).get("missing_referenced_sources")):
        raise ValueError("source bundle/span verification is not ready")
    if (manifest.get("schema_version") != "full-condition-inputs-v2"
            or manifest.get("split") != split
            or manifest.get("independent_test") is not (split == "test")
            or manifest.get("labels_read") is not False
            or manifest.get("model_inference_run") is not False
            or manifest.get("production_adoption_allowed") is not False):
        raise ValueError("source manifest does not match the declared blind split")
    names = ("documents.jsonl", "requests.jsonl", "tasks.jsonl", "row-plans.jsonl", "case-plans.jsonl")
    source_hashes = {name: sha_file(source_dir / name) for name in names}
    if any(manifest.get("outputs_sha256", {}).get(n) != h for n, h in source_hashes.items()):
        raise ValueError("source output SHA differs from full-condition manifest")
    cases = identity_map(source_dir / "case-plans.jsonl", "case_id")
    rows = read_jsonl(source_dir / "row-plans.jsonl")
    tasks = identity_map(source_dir / "tasks.jsonl", "task_id")
    requests = identity_map(source_dir / "requests.jsonl", "request_id")
    documents = identity_map(source_dir / "documents.jsonl", "document_id")
    if len({str(c["au_product_id"]) for c in cases.values()}) != manifest.get("product_count"):
        raise ValueError("case-plan product population differs from manifest")
    return root_manifest, manifest, cases, rows, tasks, requests, documents, source_hashes


def validate_coverage(cases, rows, tasks, requests):
    row_map = {}
    case_rows = defaultdict(set)
    expected_task_ids = set()
    for row in rows:
        pair = (row.get("case_id"), row.get("au_row_key"))
        if pair[0] not in cases or pair in row_map:
            raise ValueError("duplicate or unbound row plan")
        row_map[pair] = row
        case_rows[pair[0]].add(pair[1])
        binds = row.get("bindings")
        if not isinstance(binds, list):
            raise ValueError("row plan missing full task bindings")
        for binding in binds:
            tid = binding.get("task_id")
            task = tasks.get(tid)
            if task is None or (task.get("case_id"), task.get("au_row_key")) != pair:
                raise ValueError("row/task source binding mismatch")
            if task.get("request_id") not in requests:
                raise ValueError("task references a missing source request")
            expected_task_ids.add(tid)
    if expected_task_ids != set(tasks):
        raise ValueError("task plan population is not exactly the complete row binding set")
    for cid, case in cases.items():
        if (case.get("split") != case["_split"] or len(case_rows[cid]) != case.get("total_au_rows")
                or case_rows[cid] != set(case.get("row_keys", []))):
            raise ValueError("case plan does not retain every original AU row")
    return row_map


def original_case_line_hashes(split: str) -> dict[str, str]:
    """Bind source input case identities to exact original JSONL lines."""
    split_manifest = read_json((TEST_SOURCE if split == "test" else DEV_SOURCE) / "manifest.json")
    source_index = {x["path"]: x["sha256"] for x in read_json(SOURCE_BUNDLE.parent / "source-index.json")}
    out: dict[str, str] = {}
    for version in ("v2", "v3"):
        base = f".lab-output/sku-independent-real-20261011/eval-{version}"
        rel = base + "/run-token/inputs/cases.jsonl"
        path = SOURCE_BUNDLE / rel
        # Input-file SHA is pinned by its adjacent run manifest, which the
        # full-condition manifest records for both source versions.
        expected = split_manifest.get("source_inputs", {}).get(rel)
        if expected is None or sha_file(path) != expected:
            raise ValueError("original cases JSONL does not match its source manifest")
        groups_rel = base + "/groups-token/groups.jsonl"
        groups_path = SOURCE_BUNDLE / groups_rel
        if source_index.get(groups_rel) != sha_file(groups_path):
            raise ValueError("original source group map SHA differs")
        groups = {}
        for group_row in read_jsonl(groups_path):
            g = group_row.get("group", group_row)
            dossier = str(g.get("dossier_id", group_row.get("dossier_id", "")))
            if not dossier or dossier in groups:
                raise ValueError("malformed original source group map")
            groups[dossier] = g
        for line in path.read_bytes().splitlines():
            if not line:
                continue
            row = json.loads(line)
            group = groups.get(str(row.get("dossier_id")))
            if not group or group.get("excluded") or group.get("split") != split:
                continue
            cid = row.get("case_id")
            if not isinstance(cid, str) or cid in out:
                raise ValueError("duplicate/malformed original source case")
            out[cid] = sha_bytes(line)
    return out


def build_policy(source_hashes: dict, dev_hashes: dict, old_membership_hash: str,
                 source_root_manifest_sha: str, nli_sha: str) -> dict[str, Any]:
    return {
        "schema_version": "unused-holdout-choice-nli-selection-policy-v1",
        "selection_scope": "label-blind full-condition TEST source case plans only",
        "selection_count": {"products": SELECTED_PRODUCTS, "cases_per_product": CASES_PER_PRODUCT,
                             "cases": SELECTED_PRODUCTS * CASES_PER_PRODUCT},
        "eligibility": "Exclude every au_product_id in the complete prior subset-membership file and every DEV product. Product exclusion applies across all cases and source versions.",
        "ranking": {
            "canonicalization": "UTF-8 JSON, ensure_ascii=false, sorted keys, separators(',',':'), no NaN",
            "policy_hash": "SHA256 of this policy object before adding policy_sha256, selected membership, or draw outputs",
            "product_rank": "SHA256(UTF8(policy_sha256 + '|product|' + au_product_id)); ascending digest, then product ID",
            "case_rank": "SHA256(UTF8(policy_sha256 + '|case|' + au_product_id + '|' + case_id)); ascending digest, then case ID",
            "draw": "first 20 eligible products; first 2 eligible source cases per selected product",
        },
        "source_files": {
            "test_manifest_sha256": sha_file(TEST_SOURCE / "manifest.json"),
            "test_output_sha256": source_hashes,
            "dev_manifest_sha256": sha_file(DEV_SOURCE / "manifest.json"),
            "dev_output_sha256": dev_hashes,
            "source_bundle_inventory_sha256": sha_file(SOURCE_BUNDLE.parent / "inventory.json"),
            "source_bundle_index_sha256": sha_file(SOURCE_BUNDLE.parent / "source-index.json"),
            "source_root_manifest_sha256": source_root_manifest_sha,
            "old_subset_membership_sha256": old_membership_hash,
        },
        "frozen_classification_and_proof_policy": {
            "choice_nli_module_sha256": nli_sha,
            "citation_normalizer_sha256": sha_file(HERE / "normalize_source_nli_citations_v1.py"),
            "whole_sku_evaluator_sha256": sha_file(HERE / "evaluate_dev_source_bound_whole_sku_gate_v1.py"),
            "literal_proof_module_sha256": sha_file(HERE / "sku_literal_family_proof_v1.py"),
            "policy_sha256_prefixes_as_requested": {"choice_nli": "a467", "normalizer": "f43",
                "evaluator": "2782", "literal_proof": "ef00"},
            "root_policy": "full source-bound choice NLI plus protected strict literal task proofs; no baseline fallback; all captured conditions, rows, and both directions; no classification or scoring in this preparation artifact",
            "choice_nli_semantics": "unchanged frozen a467 module semantics; no new category, semantic rule, or response rule",
        },
        "scope_guards": {"labels_read": False, "gold_or_references_read": False,
            "predictions_or_scores_read": False, "model_inference_run": False,
            "classification_or_scoring_run": False, "all_source_rows_retained": True,
            "all_original_task_bindings_retained": True},
    }


def _rank(policy_sha: str, domain: str, *ids: str) -> str:
    return sha_bytes((policy_sha + "|" + domain + "|" + "|".join(ids)).encode("utf-8"))


def select_members(policy_sha: str, test_cases: dict, dev_cases: dict,
                   old_products: set[str]) -> list[dict[str, Any]]:
    dev_products = {str(c["au_product_id"]) for c in dev_cases.values()}
    by_product = defaultdict(list)
    for case in test_cases.values():
        by_product[str(case["au_product_id"])].append(case)
    eligible = {p: cs for p, cs in by_product.items()
                if p not in old_products and p not in dev_products and len(cs) >= CASES_PER_PRODUCT}
    ranked_products = sorted(eligible, key=lambda p: (_rank(policy_sha, "product", p), p))
    if len(ranked_products) < SELECTED_PRODUCTS:
        raise ValueError("too few unconsumed TEST products with two cases")
    chosen = []
    for product in ranked_products[:SELECTED_PRODUCTS]:
        candidates = sorted(eligible[product],
                            key=lambda c: (_rank(policy_sha, "case", product, c["case_id"]), c["case_id"]))
        for case in candidates[:CASES_PER_PRODUCT]:
            chosen.append({"au_product_id": product, "case_id": case["case_id"],
                "dossier_id": case["dossier_id"], "source_version": case["source_version"],
                "selection_hash": _rank(policy_sha, "case", product, case["case_id"])})
    return chosen


def _write_selected_jsonl(source_path: Path, output_path: Path, predicate) -> int:
    count = 0
    with output_path.open("xb") as out:
        for line in source_path.read_bytes().splitlines(keepends=True):
            if not line.strip():
                continue
            row = json.loads(line)
            if predicate(row):
                out.write(line)
                count += 1
    return count


def materialize(selected: list[dict], source_hashes: dict, root_manifest: dict,
                test_cases: dict, test_rows: list, test_tasks: dict, test_requests: dict,
                test_documents: dict, row_map: dict, output_dir: Path, selection_policy: dict,
                policy_sha: str, old_products: set[str], dev_products: set[str], dev_test_audit: dict):
    source_dir = TEST_SOURCE
    inputs_dir = output_dir / "source-inputs"
    inputs_dir.mkdir(parents=True, exist_ok=False)
    selected_case_ids = {m["case_id"] for m in selected}
    chosen_rows = [r for r in test_rows if r["case_id"] in selected_case_ids]
    chosen_pairs = {(r["case_id"], r["au_row_key"]) for r in chosen_rows}
    chosen_task_ids = {b["task_id"] for r in chosen_rows for b in r["bindings"]}
    chosen_request_ids = {test_tasks[t]["request_id"] for t in chosen_task_ids}
    chosen_document_ids = {test_requests[r]["target_document_id"] for r in chosen_request_ids}
    counts = {}
    counts["case-plans.jsonl"] = _write_selected_jsonl(source_dir / "case-plans.jsonl", inputs_dir / "case-plans.jsonl", lambda x: x["case_id"] in selected_case_ids)
    counts["row-plans.jsonl"] = _write_selected_jsonl(source_dir / "row-plans.jsonl", inputs_dir / "row-plans.jsonl", lambda x: x["case_id"] in selected_case_ids)
    counts["tasks.jsonl"] = _write_selected_jsonl(source_dir / "tasks.jsonl", inputs_dir / "tasks.jsonl", lambda x: x["task_id"] in chosen_task_ids)
    counts["requests.jsonl"] = _write_selected_jsonl(source_dir / "requests.jsonl", inputs_dir / "requests.jsonl", lambda x: x["request_id"] in chosen_request_ids)
    counts["documents.jsonl"] = _write_selected_jsonl(source_dir / "documents.jsonl", inputs_dir / "documents.jsonl", lambda x: x["document_id"] in chosen_document_ids)
    if (counts["case-plans.jsonl"] != 40 or counts["row-plans.jsonl"] != len(chosen_rows)
            or counts["tasks.jsonl"] != len(chosen_task_ids)
            or counts["requests.jsonl"] != len(chosen_request_ids)
            or counts["documents.jsonl"] != len(chosen_document_ids)):
        raise ValueError("source filter did not retain exact full case/row/task/request/document coverage")
    outputs_sha = {name: sha_file(inputs_dir / name) for name in counts}
    in_manifest = {"schema_version": "full-condition-inputs-v2", "split": "test",
        "independent_test": True, "labels_read": False, "test_split_read": True,
        "model_inference_run": False, "production_adoption_allowed": False,
        "selected_case_count": 40, "selected_row_count": len(chosen_rows),
        "selected_task_count": len(chosen_task_ids), "selected_product_count": 20,
        "outputs_sha256": outputs_sha, "output_counts": counts,
        "source_manifest_sha256": sha_file(source_dir / "manifest.json"),
        "source_root_manifest_sha256": sha_file(source_dir.parent / "manifest.json"),
        "source_original_output_sha256": source_hashes,
        "selection_policy_sha256": policy_sha,
        "selected_membership_sha256": sha_file(output_dir / "selection-membership.jsonl"),
        "all_rows_for_selected_cases_retained": True,
        "all_original_task_bindings_retained": True,
        "source_only": True, "labels_gold_references_predictions_scores_read": False}
    (inputs_dir / "manifest.json").write_text(json.dumps(in_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    # Call the unchanged NLI body behind a narrow split/count adapter.
    spec = importlib.util.spec_from_file_location("sku_directional_choice_nli_unused_holdout", NLI_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen NLI module")
    nli = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = nli
    spec.loader.exec_module(nli)
    original_loader = nli._load_manifest
    def test_loader(path: Path):
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if (manifest.get("schema_version") != "full-condition-inputs-v2"
                or manifest.get("split") != "test" or manifest.get("independent_test") is not True
                or manifest.get("labels_read") is not False or manifest.get("test_split_read") is not True
                or manifest.get("selected_case_count") != 40
                or manifest.get("selected_row_count") != len(chosen_rows)
                or manifest.get("selected_task_count") != len(chosen_task_ids)):
            raise ValueError("input bundle is not the frozen source-only TEST40 handoff")
        return manifest
    nli._load_manifest = test_loader
    prepared_dir = output_dir / "prepared-choice-nli"
    try:
        nli_result = nli.prepare(inputs_dir, SOURCE_BUNDLE, prepared_dir)
    finally:
        nli._load_manifest = original_loader
    freeze_path = prepared_dir / "freeze.json"
    freeze = read_json(freeze_path)
    freeze.update({"dataset": "unused frozen TEST40 source handoff only", "split": "test",
        "independent_test": True, "test_split_read": True,
        "wrapper_sha256": sha_file(Path(__file__)), "selection_policy_sha256": policy_sha,
        "selected_membership_sha256": sha_file(output_dir / "selection-membership.jsonl"),
        "selection_scope": "20 TEST products x 2 cases; all selected source rows and tasks retained",
        "input_source_manifest_sha256": sha_file(inputs_dir / "manifest.json"),
        "full_test_source_manifest_sha256": sha_file(source_dir / "manifest.json"),
        "dev_test_product_separation_audit": dev_test_audit,
        "excluded_old_product_count": len(old_products), "selected_old_product_overlap": 0,
        "selected_dev_product_overlap": 0, "all_original_tasks_retained": True,
        "labels_read": False, "gold_or_worker_responses_read": False,
        "inference_run": False, "scores_or_gate_outputs_created": False,
        "classification_or_scoring_run": False, "production_adoption_allowed": False})
    freeze_path.write_text(json.dumps(freeze, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    summary_path = prepared_dir / "summary.json"
    summary = read_json(summary_path)
    summary.update(freeze)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    final_manifest = {"schema_version": "unused-holdout-source-choice-nli-v1",
        "phase": "source_only_test40_choice_nli_preparation", "selection_policy_sha256": policy_sha,
        "wrapper_sha256": sha_file(Path(__file__)), "selected_case_count": 40,
        "selected_product_count": 20, "selected_row_count": len(chosen_rows),
        "selected_task_count": len(chosen_task_ids), "selected_request_count": len(chosen_request_ids),
        "selected_document_count": len(chosen_document_ids), "prepared_pair_count": freeze["prepared_pair_count"],
        "ready_pair_count": freeze["ready_pair_count"], "abstention_task_count": freeze["abstention_task_count"],
        "preparation_error_count": freeze["source_fact_errors"],
        "all_original_tasks_retained": freeze["all_original_tasks_retained"],
        "dev_test_product_separation_audit": dev_test_audit,
        "old_product_exclusion_count": len(old_products), "selected_old_product_overlap": 0,
        "selected_dev_product_overlap": 0, "labels_read": False, "gold_or_references_read": False,
        "predictions_or_scores_read": False, "inference_run": False,
        "classification_or_scoring_run": False, "production_adoption_allowed": False,
        "source_input_files_sha256": outputs_sha,
        "prepared_freeze_sha256": sha_file(freeze_path)}
    (output_dir / "manifest.json").write_text(json.dumps(final_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return final_manifest


def run(output_dir: Path = BASE) -> dict[str, Any]:
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError("refusing to overwrite " + str(output_dir))
    output_dir.mkdir(parents=True)
    # Verify whole-source identities and coverage before the freeze, without
    # loading any label, reference, prediction, score, or annotation artifact.
    test_root, test_manifest, test_cases, test_rows, test_tasks, test_requests, test_docs, test_hashes = load_source(TEST_SOURCE)
    dev_root, dev_manifest, dev_cases, dev_rows, dev_tasks, dev_requests, dev_docs, dev_hashes = load_source(DEV_SOURCE)
    for cid, case in test_cases.items():
        case["_split"] = "test"
    for cid, case in dev_cases.items():
        case["_split"] = "dev"
    row_map = validate_coverage(test_cases, test_rows, test_tasks, test_requests)
    validate_coverage(dev_cases, dev_rows, dev_tasks, dev_requests)
    # Original input source identities are bound by source-case line hashes.
    original_test_hashes = original_case_line_hashes("test")
    original_dev_hashes = original_case_line_hashes("dev")
    if set(original_test_hashes) != set(test_cases) or set(original_dev_hashes) != set(dev_cases):
        raise ValueError("full-condition case IDs differ from original split case inputs")
    old_members = read_jsonl(OLD_MEMBERSHIP)
    old_products = {str(row["au_product_id"]) for row in old_members}
    if len(old_members) != 40 or len(old_products) != 20:
        raise ValueError("prior subset membership schema/count differs from frozen 20-product set")
    test_products = {str(c["au_product_id"]) for c in test_cases.values()}
    dev_products = {str(c["au_product_id"]) for c in dev_cases.values()}
    separation = {"test_product_count": len(test_products), "dev_product_count": len(dev_products),
        "test_dev_product_overlap_count": len(test_products & dev_products),
        "old_membership_product_count": len(old_products),
        "test_old_product_overlap_count": len(test_products & old_products),
        "eligible_test_product_count_after_exclusions": len(test_products - dev_products - old_products),
        "eligible_test_products_with_two_or_more_cases": sum(
            1 for p in test_products - dev_products - old_products
            if sum(str(c["au_product_id"]) == p for c in test_cases.values()) >= 2),
        "product_identity_fields_checked": ["au_product_id"],
        "split_inputs": {"dev_manifest_sha256": sha_file(DEV_SOURCE / "manifest.json"),
                         "test_manifest_sha256": sha_file(TEST_SOURCE / "manifest.json")}}
    if separation["test_dev_product_overlap_count"] != 0:
        raise ValueError("DEV/TEST product separation failed")
    if not old_products.issubset(test_products):
        raise ValueError("prior consumed product IDs are not all represented in full TEST source")
    # Policy file is durably written before product or case ranking occurs.
    policy = build_policy(test_hashes, dev_hashes, sha_file(OLD_MEMBERSHIP),
                          sha_file(TEST_SOURCE.parent / "manifest.json"), sha_file(NLI_PATH))
    policy_sha = sha_bytes(canonical(policy))
    policy_file = {**policy, "policy_sha256": policy_sha,
                   "dev_test_product_separation_audit": separation,
                   "status": "frozen_before_selection"}
    (output_dir / "selection-policy.json").write_text(
        json.dumps(policy_file, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    # Draw only after the policy file has been written and hashed.
    members = select_members(policy_sha, test_cases, dev_cases, old_products)
    products = Counter(m["au_product_id"] for m in members)
    if len(products) != 20 or set(products.values()) != {2}:
        raise AssertionError("selection does not satisfy frozen 20x2 design")
    if {m["au_product_id"] for m in members} & (old_products | dev_products):
        raise AssertionError("selected product is consumed or belongs to DEV")
    with (output_dir / "selection-membership.jsonl").open("xb") as out:
        for item in sorted(members, key=lambda x: (x["au_product_id"], x["case_id"])):
            out.write(canonical(item) + b"\n")
    # Record selected original case-line hashes as integrity receipts, not source contents.
    with (output_dir / "original-case-line-receipts.jsonl").open("xb") as out:
        for item in sorted(members, key=lambda x: (x["au_product_id"], x["case_id"])):
            out.write(canonical({"case_id": item["case_id"], "split": "test",
                "original_case_line_sha256": original_test_hashes[item["case_id"]]}) + b"\n")
    return materialize(members, test_hashes, test_root, test_cases, test_rows, test_tasks,
        test_requests, test_docs, row_map, output_dir, policy, policy_sha,
        old_products, dev_products, separation)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=BASE)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.output_dir), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
