#!/usr/bin/env python3
"""Configurable identity-only volume bridge for indexed TEST source phases.

This module stamps cohort metadata and delegates all semantic work to the
existing generic source builder and frozen literal/native proof modules.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROTOCOL = "sku-body-invariant-scoped-rakuten-inventory-test-v1"


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def load_config(path: str | Path) -> dict[str, Any]:
    """Load a volume config, resolving configured paths against the repo root."""
    config_path = Path(path).resolve()
    config = read_json(config_path)
    config["config_path"] = str(config_path)
    for key in ("identity_plan_dir", "base", "source_root", "full_condition_source_root", "output", "literal_output", "native_output"):
        if config.get(key) is not None:
            candidate = Path(config[key])
            config[key] = str((ROOT / candidate).resolve() if not candidate.is_absolute() else candidate.resolve())
    if config.get("identity_plan_dir"):
        plan_dir = Path(config["identity_plan_dir"])
        config.setdefault("identity_plan_manifest_sha256", sha(plan_dir / "manifest.json"))
        config.setdefault("selected_membership_sha256", sha(plan_dir / "selected-membership.jsonl"))
        config.setdefault("identity_bridge_sha256", sha(Path(__file__)))
    return config


def scope_source_manifest(input_dir: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    """Validate source-only cohort files and return a dynamically scoped manifest.

    Config is metadata-only: expected dataset, cohort counts, identity-plan
    manifest SHA, membership SHA, and identity bridge SHA. No labels or source
    values are read here.
    """
    input_dir = Path(input_dir).resolve()
    manifest = read_json(input_dir / "manifest.json")
    expected = {
        "schema_version": "sku-next-body-indexed-test-source-v1",
        "dataset": config["dataset"], "split": "test", "independent_test": True,
        "selected_product_count": int(config["selected_product_count"]),
        "selected_case_count": int(config["selected_case_count"]),
        "labels_read": False, "predictions_read": False,
        "model_inference_run": False, "scores_created": False,
        "test_split_read": True, "production_adoption_allowed": False,
        "identity_plan_manifest_sha256": config["identity_plan_manifest_sha256"],
        "selected_membership_sha256": config["selected_membership_sha256"],
        "identity_bridge_sha256": config["identity_bridge_sha256"],
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError("indexed TEST source scope/blindness guard failed: " + key)
    outputs = manifest.get("outputs_sha256")
    if not isinstance(outputs, dict) or not outputs:
        raise ValueError("indexed TEST source output hashes missing")
    for name, digest in outputs.items():
        if Path(name).name != name or sha(input_dir / name) != digest:
            raise ValueError("indexed TEST source output SHA mismatch: " + str(name))
    for field, filename in (("selection_policy_sha256", "selection-policy.json"),
                            ("selection_plan_sha256", "selection-plan.json")):
        if sha(input_dir / filename) != manifest.get(field):
            raise ValueError("identity plan hash mismatch: " + filename)
    counts = {"row_count": "row-plans.jsonl", "task_count": "tasks.jsonl",
              "derived_request_count": "requests.jsonl", "document_count": "documents.jsonl"}
    loaded: dict[str, list[dict[str, Any]]] = {}
    for field, filename in counts.items():
        loaded[filename] = rows(input_dir / filename)
        if len(loaded[filename]) != manifest.get(field):
            raise ValueError("cohort manifest population mismatch: " + field)
    cases = rows(input_dir / "case-plans.jsonl")
    membership = rows(input_dir / "membership.jsonl")
    if (len(cases) != expected["selected_case_count"]
            or len({str(row.get("au_product_id")) for row in cases}) != expected["selected_product_count"]
            or len({str(row.get("case_id")) for row in cases}) != len(cases)):
        raise ValueError("cohort case/product identity population mismatch")
    want = {(str(x.get("case_id")), str(x.get("au_product_id"))) for x in membership}
    got = {(str(x.get("case_id")), str(x.get("au_product_id"))) for x in cases}
    if len(want) != len(membership) or want != got:
        raise ValueError("source cases differ from frozen identity membership")
    task_ids = {str(t.get("task_id")) for t in loaded["tasks.jsonl"]}
    if len(task_ids) != len(loaded["tasks.jsonl"]):
        raise ValueError("duplicate task ID in source cohort")
    case_ids = {str(c.get("case_id")) for c in cases}
    request_ids = {str(r.get("request_id")) for r in loaded["requests.jsonl"]}
    task_bindings = {}
    for task in loaded["tasks.jsonl"]:
        if (str(task.get("case_id")) not in case_ids
                or str(task.get("request_id")) not in request_ids):
            raise ValueError("task case/request source binding missing")
        task_bindings[str(task["task_id"])] = (
            str(task["case_id"]), str(task.get("au_row_key")),
            str(task.get("request_id")), str(task.get("direction")), str(task.get("axis_key")))
    row_task_ids: list[str] = []
    for row in loaded["row-plans.jsonl"]:
        for binding in row.get("bindings", []):
            task_id = str(binding.get("task_id"))
            row_task_ids.append(task_id)
            expected_binding = (str(row.get("case_id")), str(row.get("au_row_key")),
                                str(binding.get("request_id")), str(binding.get("direction")),
                                str(binding.get("axis_key")))
            if task_bindings.get(task_id) != expected_binding:
                raise ValueError("row-plan task/source binding mismatch")
    if len(row_task_ids) != len(task_ids) or set(row_task_ids) != task_ids:
        raise ValueError("row/task source coverage mismatch")
    result = dict(manifest)
    result.update({"schema_version": PROTOCOL,
                   "selected_row_count": manifest["row_count"],
                   "selected_task_count": manifest["task_count"],
                   "selected_case_count": expected["selected_case_count"],
                   "selected_product_count": expected["selected_product_count"],
                   "identity_plan_manifest_sha256": expected["identity_plan_manifest_sha256"],
                   "selected_membership_sha256": expected["selected_membership_sha256"],
                   "identity_bridge_sha256": expected["identity_bridge_sha256"]})
    return result


def _source_phase(config: dict[str, Any]) -> None:
    plan_dir = Path(config["identity_plan_dir"])
    out = Path(config["output"])
    membership_path = plan_dir / "selected-membership.jsonl"
    plan_manifest = plan_dir / "manifest.json"
    raw_manifest = read_json(plan_manifest)
    membership = rows(membership_path)
    count_products = len({str(x.get("au_product_id")) for x in membership})
    count_cases = len({str(x.get("case_id")) for x in membership})
    if count_products != int(config["selected_product_count"]) or count_cases != int(config["selected_case_count"]):
        raise ValueError("identity plan counts differ from volume configuration")
    if raw_manifest.get("selected_product_count") != count_products or raw_manifest.get("selected_case_count") != count_cases or raw_manifest.get("selection_based_only_on_identity_fields") is not True:
        raise ValueError("identity plan is not a matching identity-only cohort")
    membership_sha = sha(membership_path)
    plan_sha = sha(plan_manifest)
    if raw_manifest.get("outputs_sha256", {}).get("selected-membership.jsonl") != membership_sha:
        raise ValueError("identity manifest does not pin selected membership bytes")
    if config.get("identity_plan_manifest_sha256", plan_sha) != plan_sha or config.get("selected_membership_sha256", membership_sha) != membership_sha:
        raise ValueError("identity plan SHA differs from the volume configuration")
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise FileExistsError("volume source output must be empty")
    clean = [{"case_id": str(x["case_id"]), "au_product_id": str(x["au_product_id"])} for x in membership]
    def dump(path: Path, value: Any) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (out / "membership.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for x in clean), encoding="utf-8")
    selection_plan = {"schema_version": "sku-next-body-indexed-test-selection-plan-v1", "dataset": config["dataset"],
        "selected_product_count": count_products, "selected_case_count": count_cases,
        "cases_per_product": config.get("cases_per_product"), "identity_plan_manifest_sha256": plan_sha,
        "selected_membership_sha256": membership_sha, "selected_membership": clean,
        "selection_based_only_on_identity_fields": True}
    policy = {"schema_version": "sku-next-body-indexed-test-selection-policy-v1",
        "source_identity_plan_schema": raw_manifest["schema_version"],
        "selection_method": "frozen preselected ID-only membership; no condition-text reselection",
        "identity_plan_manifest_sha256": plan_sha, "selected_membership_sha256": membership_sha,
        "selected_product_count": count_products, "selected_case_count": count_cases,
        "labels_read": False, "predictions_read": False, "condition_text_read_for_selection": False}
    dump(out / "selection-policy.json", policy)
    selection_plan["policy_sha256"] = sha(out / "selection-policy.json")
    dump(out / "selection-plan.json", selection_plan)
    builder = {"base": config["base"], "source_root": config["source_root"], "output": str(out),
        "dataset": config["dataset"], "prior_membership_sha256": membership_sha,
        "identity_plan_manifest_sha256": plan_sha, "selected_membership_sha256": membership_sha,
        "selected_product_count": count_products, "selected_case_count": count_cases,
        "cases_per_product": config.get("cases_per_product"), "identity_bridge_sha256": sha(Path(__file__))}
    dump(out / "source-builder-config.json", builder)
    subprocess.run([sys.executable, "-B", str(HERE / "prepare_indexed_test_source_generic_v1.py"), "--config", str(out / "source-builder-config.json")], check=True)
    final = read_json(out / "manifest.json")
    final.update({"identity_plan_manifest_sha256": plan_sha, "selected_membership_sha256": membership_sha,
                  "identity_bridge_sha256": sha(Path(__file__)),
                  "identity_plan_manifest_path": str(plan_manifest), "selected_membership_path": str(membership_path)})
    (out / "manifest.json").write_text(json.dumps(final, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _native_phase(config: dict[str, Any]) -> dict[str, Any]:
    import sku_body_invariant_scoped_rakuten_inventory_test_v1 as scoped
    input_dir = Path(config["output"])
    source_manifest = scope_source_manifest(input_dir, config)
    # Configure only metadata guard values; source extraction and validation remain frozen.
    indexed = scoped.INDEXED
    original = indexed.manifest
    indexed.manifest = lambda path: scope_source_manifest(path, config)
    try:
        report = scoped.prepare(input_dir, Path(config["source_root"]), Path(config["native_output"]))
    finally:
        indexed.manifest = original
    _stamp_scoped_manifest(report, config, source_manifest)
    (Path(config["native_output"]) / "manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _stamp_scoped_manifest(report: dict[str, Any], config: dict[str, Any],
                           source_manifest: dict[str, Any]) -> None:
    """Add cohort identity and raw-source pins omitted by the frozen wrapper."""
    report.update({"dataset": config["dataset"],
        "selected_product_count": config["selected_product_count"],
        "selected_case_count": config["selected_case_count"],
        "identity_plan_manifest_sha256": config["identity_plan_manifest_sha256"],
        "selected_membership_sha256": config["selected_membership_sha256"],
        "identity_bridge_sha256": config["identity_bridge_sha256"],
        "raw_source_capture_inventory_sha256": source_manifest["raw_source_capture_inventory_sha256"]})


def _literal_phase(config: dict[str, Any]) -> dict[str, Any]:
    """Run unchanged literal proof routines over dynamically scoped inputs."""
    import importlib.util
    import sys
    sys.path.insert(0, str(HERE))
    path = HERE / "prepare_indexed_test_literal_proofs_v1.py"
    spec = importlib.util.spec_from_file_location("_volume_literal_frozen", path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load frozen literal proof module")
    frozen = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = frozen
    spec.loader.exec_module(frozen)
    cohort = Path(config["output"])
    source = Path(config["source_root"])
    source_root = Path(config.get("full_condition_source_root", config["source_root"]))
    out = Path(config["literal_output"])
    frozen.COHORT, frozen.SOURCE, frozen.SOURCE_ROOT, frozen.OUT = cohort, source, source_root, out

    def dynamic_prepare_input():
        manifest = read_json(cohort / "manifest.json")
        validated = scope_source_manifest(cohort, config)
        cases_list = rows(cohort / "case-plans.jsonl")
        tasks_list = rows(cohort / "tasks.jsonl")
        requests_list = rows(cohort / "requests.jsonl")
        cases = frozen.indexed(cases_list, "case_id")
        tasks = frozen.indexed(tasks_list, "task_id")
        req = frozen.indexed(requests_list, "request_id")
        rowplans = rows(cohort / "row-plans.jsonl")
        if len(cases) != config["selected_case_count"] or len({str(c["au_product_id"]) for c in cases.values()}) != config["selected_product_count"]:
            raise ValueError("volume cohort populations differ from identity plan")
        for task in tasks.values():
            if task.get("case_id") not in cases or task.get("request_id") not in req:
                raise ValueError("task case/request binding missing")
        task_bind = {(t["task_id"], t["case_id"], t["au_row_key"], t["request_id"], t["direction"], t["axis_key"]) for t in tasks.values()}
        seen = set()
        for row in rowplans:
            for binding in row.get("bindings", []):
                item = (binding["task_id"], row["case_id"], row["au_row_key"], binding["request_id"], binding["direction"], binding["axis_key"])
                if item not in task_bind or item in seen:
                    raise ValueError("row-plan binding does not uniquely match task")
                seen.add(item)
        if seen != task_bind:
            raise ValueError("row plans omit tasks")
        import prepare_dev_literal_family_proofs_v1 as source_loader
        source_manifest = read_json(source_root / "manifest.json")
        source_cases, source_products, pins, selected = source_loader.load_original_records(source, source_manifest, cases)
        out.mkdir(parents=True, exist_ok=False)
        inp = out / "inputs"
        inp.mkdir()
        frozen.wr(inp / "tasks.jsonl", (tasks[k] for k in sorted(tasks)))
        frozen.wr(inp / "source-cases.jsonl", (source_cases[k] for k in sorted(source_cases)))
        frozen.wr(inp / "source-products.jsonl", (source_products[k] for k in sorted(source_products)))
        frozen.wr(inp / "row-plans.jsonl", sorted(rowplans, key=lambda x: (x["case_id"], x["au_row_key"])))
        frozen.wr(inp / "selected-source-manifest.jsonl", sorted(selected, key=lambda x: x["case_id"]))
        frozen.wj(inp / "source-input-manifests.json", pins)
        names = ("tasks.jsonl", "source-cases.jsonl", "source-products.jsonl", "row-plans.jsonl", "selected-source-manifest.jsonl", "source-input-manifests.json")
        staged = {"schema_version": "sku-next-body-invariant-test-literal-proof-input-v1", "dataset": config["dataset"],
            "cohort_manifest_sha256": sha(cohort / "manifest.json"), "selected_case_count": len(cases),
            "selected_product_count": len({str(x["au_product_id"]) for x in cases.values()}),
            "row_plan_count": len(rowplans), "task_count": len(tasks), "request_count": len(req), "source_input_pins": pins,
            "output_sha256": {n: sha(inp / n) for n in names}, "labels_read": False, "predictions_read": False,
            "model_inference_run": False, "test_split_read": True,
            "identity_plan_manifest_sha256": validated["identity_plan_manifest_sha256"],
            "selected_membership_sha256": validated["selected_membership_sha256"],
            "raw_source_capture_inventory_sha256": manifest.get("raw_source_capture_inventory_sha256"),
            "production_adoption_allowed": False}
        frozen.wj(inp / "manifest.json", staged)
        return tasks, source_cases, source_products, pins, staged

    frozen.prepare_input = dynamic_prepare_input
    frozen.run()
    # Only dataset metadata is adapted; the proof functions and proof results
    # remain those produced by the unchanged frozen module.
    proof_manifest = read_json(out / "manifest.json")
    proof_manifest["dataset"] = config["dataset"]
    proof_manifest["selected_product_count"] = config["selected_product_count"]
    proof_manifest["selected_case_count"] = config["selected_case_count"]
    proof_manifest["identity_plan_manifest_sha256"] = config["identity_plan_manifest_sha256"]
    proof_manifest["selected_membership_sha256"] = config["selected_membership_sha256"]
    proof_manifest["identity_bridge_sha256"] = config["identity_bridge_sha256"]
    (out / "manifest.json").write_text(json.dumps(proof_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return proof_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--phase", choices=("source", "literal", "native"), required=True)
    args = parser.parse_args()
    cfg = load_config(args.config)
    if args.phase == "source":
        _source_phase(cfg)
    elif args.phase == "native":
        _native_phase(cfg)
    else:
        _literal_phase(cfg)


if __name__ == "__main__":
    main()
