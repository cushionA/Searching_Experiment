#!/usr/bin/env python3
"""Freeze and materialize a label-blind product-balanced full-test subset.

Phase 1 uses only v2 test case-plan/task metadata and writes a SHA-pinned 200-case
membership before reading case/product content. Phase 2 materializes only those
cases' requests, tasks, documents, frozen source rows, and referenced captures.
No labels, predictions, model weights, or inference are accessed.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2/test"
PREP = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1"
OUT = ROOT / ".lab-output/sku-final-test-subset-20261013-v1"
SEED = "sku-final-test-subset-v1-20261013"
SUBSET_SIZE = 200
PER_PRODUCT_CAP = 2


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def read_rows(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_rows(path: Path, values):
    with path.open("xb") as f:
        for value in values:
            f.write(canonical(value) + b"\n")


def write_json(path: Path, value):
    with path.open("xb") as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode() + b"\n")


def relpath(value: str) -> str:
    return str(value).replace("\\", "/")


def collect_file_sha(value, out):
    """Collect path/SHA pairs mechanically without reporting text values."""
    if isinstance(value, dict):
        raw = value.get("raw_file")
        digest = value.get("sha256")
        if isinstance(raw, str) and isinstance(digest, str):
            out.add((relpath(raw), digest))
        source = value.get("array_source")
        if isinstance(source, dict) and isinstance(source.get("file"), str):
            out.add((relpath(source["file"]), None))
        for child in value.values():
            collect_file_sha(child, out)
    elif isinstance(value, list):
        for child in value:
            collect_file_sha(child, out)


def freeze_membership(out_dir: Path):
    if out_dir.exists():
        raise FileExistsError("refusing to overwrite " + str(out_dir))
    out_dir.mkdir(parents=True)
    plan_dir = out_dir / "plan"
    plan_dir.mkdir()
    manifest = json.loads((INPUT / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("split") != "test" or manifest.get("independent_test") is not True or manifest.get("labels_read") is not False:
        raise ValueError("input is not the declared label-blind independent test split")
    plan_names = ("case-plans.jsonl", "tasks.jsonl")
    input_hashes = {name: file_sha(INPUT / name) for name in plan_names}
    if manifest.get("outputs_sha256", {}).get("case-plans.jsonl") != input_hashes["case-plans.jsonl"] or manifest.get("outputs_sha256", {}).get("tasks.jsonl") != input_hashes["tasks.jsonl"]:
        raise ValueError("v2 test metadata SHA differs from its manifest")
    case_meta = {}
    for row in read_rows(INPUT / "case-plans.jsonl"):
        cid = row["case_id"]
        if cid in case_meta:
            raise ValueError("duplicate v2 test case metadata")
        case_meta[cid] = {"case_id": cid, "au_product_id": str(row["au_product_id"]),
                          "dossier_id": str(row["dossier_id"]), "source_version": row["source_version"],
                          "expected_au_rows": row["total_au_rows"], "condition_count": row["total_task_bindings"]}
    request_ids_by_case = collections.defaultdict(set)
    condition_counts = collections.Counter()
    for task in read_rows(INPUT / "tasks.jsonl"):
        cid = task["case_id"]
        if cid not in case_meta:
            raise ValueError("test task lacks case metadata")
        request_ids_by_case[cid].add(task["request_id"])
        condition_counts[cid] += 1
    if set(condition_counts) != set(case_meta):
        raise ValueError("test case metadata and task population differ")
    for cid, row in case_meta.items():
        if condition_counts[cid] != row["condition_count"]:
            raise ValueError("case condition-count metadata differs")
        row["unique_request_count"] = len(request_ids_by_case[cid])

    by_product = collections.defaultdict(list)
    for meta in case_meta.values():
        by_product[meta["au_product_id"]].append(meta)
    product_order = sorted(by_product, key=lambda product_id: hashlib.sha256((SEED + "\0product\0" + product_id).encode()).hexdigest())
    for product_id, cases in by_product.items():
        cases.sort(key=lambda row: hashlib.sha256((SEED + "\0case\0" + row["case_id"]).encode()).hexdigest())
    selected = []
    for round_index in range(PER_PRODUCT_CAP):
        for product_id in product_order:
            candidates = by_product[product_id]
            if round_index < len(candidates) and len(selected) < SUBSET_SIZE:
                selected.append(candidates[round_index])
            if len(selected) == SUBSET_SIZE:
                break
        if len(selected) == SUBSET_SIZE:
            break
    if len(selected) != SUBSET_SIZE:
        raise ValueError("metadata cannot supply the declared subset size under product cap")
    selected_product_counts = collections.Counter(row["au_product_id"] for row in selected)
    if max(selected_product_counts.values()) > PER_PRODUCT_CAP:
        raise ValueError("product cap violated")
    selected_ids = {row["case_id"] for row in selected}
    selected.sort(key=lambda row: (row["au_product_id"], row["case_id"]))
    membership = [{"case_id": row["case_id"], "au_product_id": row["au_product_id"],
                   "dossier_id": row["dossier_id"], "source_version": row["source_version"],
                   "metadata_condition_count": row["condition_count"],
                   "metadata_unique_request_count": row["unique_request_count"],
                   "selection_hash": hashlib.sha256((SEED + "\0case\0" + row["case_id"]).encode()).hexdigest()}
                  for row in selected]
    write_rows(plan_dir / "subset-membership.jsonl", membership)
    output_bytes = (plan_dir / "subset-membership.jsonl").read_bytes()
    plan = {"schema_version": "sku-final-test-subset-plan-v1", "status": "predeclared_before_content_materialization",
            "seed": SEED, "selection_algorithm": "within-product case SHA order; product SHA order; round-robin one case per product then second case; cap two",
            "subset_size": SUBSET_SIZE, "product_cap": PER_PRODUCT_CAP,
            "source_population_case_count": len(case_meta), "source_product_count": len(by_product),
            "selected_case_count": len(selected_ids), "selected_product_count": len(selected_product_counts),
            "selected_product_count_distribution": dict(sorted(collections.Counter(selected_product_counts.values()).items())),
            "candidate_case_condition_count_summary": summarize([x["condition_count"] for x in case_meta.values()]),
            "selected_case_condition_count_summary": summarize([x["condition_count"] for x in selected]),
            "selected_task_binding_count": sum(x["condition_count"] for x in selected),
            "selected_unique_request_count_upper_bound": sum(x["unique_request_count"] for x in selected),
            "selection_metadata_inputs": {"manifest_sha256": file_sha(INPUT / "manifest.json"), **input_hashes},
            "membership_sha256": sha(output_bytes), "labels_read": False, "predictions_read": False,
            "model_inference_run": False, "subset_choice_uses_only_case_product_and_condition_metadata": True,
            "all_1292_test_cases_remain_in_source_coverage_population": True}
    write_json(plan_dir / "subset-plan.json", plan)
    return plan, selected_ids


def summarize(values):
    vals = sorted(values)
    if not vals:
        return {"count": 0}
    return {"count": len(vals), "min": vals[0], "median": vals[(len(vals) - 1) // 2],
            "p90": vals[int((len(vals) - 1) * .9)], "max": vals[-1], "total": sum(vals)}


def materialize(out_dir: Path, selected_ids: set[str]):
    plan_dir = out_dir / "plan"
    plan = json.loads((plan_dir / "subset-plan.json").read_text(encoding="utf-8"))
    membership = read_rows(plan_dir / "subset-membership.jsonl")
    if sha((plan_dir / "subset-membership.jsonl").read_bytes()) != plan["membership_sha256"] or {x["case_id"] for x in membership} != selected_ids:
        raise ValueError("predeclared membership changed before materialization")
    data_dir = out_dir / "inputs"
    data_dir.mkdir()
    # Filter the frozen v2 test artifacts by predeclared case/request IDs.
    tasks = [x for x in read_rows(INPUT / "tasks.jsonl") if x["case_id"] in selected_ids]
    task_ids = {x["task_id"] for x in tasks}
    request_ids = {x["request_id"] for x in tasks}
    model_input_ids = {x["model_input_id"] for x in tasks}
    requests = [x for x in read_rows(INPUT / "requests.jsonl") if x["request_id"] in request_ids]
    if {x["request_id"] for x in requests} != request_ids:
        raise ValueError("selected tasks lack complete request rows")
    documents_ids = {x["target_document_id"] for x in requests}
    documents = [x for x in read_rows(INPUT / "documents.jsonl") if x["document_id"] in documents_ids]
    if {x["document_id"] for x in documents} != documents_ids:
        raise ValueError("selected requests lack full target documents")
    model_inputs = [x for x in read_rows(INPUT / "baseline-model-inputs.jsonl") if x["model_input_id"] in model_input_ids]
    request_map = [x for x in read_rows(INPUT / "request-model-input-map.jsonl") if x["request_id"] in request_ids]
    comp_units = [x for x in read_rows(INPUT / "composition-units.jsonl") if x["group_id"] in request_ids]
    comp_bindings = []
    for x in read_rows(INPUT / "composition-unit-bindings.jsonl"):
        if x["group_id"] in request_ids:
            bind = {**x, "task_ids": [tid for tid in x["task_ids"] if tid in task_ids]}
            if bind["task_ids"]:
                comp_bindings.append(bind)
    row_plans = [x for x in read_rows(INPUT / "row-plans.jsonl") if x["case_id"] in selected_ids]
    case_plans = [x for x in read_rows(INPUT / "case-plans.jsonl") if x["case_id"] in selected_ids]
    if {x["case_id"] for x in case_plans} != selected_ids or sum(len(x["bindings"]) for x in row_plans) != len(tasks):
        raise ValueError("subset case/row/task coverage differs")
    if sum(len(x["task_ids"]) for x in comp_bindings) != len(tasks):
        raise ValueError("composition bindings do not cover every selected task")
    # Per-request scoped context units. Empty target axes become singleton shared
    # units with no desired value/selected axes; downstream safe fallback is unknown.
    sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
    spec = importlib.util.spec_from_file_location("scoped_units", ROOT / "experiments/sku-matching/composition_scoped_units_v1.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    scoped = module.build_units(requests, proof_ids=set())
    scoped_rows = list(scoped.values())
    policy_rows = []
    for request in requests:
        empty = not bool(request.get("target_selected_axes_text"))
        policy_rows.append({"request_id": request["request_id"], "unit_id": request["request_id"],
                            "empty_target_axes": empty,
                            "composition_unit_kind": "shared" if empty else "paired",
                            "empty_axes_fallback": "unknown" if empty else None,
                            "case_denominator_retained": True,
                            "proof_shortcut_used": False,
                            "production_adoption_allowed": False})
    outputs = {
        "case-plans.jsonl": case_plans, "row-plans.jsonl": row_plans, "tasks.jsonl": tasks,
        # Expected selected values are binding/label metadata, not model input.
        "requests.jsonl": [{k: v for k, v in x.items() if k != "selected_value"} for x in requests],
        "documents.jsonl": documents,
        "baseline-model-inputs.jsonl": model_inputs, "request-model-input-map.jsonl": request_map,
        "composition-units-original.jsonl": comp_units, "composition-unit-bindings.jsonl": comp_bindings,
        "composition-units-scoped.jsonl": scoped_rows, "request-execution-policy.jsonl": policy_rows,
    }
    for name, values in outputs.items():
        write_rows(data_dir / name, values)
    # Materialize exact frozen source case/product rows only after membership SHA is frozen.
    source_root = PREP / "source"
    source_base = source_root / ".lab-output/sku-independent-real-20261011"
    source_cases, source_products, source_manifests = [], [], {}
    selected_dossiers = {x["dossier_id"] for x in membership}
    selected_product_keys = {(x["dossier_id"], x["au_product_id"]) for x in membership}
    for version in ("v2", "v3"):
        idir = source_base / f"eval-{version}/run-token/inputs"
        case_path, product_path = idir / "cases.jsonl", idir / "products.jsonl"
        case_hash, product_hash = file_sha(case_path), file_sha(product_path)
        source_manifest = json.loads((idir / "manifest.json").read_text(encoding="utf-8"))
        source_manifests[version] = {"manifest_sha256": file_sha(idir / "manifest.json"),
                                     "cases_sha256": case_hash, "products_sha256": product_hash,
                                     "case_count_full_source": source_manifest["case_count"],
                                     "product_pair_count_full_source": source_manifest["product_pairs"]}
        for source_line, line in enumerate(case_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line:
                continue
            record = json.loads(line)
            if record["case_id"] in selected_ids:
                source_cases.append({"source_version": version, "source_line": source_line, "record": record})
        for source_line, line in enumerate(product_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line:
                continue
            record = json.loads(line)
            key = (str(record["dossier_id"]), str(record["au_product"]["product_id"]))
            if key in selected_product_keys:
                source_products.append({"source_version": version, "source_line": source_line, "record": record})
    if len(source_cases) != len(selected_ids) or {x["record"]["case_id"] for x in source_cases} != selected_ids:
        raise ValueError("subset source cases did not resolve uniquely")
    if {(x["record"]["dossier_id"], str(x["record"]["au_product"]["product_id"])) for x in source_products} != selected_product_keys:
        raise ValueError("subset source products did not resolve exactly")
    write_rows(data_dir / "source-cases.jsonl", source_cases)
    write_rows(data_dir / "source-products.jsonl", source_products)
    write_json(data_dir / "source-input-manifests.json", source_manifests)
    # Copy only raw source files referenced by selected source rows, after checking their declared SHA.
    refs = set()
    for row in source_cases + source_products:
        collect_file_sha(row["record"], refs)
    source_index_rows = json.loads((PREP / "source-index.json").read_text(encoding="utf-8"))
    source_index = {x["path"]: x for x in source_index_rows}
    captured_dir = data_dir / "source"
    captured_dir.mkdir()
    source_manifest_rows = []
    for path, expected in sorted(refs):
        src = source_root / path
        if not src.is_file():
            source_manifest_rows.append({"path": path, "sha256": None, "bytes": None,
                                         "source_index_sha256": None, "status": "missing_from_frozen_source_bundle"})
            continue
        actual = file_sha(src)
        index = source_index.get(path)
        if index and index["sha256"] != actual:
            raise ValueError("selected source-index SHA differs: " + Path(path).name)
        if expected and expected != actual:
            raise ValueError("selected source row SHA differs: " + Path(path).name)
        target = captured_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        source_manifest_rows.append({"path": path, "sha256": actual, "bytes": src.stat().st_size,
                                     "source_index_sha256": index["sha256"] if index else None, "status": "verified_copied"})
    write_rows(data_dir / "selected-source-manifest.jsonl", source_manifest_rows)
    # Source product documents are filtered copies of the v2 condition adapter docs.
    # They preserve complete visible target docs and exact document hashes.
    output_hashes = {name: file_sha(data_dir / name) for name in outputs}
    output_hashes.update({"source-cases.jsonl": file_sha(data_dir / "source-cases.jsonl"),
                          "source-products.jsonl": file_sha(data_dir / "source-products.jsonl"),
                          "source-input-manifests.json": file_sha(data_dir / "source-input-manifests.json"),
                          "selected-source-manifest.jsonl": file_sha(data_dir / "selected-source-manifest.jsonl")})
    task_by_case = collections.Counter(x["case_id"] for x in tasks)
    subset_manifest = {"schema_version": "sku-final-test-subset-materialization-v1", "labels_read": False,
        "predictions_read": False, "model_inference_run": False, "subset_plan_sha256": file_sha(plan_dir / "subset-plan.json"),
        "membership_sha256": file_sha(plan_dir / "subset-membership.jsonl"), "source_test_split": "independent_test",
        "selected_case_count": len(selected_ids), "selected_product_count": len(selected_product_keys),
        "selected_task_binding_count": len(tasks), "selected_request_count": len(requests),
        "task_count": len(tasks), "request_count": len(requests), "document_count": len(documents),
        "unique_baseline_model_input_count": len(model_inputs), "selected_document_count": len(documents),
        "selected_AU_row_plan_count": len(row_plans), "composition_unit_count": len(scoped_rows),
        "empty_target_axes_request_count": sum(not bool(r.get("target_selected_axes_text")) for r in requests),
        "empty_target_axes_policy": "per-request shared singleton; no desired value or selected axes in model payload; unknown fallback and case denominator retained",
        "failures_in_subset": "not selected as successful cases; all 259 remain in the parent full-test coverage table",
        "selected_source_case_count": len(source_cases), "selected_source_product_count": len(source_products),
        "selected_raw_capture_count": sum(x["status"] == "verified_copied" for x in source_manifest_rows),
        "selected_raw_capture_missing_count": sum(x["status"] != "verified_copied" for x in source_manifest_rows),
        "selected_raw_capture_bytes": sum(x["bytes"] or 0 for x in source_manifest_rows),
        "source_capture_complete": all(x["status"] == "verified_copied" for x in source_manifest_rows),
        "source_input_manifests": source_manifests, "output_sha256": output_hashes,
        "model_stage_contract": {"fixed_baseline": "fresh JEV/NLI on selected requests; no 83-case predictions replayed",
                                 "composition": "scoped per original request; exact model input cache key may fan out only across byte-identical input; final gate/policy remains to freeze",
                                 "unverified_shortcuts": "disabled"},
        "case_task_count_summary": summarize(list(task_by_case.values())),
        "production_adoption_allowed": False}
    write_json(data_dir / "manifest.json", subset_manifest)
    return subset_manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--phase", choices=("all", "freeze", "materialize"), default="all")
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    if args.phase in {"all", "freeze"}:
        plan, selected = freeze_membership(output)
    else:
        plan_path = output / "plan/subset-plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        selected = {x["case_id"] for x in read_rows(output / "plan/subset-membership.jsonl")}
    if args.phase in {"all", "materialize"}:
        result = materialize(output, selected)
        print(json.dumps({"schema_version": result["schema_version"], "selected_case_count": result["selected_case_count"],
                          "selected_product_count": result["selected_product_count"],
                          "selected_task_binding_count": result["selected_task_binding_count"],
                          "selected_request_count": result["selected_request_count"],
                          "unique_baseline_model_input_count": result["unique_baseline_model_input_count"],
                          "selected_raw_capture_count": result["selected_raw_capture_count"],
                          "selected_raw_capture_bytes": result["selected_raw_capture_bytes"],
                          "empty_target_axes_request_count": result["empty_target_axes_request_count"],
                          "labels_read": False, "predictions_read": False}, ensure_ascii=False))
    else:
        print(json.dumps({"schema_version": plan["schema_version"], "status": plan["status"],
                          "selected_case_count": plan["selected_case_count"],
                          "selected_product_count": plan["selected_product_count"],
                          "selected_task_binding_count": plan["selected_task_binding_count"],
                          "membership_sha256": plan["membership_sha256"],
                          "labels_read": False, "predictions_read": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
