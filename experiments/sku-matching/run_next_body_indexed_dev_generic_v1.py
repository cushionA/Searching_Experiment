#!/usr/bin/env python3
"""Config-driven, source-bound indexed DEV runner; never infers or grades implicitly."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import shutil
import sys
import traceback
from typing import Any
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENTS = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(EXPERIMENTS))
import evaluate_dev_source_bound_whole_sku_gate_v1 as EVAL  # noqa: E402
import grade_final_test_subset_v1 as SCORE_ORIGINAL  # noqa: E402
import sku_body_invariant_choice_v3 as BODY  # noqa: E402
import sku_body_invariant_indexed_transport_v1 as INDEXED  # noqa: E402
import sku_directional_choice_nli_v1 as NLI  # noqa: E402


class Bundle:
    def __init__(self, config_path: Path):
        self.config_path = Path(config_path).resolve()
        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("runner configuration must be a JSON object")
        self.config = raw
        self.cohort = self.path("cohort_dir")
        self.prepared = self.path("prepared_dir")
        self.proofs = self.path("proof_dir")
        self.scoped = self.path("scoped_source_dir")
        self.source_root = self.path("source_root")
        self.scope_module_path = self.path("scope_module_path")
        self.scope = load_module(self.scope_module_path, "indexed_scoped_source_runtime")
        if not callable(getattr(self.scope, "validate_source_response", None)):
            raise ValueError("scoped source validator lacks validate_source_response")
        self.preflight_output = self.path("preflight_output")
        self.source = self.verify()

    def path(self, key: str) -> Path:
        val = self.config.get(key)
        if not isinstance(val, str) or not val:
            raise ValueError(f"configuration requires nonempty {key}")
        p = Path(val)
        return (p if p.is_absolute() else ROOT / p).resolve()

    def verify(self) -> dict[str, Any]:
        cohort_m = read_json(self.prepared / "manifest.json")
        self._verify_artifact_map(self.prepared, cohort_m.get("outputs_sha256", {}), "cohort")
        dataset = cohort_m.get("dataset")
        if (cohort_m.get("schema_version") != "sku-next-body-indexed-dev-source-v1"
                or cohort_m.get("split") != "dev" or cohort_m.get("independent_test") is not False
                or not isinstance(dataset, str) or not dataset
                or cohort_m.get("labels_read") is not False
                or cohort_m.get("predictions_read") is not False
                or cohort_m.get("model_inference_run") is not False
                or cohort_m.get("scores_created") is not False
                or cohort_m.get("test_split_read") is not False
                or cohort_m.get("production_adoption_allowed") is not False):
            raise ValueError("prepared source is not a blind DEV cohort")
        for field, file_name in (("selection_policy_sha256", "selection-policy.json"),
                                 ("selection_plan_sha256", "selection-plan.json")):
            if sha_file(self.prepared / file_name) != cohort_m.get(field):
                raise ValueError(f"cohort {file_name} differs from its frozen manifest")
        policy = read_json(self.prepared / "selection-policy.json")
        plan = read_json(self.prepared / "selection-plan.json")
        if plan.get("policy_sha256") != cohort_m.get("selection_policy_sha256"):
            raise ValueError("identity selection plan is not bound to the exact frozen policy")
        cohort_rows = {name: read_rows(self.prepared / name) for name in
                       ("membership.jsonl", "case-plans.jsonl", "row-plans.jsonl", "tasks.jsonl", "requests.jsonl", "documents.jsonl")}
        counts = {"selected_case_count": len(cohort_rows["membership.jsonl"]),
                  "selected_product_count": len({r.get("au_product_id") for r in cohort_rows["membership.jsonl"]}),
                  "row_count": len(cohort_rows["row-plans.jsonl"]),
                  "task_count": len(cohort_rows["tasks.jsonl"]),
                  "derived_request_count": len(cohort_rows["requests.jsonl"]),
                  "document_count": len(cohort_rows["documents.jsonl"])}
        for key, actual in counts.items():
            if cohort_m.get(key) != actual or actual <= 0:
                raise ValueError(f"prepared source manifest population mismatch: {key}")
        members = unique_index(cohort_rows["membership.jsonl"], "case_id", "cohort membership")
        cases = unique_index(cohort_rows["case-plans.jsonl"], "case_id", "case plans")
        tasks = unique_index(cohort_rows["tasks.jsonl"], "task_id", "original tasks")
        if set(members) != set(cases) or not tasks or any(t.get("case_id") not in members for t in tasks.values()):
            raise ValueError("cohort case/task membership is not exact")
        source_root_inventory = self.source_root.parent / "inventory.json"
        source_inventory_sha = cohort_m.get("source_bundle_inventory_sha256")
        if not source_root_inventory.is_file() or sha_file(source_root_inventory) != source_inventory_sha:
            raise ValueError("raw-source inventory differs from the frozen cohort inventory SHA")
        self.source_inventory = source_root_inventory

        scoped_m = read_json(self.scoped / "manifest.json")
        self._verify_artifact_map(self.scoped, scoped_m.get("artifact_sha256", {}), "scoped source")
        source_adapter_name = scoped_m.get("scoped_inventory_module")
        source_adapter_copy = self.scoped / source_adapter_name if isinstance(source_adapter_name, str) else None
        if (scoped_m.get("schema_version") != "sku-body-invariant-scoped-rakuten-inventory-v1"
                or scoped_m.get("dataset") != dataset or scoped_m.get("split") != "dev"
                or scoped_m.get("source_input_manifest_sha256") != sha_file(self.prepared / "manifest.json")
                or scoped_m.get("task_count") != counts["task_count"]
                or scoped_m.get("row_count") != counts["row_count"]
                or scoped_m.get("derived_request_count") != counts["derived_request_count"]
                or scoped_m.get("document_count") != counts["document_count"]
                or scoped_m.get("private_validation_binding_count") != counts["task_count"]
                or scoped_m.get("private_validation_bindings_preserved_byte_exact") is not True
                or scoped_m.get("worker_payload_contains_private_bindings") is not False
                or scoped_m.get("prepared_pair_count") != scoped_m.get("payload_count")
                or scoped_m.get("preparation_error_count") != 0
                or scoped_m.get("labels_read") is not False or scoped_m.get("predictions_read") is not False
                or scoped_m.get("model_inference_run") is not False or scoped_m.get("scores_created") is not False
                or scoped_m.get("semantic_truth_verified") is not False
                or scoped_m.get("production_adoption_allowed") is not False
                or source_adapter_copy is None or not source_adapter_copy.is_file()
                or sha_file(source_adapter_copy) != scoped_m.get("scoped_inventory_module_sha256")
                or sha_file(self.scope_module_path) != scoped_m.get("scoped_inventory_module_sha256")):
            raise ValueError("scoped-native source preparation does not match the cohort or scoped validator")
        scoped_freeze = read_json(self.scoped / "freeze.json")
        if (scoped_freeze.get("source_root") != str(self.source_root)
                or scoped_freeze.get("inference_run") is not False
                or scoped_freeze.get("gold_or_worker_responses_read") is not False
                or scoped_freeze.get("source_fact_errors") != 0):
            raise ValueError("scoped source freeze is not exact, blind, and fact-error-free")
        scoped_rows = {name: read_rows(self.scoped / name) for name in
                       ("source-membership.jsonl", "source-case-plans.jsonl", "source-row-plans.jsonl",
                        "source-tasks.jsonl", "source-requests.jsonl", "model-pairs-v3.jsonl",
                        "model-payloads-v3.jsonl", "validation-bindings.jsonl", "source-bodies.jsonl")}
        for key, name in (("document_count", "source-membership.jsonl"),
                          ("task_count", "source-tasks.jsonl"),
                          ("row_count", "source-row-plans.jsonl"),
                          ("derived_request_count", "source-requests.jsonl"),
                          ("prepared_pair_count", "model-pairs-v3.jsonl"),
                          ("payload_count", "model-payloads-v3.jsonl"),
                          ("private_validation_binding_count", "validation-bindings.jsonl"),
                          ("document_count", "source-bodies.jsonl")):
            expected = scoped_m.get(key)
            if len(scoped_rows[name]) != expected:
                raise ValueError(f"scoped source file population differs from manifest: {name}")
        task_ids = set(tasks)
        scoped_tasks = unique_index(scoped_rows["source-tasks.jsonl"], "task_id", "scoped source task")
        if task_ids != set(scoped_tasks):
            raise ValueError("scoped source task IDs differ from the original complete task set")
        pairs = scoped_rows["model-pairs-v3.jsonl"]
        payloads = scoped_rows["model-payloads-v3.jsonl"]
        pair_by_id = unique_index(pairs, "pair_id", "prepared model pair")
        payload_by_id = unique_index(payloads, "pair_id", "prepared model payload")
        if set(pair_by_id) != set(payload_by_id):
            raise ValueError("scoped source model pair and payload IDs differ")
        bindings = scoped_rows["validation-bindings.jsonl"]
        binding_ids = [(r.get("task_binding") or {}).get("task_id") for r in bindings]
        if (len(binding_ids) != len(task_ids) or len(set(binding_ids)) != len(task_ids)
                or set(binding_ids) != task_ids):
            raise ValueError("full scoped private validation bindings do not cover every original task")

        proof_m = read_json(self.proofs / "manifest.json")
        for name, digest in proof_m.get("outputs_sha256", {}).items():
            if not (self.proofs / name).is_file() or sha_file(self.proofs / name) != digest:
                raise ValueError(f"literal proof output SHA mismatch: {name}")
        if (proof_m.get("schema_version") != "sku-next-body-invariant-literal-proofs-v1"
                or proof_m.get("dataset") != dataset
                or proof_m.get("cohort_manifest_sha256") != sha_file(self.prepared / "manifest.json")
                or proof_m.get("source_bundle_inventory_sha256") != source_inventory_sha
                or proof_m.get("selected_case_count") != counts["selected_case_count"]
                or proof_m.get("selected_product_count") != counts["selected_product_count"]
                or proof_m.get("row_count") != counts["row_count"]
                or proof_m.get("task_count") != counts["task_count"]
                or proof_m.get("request_count") != counts["derived_request_count"]
                or proof_m.get("labels_read") is not False or proof_m.get("predictions_read") is not False
                or proof_m.get("model_inference_run") is not False or proof_m.get("scores_created") is not False
                or proof_m.get("production_adoption_allowed") is not False):
            raise ValueError("literal proof manifest does not match the exact blind cohort")
        proof_rows = {name: read_rows(self.proofs / name) for name in
                      ("task-literal-proof-states.jsonl", "task-source-verification.jsonl", "literal-pair-proofs.jsonl")}
        if (len(proof_rows["task-literal-proof-states.jsonl"]) != counts["task_count"]
                or len(proof_rows["task-source-verification.jsonl"]) != counts["task_count"]):
            raise ValueError("literal proof states and source receipts do not cover every task")
        literal_states = adapt_literal_proofs(tasks=list(tasks.values()),
            proof_states=proof_rows["task-literal-proof-states.jsonl"],
            source_receipts=proof_rows["task-source-verification.jsonl"],
            pair_proofs=proof_rows["literal-pair-proofs.jsonl"])
        partition = derive_partition(list(tasks.values()), bindings, pairs, literal_states,
                                     proof_rows["task-source-verification.jsonl"])
        return {"cohort_manifest": cohort_m, "cohort_rows": cohort_rows, "counts": counts,
            "policy": policy, "plan": plan, "scoped_manifest": scoped_m, "scoped_freeze": scoped_freeze,
            "scoped_rows": scoped_rows, "pair_by_id": pair_by_id, "payload_by_id": payload_by_id,
            "bindings": bindings, "proof_manifest": proof_m, "proof_rows": proof_rows,
            "literal_states": literal_states, "partition": partition,
            "source_inventory": source_root_inventory, "scope_module_sha256": sha_file(self.scope_module_path)}

    @staticmethod
    def _verify_artifact_map(directory: Path, artifacts: Any, label: str) -> None:
        if not isinstance(artifacts, dict) or not artifacts:
            raise ValueError(f"{label} manifest has no artifact hash map")
        for name, digest in artifacts.items():
            path = directory / name
            if not path.is_file() or path.is_symlink() or sha_file(path) != digest:
                raise ValueError(f"{label} artifact pin mismatch: {name}")


def load_module(path: Path, alias: str):
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load configured module {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def pin(path: Path) -> dict[str, Any]:
    return {"bytes": path.stat().st_size, "sha256": sha_file(path)}


def read_json(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_rows(path: Path) -> list[dict]:
    return NLI.read_rows(Path(path))


def unique_index(rows: list[dict], key: str, label: str) -> dict[str, dict]:
    out = {}
    for row in rows:
        ident = row.get(key)
        if not isinstance(ident, str) or not ident or ident in out:
            raise ValueError(f"missing or duplicate {label} identity")
        out[ident] = row
    return out


def write_json(path: Path, value: dict) -> None:
    with Path(path).open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        f.write("\n")


def write_rows(path: Path, rows: list[dict]) -> None:
    with Path(path).open("xb") as f:
        for row in rows:
            f.write(BODY.canon(row) + b"\n")


def adapt_literal_proofs(tasks: list[dict], proof_states: list[dict], source_receipts: list[dict],
                         pair_proofs: list[dict]) -> list[dict]:
    task_by = unique_index(tasks, "task_id", "literal task")
    state_by = unique_index(proof_states, "task_id", "literal proof state")
    receipt_by = unique_index(source_receipts, "task_id", "source receipt")
    if set(task_by) != set(state_by) or set(task_by) != set(receipt_by):
        raise ValueError("literal proof/receipt task population differs from original tasks")
    pair_by_task = {}
    for row in pair_proofs:
        ids = [row.get("forward_task_id"), row.get("reverse_task_id")]
        if (not all(isinstance(v, str) and v for v in ids) or ids[0] == ids[1]
                or any(v in pair_by_task for v in ids)):
            raise ValueError("literal pair proof task identities are missing or duplicated")
        pair_by_task[ids[0]] = row
        pair_by_task[ids[1]] = row
    if set(pair_by_task) != set(task_by):
        raise ValueError("literal pair proofs do not exactly cover every original task")
    out = []
    for task_id, task in task_by.items():
        state, receipt = state_by[task_id], receipt_by[task_id]
        family = receipt.get("ordered_family")
        selected = (task.get("raw_condition_ref") or {}).get("value")
        if (receipt.get("status") != "verified_complete_ordered_raw_family"
                or not isinstance(family, list) or family.count(selected) != 1
                or receipt.get("selected_value") != selected):
            raise ValueError("literal proof source receipt does not bind exact selected ordered family")
        pair = pair_by_task[task_id]
        if state.get("status") in {"support", "conflict"}:
            if (pair.get("status") != state.get("status") or pair.get("ordered_family") != family
                    or state.get("proof_pair_id") != pair.get("forward_task_id") + "|" + pair.get("reverse_task_id")):
                raise ValueError("decisive literal task is not bound to its verified pair proof")
            out.append({**state, "literal_proof_verified": True, "raw_source_family_verified": True,
                "selected_value": selected, "ordered_family": family,
                "proof_pair_id": state["proof_pair_id"]})
        else:
            out.append(dict(state))
    return out


def derive_partition(tasks: list[dict], bindings: list[dict], pairs: list[dict],
                     proof_states: list[dict], source_receipts: list[dict]) -> dict:
    task_by = unique_index(tasks, "task_id", "partition task")
    proof_by = unique_index(proof_states, "task_id", "partition proof")
    receipt_by = unique_index(source_receipts, "task_id", "partition receipt")
    pair_by = unique_index(pairs, "pair_id", "partition pair")
    if set(task_by) != set(proof_by) or set(task_by) != set(receipt_by):
        raise ValueError("source literal states and receipts must cover all original tasks")
    groups, seen = {}, set()
    for binding in bindings:
        task_id = (binding.get("task_binding") or {}).get("task_id")
        pair_id = binding.get("pair_id")
        if task_id not in task_by or task_id in seen:
            raise ValueError("private bindings contain a missing, foreign, or duplicate task")
        seen.add(task_id)
        if binding.get("preparation_status") == "ready":
            if pair_id not in pair_by:
                raise ValueError("ready binding has a foreign model pair")
            groups.setdefault(pair_id, []).append(task_id)
        elif pair_id is not None:
            raise ValueError("contextless binding has a model pair ID")
    if seen != set(task_by) or set(groups) != set(pair_by):
        raise ValueError("full task bindings and model pairs do not exactly partition")
    protected, dispatched, protected_details = [], [], []
    for pair_id, ids in groups.items():
        states = [EVAL.source_literal_state(task_by[tid], proof_by[tid], receipt_by[tid]) for tid in ids]
        if all(s is not None for s in states):
            protected.append(pair_id)
            protected_details.append({"pair_id": pair_id, "status": "literal_source_protected_not_dispatched",
                "task_ids": sorted(ids), "protected_task_count": len(ids)})
        else:
            dispatched.append(pair_id)
    if set(protected) & set(dispatched) or set(protected) | set(dispatched) != set(pair_by):
        raise ValueError("literal-protected and dispatched pairs do not partition all prepared pairs")
    return {"protected_pair_ids": sorted(protected), "dispatch_pair_ids": sorted(dispatched),
            "protected_details": sorted(protected_details, key=lambda x: x["pair_id"]),
            "protected_task_count": sum(row["protected_task_count"] for row in protected_details)}


def ref_index(bundle: Bundle) -> list[dict]:
    partition = bundle.source["partition"]
    dispatch = set(partition["dispatch_pair_ids"])
    pairs = bundle.source["scoped_rows"]["model-pairs-v3.jsonl"]
    # Stable pair_ref values cover all pairs; packetizers use only dispatch rows.
    ordered = sorted(pairs, key=lambda p: p["pair_id"])
    return [{"pair_ref": i, "pair_id": row["pair_id"], "cache_key": row.get("cache_key"),
             "option_count": len(row.get("option_values", [])),
             "dispatch_status": "dispatched" if row["pair_id"] in dispatch else "literal_protected_not_dispatched"}
            for i, row in enumerate(ordered)]


def write_preflight(bundle: Bundle) -> dict:
    out = bundle.preflight_output
    if out.exists() or out.is_symlink() or not out.is_relative_to(ROOT / ".lab-output"):
        raise FileExistsError("preflight output must be a new directory under .lab-output")
    out.mkdir(parents=True)
    try:
        partition = bundle.source["partition"]
        refs = ref_index(bundle)
        write_rows(out / "pair-ref-index.jsonl", refs)
        write_rows(out / "dispatch-pair-ids.jsonl", [{"pair_id": x} for x in partition["dispatch_pair_ids"]])
        write_rows(out / "literal-protected-pair-ids.jsonl", [{"pair_id": x} for x in partition["protected_pair_ids"]])
        write_rows(out / "literal-protected-no-dispatch.jsonl", partition["protected_details"])
        pair_receipt = {"schema_version": "indexed-dev-source-partition-v1",
            "status": "complete_source_derived_pre_model_partition",
            "dataset": bundle.source["cohort_manifest"]["dataset"],
            "case_count": bundle.source["counts"]["selected_case_count"],
            "product_count": bundle.source["counts"]["selected_product_count"],
            "row_count": bundle.source["counts"]["row_count"],
            "task_count": bundle.source["counts"]["task_count"],
            "prepared_pair_count": len(bundle.source["pair_by_id"]),
            "dispatch_pair_count": len(partition["dispatch_pair_ids"]),
            "dispatch_pair_ids_sha256": digest_canonical(partition["dispatch_pair_ids"]),
            "literal_protected_pair_count": len(partition["protected_pair_ids"]),
            "literal_protected_pair_ids_sha256": digest_canonical(partition["protected_pair_ids"]),
            "literal_protected_task_count": partition["protected_task_count"],
            "contextless_task_count": sum(b.get("preparation_status") != "ready" for b in bundle.source["bindings"]),
            "protected_pairs_are_not_missing_model_responses": True,
            "labels_read": False, "inference_run": False, "scores_created": False,
            "source_pins": source_pins(bundle)}
        write_json(out / "source-partition-receipt.json", pair_receipt)
        inventory = {"schema_version": "indexed-dev-preflight-inventory-v1",
            "files": {p.name: pin(p) for p in sorted(out.iterdir()) if p.is_file()},
            "labels_read": False, "inference_run": False, "scores_created": False}
        write_json(out / "preflight-inventory.json", inventory)
        return pair_receipt
    except Exception as exc:
        write_json(out / "preflight-failure.json", {"error": str(exc), "error_type": type(exc).__name__,
            "labels_read": False, "inference_run": False, "scores_created": False,
            "traceback": traceback.format_exc()})
        raise


def source_pins(bundle: Bundle) -> dict[str, str]:
    files = [bundle.prepared / "manifest.json", bundle.prepared / "selection-policy.json",
        bundle.prepared / "selection-plan.json", bundle.scoped / "manifest.json", bundle.scoped / "freeze.json",
        bundle.proofs / "manifest.json", bundle.source_inventory,
        bundle.config_path, Path(__file__).resolve(), bundle.scope_module_path,
        EXPERIMENTS / "test_run_next_body_indexed_dev_generic_v1.py",
        EXPERIMENTS / "prepare_indexed_dev_source_generic_v1.py",
        EXPERIMENTS / "prepare_indexed_dev_native_source_v1.py",
        EXPERIMENTS / "prepare_indexed_dev_literal_proofs_generic_v1.py",
        EXPERIMENTS / "sku_body_invariant_indexed_transport_v1.py",
        EXPERIMENTS / "evaluate_dev_source_bound_whole_sku_gate_v1.py",
        EXPERIMENTS / "sku_body_invariant_choice_v3.py",
        EXPERIMENTS / "sku_body_invariant_choice_v3.schema.json",
        EXPERIMENTS / "sku_directional_choice_nli_v1.py",
        EXPERIMENTS / "grade_final_test_subset_v1.py"]
    return {p.resolve().relative_to(ROOT).as_posix(): sha_file(p) for p in files}


def source_artifact_pins(bundle: Bundle) -> dict[str, dict[str, Any]]:
    """Pin every immutable cohort/proof/source capture, including private bindings."""
    roots = {"cohort": bundle.prepared, "scoped_source": bundle.scoped, "literal_proofs": bundle.proofs}
    out: dict[str, dict[str, Any]] = {}
    for label, directory in roots.items():
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"symlink in frozen source artifact tree: {path}")
            if path.is_file():
                out[f"{label}/{path.relative_to(directory).as_posix()}"] = pin(path)
    inv = bundle.source_inventory
    out["source_inventory"] = pin(inv)
    return out


def digest_canonical(value: Any) -> str:
    return hashlib.sha256(BODY.canon(value)).hexdigest()


def validate_partition_receipt(bundle: Bundle, preflight_dir: Path) -> dict:
    receipt = read_json(preflight_dir / "source-partition-receipt.json")
    dispatch = [r["pair_id"] for r in read_rows(preflight_dir / "dispatch-pair-ids.jsonl")]
    protected = [r["pair_id"] for r in read_rows(preflight_dir / "literal-protected-pair-ids.jsonl")]
    expected = bundle.source["partition"]
    if (receipt.get("schema_version") != "indexed-dev-source-partition-v1"
            or receipt.get("status") != "complete_source_derived_pre_model_partition"
            or dispatch != expected["dispatch_pair_ids"] or protected != expected["protected_pair_ids"]
            or receipt.get("dispatch_pair_ids_sha256") != digest_canonical(dispatch)
            or receipt.get("literal_protected_pair_ids_sha256") != digest_canonical(protected)
            or set(dispatch) | set(protected) != set(bundle.source["pair_by_id"])
            or set(dispatch) & set(protected)):
        raise ValueError("pre-model source partition differs from the exact full prepared-pair population")
    return receipt


def validate_raw_snapshot(raw_path: Path, snapshot_receipt_path: Path, failure_path: Path,
                          partition_receipt: dict, pair_refs: list[dict],
                          preflight_dir: Path) -> tuple[list[dict], list[dict], dict]:
    raw_path, snapshot_receipt_path, failure_path = map(Path, (raw_path, snapshot_receipt_path, failure_path))
    for path in (raw_path, snapshot_receipt_path, failure_path):
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT / ".lab-output"):
            raise ValueError("indexed raw snapshot artifacts must be regular .lab-output files")
    receipt = read_json(snapshot_receipt_path)
    dispatch_ids = {r["pair_id"] for r in read_rows(preflight_dir / "dispatch-pair-ids.jsonl")}
    protected_ids = {r["pair_id"] for r in read_rows(preflight_dir / "literal-protected-pair-ids.jsonl")}
    pair_ids = {row["pair_id"] for row in pair_refs}
    if (receipt.get("schema_version") != "indexed-dev-raw-snapshot-v1"
            or receipt.get("status") != "complete_firstpass_snapshot_frozen"
            or receipt.get("raw_path") != raw_path.resolve().relative_to(ROOT).as_posix()
            or receipt.get("raw_bytes") != raw_path.stat().st_size
            or receipt.get("raw_sha256") != sha_file(raw_path)
            or receipt.get("partition_receipt_sha256") != sha_file(preflight_dir / "source-partition-receipt.json")
            or receipt.get("preflight_inventory_sha256") != sha_file(preflight_dir / "preflight-inventory.json")
            or receipt.get("dispatch_pair_count") != len(dispatch_ids)
            or receipt.get("dispatch_pair_ids_sha256") != digest_canonical(sorted(dispatch_ids))
            or receipt.get("literal_protected_pair_count") != len(protected_ids)
            or receipt.get("literal_protected_pair_ids_sha256") != digest_canonical(sorted(protected_ids))
            or receipt.get("labels_read") is not False or receipt.get("scores_created") is not False):
        raise ValueError("raw indexed snapshot receipt is incomplete or source-partition-mismatched")
    if (len(pair_refs) != len(pair_ids) or not dispatch_ids <= pair_ids
            or dispatch_ids & protected_ids or dispatch_ids | protected_ids != pair_ids):
        raise ValueError("pair-ref index and partition do not exactly cover prepared pairs")
    raw_bytes = raw_path.read_bytes()
    raw = parse_jsonl_bytes(raw_bytes, str(raw_path))
    by_ref = {r["pair_ref"]: r for r in pair_refs}
    failures = read_rows(failure_path)
    if pin(failure_path) != {"bytes": receipt.get("failure_ledger_bytes"), "sha256": receipt.get("failure_ledger_sha256")}:
        raise ValueError("raw snapshot failure ledger pin differs")
    failure_by_pair = unique_index(failures, "pair_id", "missing dispatched pair failure")
    if set(failure_by_pair) & protected_ids or set(failure_by_pair) - dispatch_ids:
        raise ValueError("failure ledger includes protected or foreign pair")
    assigned = []
    for response in raw:
        ref = response.get("pair_ref")
        if type(ref) is not int or ref not in by_ref:
            raise ValueError("raw indexed response has foreign or malformed pair_ref")
        pair_id = by_ref[ref]["pair_id"]
        if pair_id in protected_ids or pair_id not in dispatch_ids:
            raise ValueError("raw indexed response belongs to protected or foreign pair")
        assigned.append(pair_id)
    if len(assigned) != len(set(assigned)):
        raise ValueError("raw indexed snapshot contains duplicate pair responses")
    missing = dispatch_ids - set(assigned)
    if set(failure_by_pair) != missing or any(r.get("status") != "missing_firstpass_response_unknown"
            or r.get("semantic_retry_performed") is not False for r in failures):
        raise ValueError("missing response ledger must exactly equal dispatched pairs without a first-pass response")
    if (receipt.get("response_count") != len(raw)
            or receipt.get("response_pair_ids_sha256") != digest_canonical(sorted(assigned))
            or receipt.get("failure_count") != len(failures)
            or receipt.get("all_predictions_frozen") is not True):
        raise ValueError("raw indexed response/failure counts differ from snapshot receipt")
    return raw, failures, receipt


def parse_jsonl_bytes(data: bytes, where: str) -> list[dict]:
    out = []
    for i, line in enumerate(data.splitlines(), 1):
        if not line.strip():
            raise ValueError(f"blank JSONL line {i} in {where}")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"non-object JSONL row {i} in {where}")
        out.append(value)
    return out


def assemble(config_path: Path, raw_path: Path, snapshot_receipt: Path,
             failure_ledger: Path, preflight_dir: Path, output_dir: Path) -> dict:
    bundle = Bundle(config_path)
    partition_receipt = validate_partition_receipt(bundle, preflight_dir)
    refs = read_rows(preflight_dir / "pair-ref-index.jsonl")
    raw, failures, snapshot = validate_raw_snapshot(raw_path, snapshot_receipt, failure_ledger,
                                                      partition_receipt, refs, preflight_dir)
    pairs = bundle.source["pair_by_id"]
    payloads = bundle.source["payload_by_id"]
    source_bodies = unique_index(bundle.source["scoped_rows"]["source-bodies.jsonl"],
                                 "document_sha256", "source body")
    ref_by_id = unique_index(refs, "pair_id", "pair-ref index")
    ref_by_number = {r["pair_ref"]: r for r in refs}
    validated_pairs, render_report, source_citations = [], [], []
    for indexed in raw:
        pair_ref = indexed["pair_ref"]
        pair_id = ref_by_number[pair_ref]["pair_id"]
        pair, payload = pairs[pair_id], payloads[pair_id]
        target_sha = payload.get("context", {}).get("target_document_sha256")
        source_body = source_bodies.get(target_sha)
        if source_body is None:
            render_report.append({"pair_id": pair_id, "status": "invalid_unknown", "error": "target source body absent"})
            continue
        try:
            rendered = INDEXED.render_indexed_response(indexed, pair, payload, pair_ref)
            validation = bundle.scope.validate_source_response(rendered, pair, payload, source_body, bundle.source_root)
            decisions = []
            for decision in rendered["decisions"]:
                copied = dict(decision)
                copied["citations_verified"] = (2 * len(decision.get("target_fact_indices", []))
                    + len(decision.get("body_quote_refs", [])))
                decisions.append(copied)
            checked = {"pair_id": pair_id, "cache_key": pair["cache_key"], "judgments": decisions,
                "source_citations_verified": validation.get("source_citations_verified") is True,
                "semantic_truth_verified": False, "sourceproof_certified": False,
                "production_adoption_allowed": False,
                "validated_source_citation_count": len(validation.get("citations", []))}
            if len(decisions) != len(pair.get("option_values", [])):
                raise ValueError("scoped source validator did not accept a complete option vector")
            validated_pairs.append(checked)
            source_citations.extend(validation.get("citations", []))
            render_report.append({"pair_id": pair_id, "pair_ref": pair_ref,
                "status": "scoped_source_citations_verified", "decision_count": len(decisions),
                "validated_source_citation_count": checked["validated_source_citation_count"]})
        except (KeyError, TypeError, ValueError, OSError, JsonSchemaValidationError) as exc:
            render_report.append({"pair_id": pair_id, "pair_ref": pair_ref,
                "status": "invalid_unknown", "error_type": type(exc).__name__, "error": str(exc)})

    out = Path(output_dir).resolve()
    if out.exists() or out.is_symlink() or not out.is_relative_to(ROOT / ".lab-output"):
        raise FileExistsError("assembly output must be a new directory under .lab-output")
    out.mkdir(parents=True)
    try:
        shutil.copyfile(raw_path, out / "raw-indexed-responses.jsonl")
        write_json(out / "raw-snapshot-receipt.json", snapshot)
        write_rows(out / "missing-response-failures.jsonl", failures)
        rendered_responses = []
        for row in raw:
            ref = row["pair_ref"]
            pair_id = ref_by_number[ref]["pair_id"]
            try:
                rendered_responses.append(INDEXED.render_indexed_response(row, pairs[pair_id], payloads[pair_id], ref))
            except (KeyError, TypeError, ValueError, JsonSchemaValidationError):
                continue
        write_rows(out / "rendered-v3-responses.jsonl", rendered_responses)
        write_rows(out / "source-validation-report.jsonl", render_report)
        write_rows(out / "validated-pairs-for-eval.jsonl", validated_pairs)
        write_rows(out / "verified-source-citation-metadata.jsonl", source_citations)
        bindings = bundle.source["bindings"]
        tasks = bundle.source["cohort_rows"]["tasks.jsonl"]
        cases = bundle.source["cohort_rows"]["case-plans.jsonl"]
        rows = bundle.source["cohort_rows"]["row-plans.jsonl"]
        states = bundle.source["literal_states"]
        receipts = bundle.source["proof_rows"]["task-source-verification.jsonl"]
        gate_results = {}
        for variant in ("indexed-body-invariant-v3", "literal-only"):
            variant_pairs = validated_pairs if variant == "indexed-body-invariant-v3" else []
            resolutions = EVAL.build_choice_nli_task_resolutions(tasks, bindings, variant_pairs, states, receipts)
            internal = "choice_nli_full" if variant == "indexed-body-invariant-v3" else "baseline_literal_overlay"
            gate = EVAL.finish_whole_sku_gate(internal, cases, rows, tasks, resolutions)
            gate["variant"] = variant
            gate["assembly_method"] = ("indexed_model_semantics_plus_scoped_source_validation_and_literal_proofs"
                if variant == "indexed-body-invariant-v3" else "literal_source_proofs_only_unknown_elsewhere")
            gate["production_adoption_allowed"] = False
            if (len(resolutions) != len(tasks) or len(gate["rows"]) != len(rows)
                    or len(gate["decisions"]) != len(cases)):
                raise ValueError(f"whole-gate output coverage differs from source populations: {variant}")
            target = out / variant
            target.mkdir()
            write_rows(target / "task-resolutions.jsonl", resolutions)
            write_rows(target / "all-row-results.jsonl", gate["rows"])
            write_rows(target / "case-decisions.jsonl", gate["decisions"])
            gate_results[variant] = {"task_state_counts": gate["task_state_counts"],
                "row_state_counts": gate["row_state_counts"], "decision_counts": gate["decision_counts"]}
        counts = bundle.source["counts"]
        assembly_receipt = {"schema_version": "indexed-dev-whole-gate-assembly-v1",
            "status": "complete_both_whole_gate_variants_frozen",
            "dataset": bundle.source["cohort_manifest"]["dataset"],
            "selected_case_count": counts["selected_case_count"],
            "selected_product_count": counts["selected_product_count"],
            "row_count": counts["row_count"], "task_count": counts["task_count"],
            "prepared_pair_count": len(pairs), "dispatched_pair_count": len(partition_receipt_ids(preflight_dir)),
            "raw_response_count": len(raw), "missing_response_count": len(failures),
            "invalid_or_unvalidated_pair_count": len(render_report) - len(validated_pairs),
            "variants": gate_results, "labels_read": False, "scores_created": False,
            "independent_test": False, "production_adoption_allowed": False,
            "source_pins": source_pins(bundle),
            "raw_indexed_response_sha256": sha_file(raw_path),
            "snapshot_receipt_sha256": sha_file(snapshot_receipt)}
        write_json(out / "assembly-receipt.json", assembly_receipt)
        inventory = {"schema_version": "indexed-dev-assembly-inventory-v1",
            "files": {p.relative_to(out).as_posix(): pin(p) for p in sorted(out.rglob("*"))
                      if p.is_file() and p.name != "assembly-output-inventory.json"},
            "labels_read": False, "scores_created": False}
        write_json(out / "assembly-output-inventory.json", inventory)
        return assembly_receipt
    except Exception as exc:
        write_json(out / "assembly-failure.json", {"error_type": type(exc).__name__, "error": str(exc),
            "traceback": traceback.format_exc(), "labels_read": False, "scores_created": False})
        raise


def freeze(config_path: Path, preflight_dir: Path, assembly_dir: Path,
           qa_receipts: list[Path], output_path: Path) -> dict[str, Any]:
    """Freeze all gate, source, protocol, and QA artifacts before any label access."""
    bundle = Bundle(config_path)
    validate_partition_receipt(bundle, preflight_dir)
    assembly_dir = Path(assembly_dir).resolve()
    assembly_receipt = read_json(assembly_dir / "assembly-receipt.json")
    inventory = read_json(assembly_dir / "assembly-output-inventory.json")
    if (assembly_receipt.get("status") != "complete_both_whole_gate_variants_frozen"
            or assembly_receipt.get("labels_read") is not False
            or assembly_receipt.get("scores_created") is not False
            or inventory.get("schema_version") != "indexed-dev-assembly-inventory-v1"):
        raise ValueError("assembly is incomplete or not blind source-only output")
    files: dict[str, dict[str, Any]] = {}
    qa_paths: dict[str, str] = {}
    for group, directory in (("preflight", Path(preflight_dir).resolve()), ("assembly", assembly_dir)):
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"symlink in frozen {group} outputs: {path}")
            if path.is_file():
                files[f"{group}/{path.relative_to(directory).as_posix()}"] = pin(path)
    for i, receipt_path in enumerate(qa_receipts):
        path = Path(receipt_path).resolve()
        if path.is_symlink() or not path.is_file() or not path.is_relative_to(ROOT / ".lab-output"):
            raise ValueError("QA receipt must be a regular .lab-output file")
        qkey = f"qa/{i:03d}-{path.name}"
        files[qkey] = pin(path)
        qa_paths[qkey] = path.relative_to(ROOT).as_posix()
    target = Path(output_path).resolve()
    if target.exists() or target.is_symlink() or not target.is_relative_to(ROOT / ".lab-output"):
        raise FileExistsError("freeze output must be a new .lab-output JSON file")
    counts = bundle.source["counts"]
    receipt = {"schema_version": "indexed-next-body-dev-pregrade-freeze-v1",
        "status": "all_source_predictions_gates_code_and_qa_frozen_before_labels",
        "cohort_name": bundle.config.get("cohort_name"),
        "dataset": bundle.source["cohort_manifest"]["dataset"],
        "split": "dev", "independent_test": False,
        **counts,
        "prepared_pair_count": len(bundle.source["pair_by_id"]),
        "dispatch_pair_count": len(bundle.source["partition"]["dispatch_pair_ids"]),
        "literal_protected_pair_count": len(bundle.source["partition"]["protected_pair_ids"]),
        "source_code_pins": source_pins(bundle),
        "source_artifact_pins": source_artifact_pins(bundle),
        "frozen_output_files": files,
        "qa_receipt_paths": qa_paths,
        "assembly_receipt_sha256": sha_file(assembly_dir / "assembly-receipt.json"),
        "preflight_inventory_sha256": sha_file(Path(preflight_dir) / "preflight-inventory.json"),
        "grade_adapter": "empty archival version skip adapter; original scorer unchanged",
        "labels_read": False, "test_labels_read": False, "scores_created": False,
        "root_go_for_grading": False, "production_adoption_allowed": False}
    receipt["freeze_sha256"] = digest_canonical(receipt)
    write_json(target, receipt)
    return receipt


def cloned_selected_label_reader():
    """Clone the frozen selected-reference reader; only empty archive skip is adapted."""
    original_source = inspect.getsource(SCORE_ORIGINAL.load_token_reference_shards)
    old = '        if not selected:\n            raise ValueError(f"frozen subset has no cases for required split {split}")'
    new = '        if not selected:\n            continue'
    if original_source.count(old) != 1:
        raise RuntimeError("original frozen parser empty-split guard changed; refusing adaptation")
    namespace = dict(SCORE_ORIGINAL.__dict__)
    exec(compile(original_source.replace(old, new), SCORE_ORIGINAL.__file__, "exec"), namespace)
    reader_source = inspect.getsource(SCORE_ORIGINAL.read_label_archives)
    exec(compile(reader_source, SCORE_ORIGINAL.__file__, "exec"), namespace)
    return namespace["read_label_archives"]


def grade_after_go(root_go_path: Path, freeze_path: Path, config_path: Path,
                   assembly_dir: Path, output_path: Path,
                   archive_v2: Path | None = None, archive_v3: Path | None = None) -> dict[str, Any]:
    """Post-freeze score entry point. Do not invoke without exact root GO."""
    bundle = Bundle(config_path)
    go = read_json(root_go_path)
    frozen = read_json(freeze_path)
    if (go.get("root_go_for_indexed_dev_grading") is not True
            or go.get("freeze_receipt_sha256") != sha_file(freeze_path)
            or frozen.get("schema_version") != "indexed-next-body-dev-pregrade-freeze-v1"
            or frozen.get("status") != "all_source_predictions_gates_code_and_qa_frozen_before_labels"
            or frozen.get("source_code_pins") != source_pins(bundle)
            or frozen.get("source_artifact_pins") != source_artifact_pins(bundle)):
        raise PermissionError("grading requires exact freeze-bound root GO and unchanged source/code pins")
    expected_freeze_digest = frozen.get("freeze_sha256")
    hash_body = dict(frozen)
    hash_body.pop("freeze_sha256", None)
    if expected_freeze_digest != digest_canonical(hash_body):
        raise ValueError("freeze receipt self-digest mismatch")
    expected_files = frozen.get("frozen_output_files")
    if not isinstance(expected_files, dict) or not expected_files:
        raise ValueError("freeze has no full pre-grade output inventory")
    for key, expected in expected_files.items():
        if key.startswith("assembly/"):
            rel = key[len("assembly/"):]
            path = Path(assembly_dir).resolve() / rel
        elif key.startswith("preflight/"):
            rel = key[len("preflight/"):]
            path = bundle.preflight_output / rel
        elif key.startswith("qa/"):
            rel = frozen.get("qa_receipt_paths", {}).get(key)
            if not isinstance(rel, str):
                raise ValueError("freeze omits a QA receipt path")
            path = ROOT / rel
        else:
            continue
        if not path.is_file() or pin(path) != expected:
            raise ValueError(f"frozen pre-grade output changed: {key}")
    for key, rel in frozen.get("qa_receipt_paths", {}).items():
        path = ROOT / rel
        if not path.is_file() or pin(path) != expected_files.get(key):
            raise ValueError(f"frozen QA receipt changed: {key}")
    members = bundle.source["cohort_rows"]["membership.jsonl"]
    case_plans = unique_index(bundle.source["cohort_rows"]["case-plans.jsonl"], "case_id", "grading case plan")
    if set(case_plans) != {m["case_id"] for m in members}:
        raise ValueError("frozen case plans do not exactly cover selected DEV membership")
    score_membership = []
    for member in members:
        version = case_plans[member["case_id"]].get("source_version")
        if version not in {"v2", "v3"}:
            raise ValueError("selected DEV case lacks an exact source archive version")
        score_membership.append({**member, "source_version": version, "split": version})
    archive_v2 = Path(archive_v2) if archive_v2 else ROOT / SCORE_ORIGINAL.V2_LABEL_ARCHIVE
    archive_v3 = Path(archive_v3) if archive_v3 else ROOT / SCORE_ORIGINAL.V3_LABEL_ARCHIVE
    # This is the sole label-opening site and is guarded above by exact root GO.
    references = cloned_selected_label_reader()(archive_v2, archive_v3, score_membership,
                                                 bundle.prepared / "row-plans.jsonl")
    if set(references) != {m["case_id"] for m in score_membership}:
        raise ValueError("selected frozen DEV references do not exactly cover the membership")
    variants = {}
    for variant in ("indexed-body-invariant-v3", "literal-only"):
        variants[variant] = read_rows(Path(assembly_dir) / variant / "case-decisions.jsonl")
    results = {variant: SCORE_ORIGINAL.score_variant(score_membership, predictions, references)
               for variant, predictions in variants.items()}
    output = {"schema_version": "indexed-next-body-dev-postfreeze-grade-v1",
        "phase": "post-freeze-grade", "freeze_receipt_sha256": sha_file(freeze_path),
        "root_go_sha256": sha_file(root_go_path), "dataset": frozen["dataset"],
        "source_split": "dev", "independent_test": False,
        "selected_case_count": len(members), "selected_product_count": frozen["selected_product_count"],
        "labels_read_after_root_go": True, "gates_changed": False,
        "classifier_outputs_changed": False, "production_adoption_allowed": False,
        "references_used": len(references), "variants": results}
    out = Path(output_path).resolve()
    if out.exists() or not out.is_relative_to(ROOT / ".lab-output"):
        raise FileExistsError("grade output must be a new .lab-output JSON file")
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, output)
    return output


def partition_receipt_ids(preflight_dir: Path) -> list[str]:
    return [r["pair_id"] for r in read_rows(preflight_dir / "dispatch-pair-ids.jsonl")]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight")
    pre.add_argument("--config", type=Path, required=True)
    ass = sub.add_parser("assemble")
    for name in ("config", "raw-indexed", "snapshot-receipt", "failure-ledger", "preflight-dir", "output-dir"):
        ass.add_argument("--" + name, type=Path, required=True)
    frz = sub.add_parser("freeze", help="pin all blind source, responses, gates, code, and QA artifacts")
    frz.add_argument("--config", type=Path, required=True)
    frz.add_argument("--preflight-dir", type=Path, required=True)
    frz.add_argument("--assembly-dir", type=Path, required=True)
    frz.add_argument("--qa-receipt", type=Path, action="append", default=[])
    frz.add_argument("--output", type=Path, required=True)
    grd = sub.add_parser("grade", help="score only after freeze-bound root GO")
    grd.add_argument("--root-go", type=Path, required=True)
    grd.add_argument("--freeze", type=Path, required=True)
    grd.add_argument("--config", type=Path, required=True)
    grd.add_argument("--assembly-dir", type=Path, required=True)
    grd.add_argument("--output", type=Path, required=True)
    grd.add_argument("--v2-label-archive", type=Path)
    grd.add_argument("--v3-label-archive", type=Path)
    args = p.parse_args()
    if args.command == "preflight":
        bundle = Bundle(args.config)
        result = write_preflight(bundle)
        print(json.dumps({"preflight_status": result["status"],
            "dispatch_pair_count": result["dispatch_pair_count"],
            "literal_protected_pair_count": result["literal_protected_pair_count"],
            "labels_read": False, "inference_run": False}, sort_keys=True))
    elif args.command == "assemble":
        result = assemble(args.config, args.raw_indexed, args.snapshot_receipt,
                          args.failure_ledger, args.preflight_dir, args.output_dir)
        print(json.dumps({"assembly_status": result["status"],
            "variants": sorted(result["variants"]), "scores_created": False}, sort_keys=True))
    elif args.command == "freeze":
        result = freeze(args.config, args.preflight_dir, args.assembly_dir,
                        args.qa_receipt, args.output)
        print(json.dumps({"freeze_status": result["status"], "labels_read": False,
            "root_go_for_grading": False}, sort_keys=True))
    else:
        result = grade_after_go(args.root_go, args.freeze, args.config, args.assembly_dir,
                                args.output, args.v2_label_archive, args.v3_label_archive)
        print(json.dumps({"phase": result["phase"], "labels_read_after_root_go": True,
            "production_adoption_allowed": False}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
