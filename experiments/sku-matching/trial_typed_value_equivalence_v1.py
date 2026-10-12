#!/usr/bin/env python3
"""Verify frozen typed sidecars, then compare the unchanged mixed-row gate.

Verified raw SKU equalities can replace a non-supporting prediction. Actual
model scores are preserved as source_model_prediction and never relabeled.
Every request binding must pass before replacement. References are opened only
after all predictions and same-row gates have been frozen.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import evaluate_mixed_gpu_sku_v1 as E
import evaluate_keyword_filtered_product_v1 as REFERENCES
import trial_keyword_filtered_product_v1 as VERIFY_MODEL
import verify_typed_value_equivalence_v1 as VERIFY

ROOT = Path(__file__).resolve().parents[2]
PREP = ROOT / ".lab-output/sku-mixed-gpu-prep-20261012-v5"
CPU = ROOT / ".lab-output/sku-mixed-cpu-run-20261012-v1"
CURRENT = ROOT / ".lab-output/sku-baseline-ablation-20261012-v2"
INCOMING = ROOT / ".lab-output/sku-claude-value-equivalence-incoming-20261012-v1"
OLD_EVAL = ROOT / ".lab-output/sku-mixed-cpu-evaluation-20261012-v4"
ROUTES = ROOT / ".lab-output/sku-mixed-cpu-reference-routes-20261012-v2"
DEFAULT_OUT = ROOT / ".lab-output/sku-typed-value-equivalence-20261012-v1"
ARCHIVES = {
    ROOT / ".lab-output/sku-claude-v7-integration-review-20261012-v1/20261012-sku-align-v6-v7-outputs.zip":
        "f5522952c8a67d5a7289cdb6f9cdcb5f2d093026a79360b704c08fd68dd5503c",
    ROOT / ".lab-output/sku-claude-dev-input-share-20261012-v1/20261012-sku-independent-real-eval-v2-v3-token-inputs.zip":
        "db2815a3d5c726e45f996ad07e73cdcf1cdff0610de53ad84af8a4a0a880488f",
}


def read_json(p): return json.loads(Path(p).read_text(encoding="utf-8"))
def rows(p): return list(E.rows(Path(p)))
def sha_body(b): return hashlib.sha256(b).hexdigest()
def normalized(p): return str(p).replace("\\", "/")


class BundleStore(VERIFY.sources.RawStore):
    def __init__(self, prepared):
        super().__init__(prepared)
        self.entries = {}
        for entry in rows(prepared / "raw-source-registry.jsonl"):
            for alias in entry["original_aliases"] + [entry["raw_file"]]:
                key = normalized(alias)
                previous = self.entries.setdefault(key, entry)
                if previous != entry: raise ValueError("conflicting raw capture alias")

    def raw(self, rel, expected_sha256=None):
        key = normalized(rel)
        entry = self.entries[key]
        path = Path(entry["bundle_file"])
        if path.is_absolute() or ".." in path.parts: raise ValueError("unsafe raw capture path")
        body = (self.root / path).read_bytes()
        digest = sha_body(body)
        if digest != entry["sha256"] or len(body) != entry["bytes"]:
            raise ValueError("frozen raw capture differs")
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError("raw source SHA differs from proof")
        self._bytes[key], self._sha[key] = body, digest
        return body

    def sha(self, rel):
        self.raw(rel)
        return self._sha[normalized(rel)]


def run(output_dir):
    output_dir = Path(output_dir).resolve()
    if output_dir.exists(): raise FileExistsError(output_dir)
    manifest, requests, documents, tasks, cases, membership, hosts = E.load_mixed_prepared(PREP)
    cpu_summary, _ = E.verify_cpu_run(PREP, CPU, manifest, requests)
    _, _, eligible = E.check_prediction_coverage(PREP, CPU, requests, documents, tasks, cpu_summary)
    VERIFY_MODEL.verify_source(CPU / "product-model")
    for name, want in manifest["output_sha256"].items(): assert E.sha(PREP / name) == want, name
    current_summary = read_json(CURRENT / "summary.json")
    for name, want in current_summary["output_sha256"].items(): assert E.sha(CURRENT / name) == want, name
    original = E.GATE.indexed(CURRENT / "forward-nli-reverse-jev/predictions.jsonl", "request_id")
    assert set(original) == set(requests)
    E.GATE.validate_predictions(requests, documents, tasks, original, original, "baseline")
    source_cases = E.GATE.indexed(PREP / "frozen-source-cases.jsonl", "case_id")
    source_products = E.GATE.indexed(PREP / "frozen-source-products.jsonl", "dossier_id")
    incoming = read_json(INCOMING / "20261012-sku-value-equivalence-v1.manifest.json")
    validation = read_json(INCOMING / "validation.json")
    assert validation["archive_sha256"] == incoming["sha256"] == E.sha(INCOMING / incoming["archive"])
    assert validation["all_crc_byte_sha_checks_passed"] is True
    for name, want in validation["members"].items(): assert E.sha(INCOMING / "extracted" / name) == want
    frozen_files = [PREP / "manifest.json", *(PREP / name for name in manifest["output_sha256"]),
                    *(PREP / name for name in manifest.get("raw_capture_output_sha256", {})),
                    CPU / "summary.json", CPU / "cpu-source-freeze.json", CPU / "execution-freeze.json",
                    CURRENT / "summary.json", CURRENT / "forward-nli-reverse-jev/predictions.jsonl",
                    CURRENT / "forward-nli-reverse-jev/gates/decisions.jsonl", INCOMING / "validation.json",
                    INCOMING / "20261012-sku-value-equivalence-v1.manifest.json", INCOMING / incoming["archive"],
                    Path(__file__), Path(VERIFY.__file__), Path(VERIFY.sources.__file__), Path(E.__file__),
                    Path(E.GATE.__file__), ROOT / "experiments/sku-matching/integrate_claude_description_v1.py",
                    Path(REFERENCES.__file__), Path(VERIFY_MODEL.__file__)]
    frozen_files.extend(INCOMING / "extracted" / name for name in validation["members"])
    archives = {}
    for path, want in ARCHIVES.items():
        assert E.sha(path) == want
        archives[path] = zipfile.ZipFile(path)
        frozen_files.append(path)
    blob_cache, blob_receipts = {}, []

    def blob(spec):
        name, want = normalized(spec["path"]), spec["sha256"]
        if name in blob_cache:
            assert sha_body(blob_cache[name]) == want
            return blob_cache[name]
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts: raise ValueError("unsafe input member")
        local = ROOT / rel
        if local.is_file() and E.sha(local) == want:
            body = local.read_bytes()
            origin = {"path": str(local)}
            frozen_files.append(local)
        else:
            found = [(p, z.read(name)) for p, z in archives.items() if name in z.namelist()]
            if not found: raise FileNotFoundError("pinned proof input missing: " + name)
            if any(sha_body(b) != want for _, b in found): raise ValueError("conflicting proof input member")
            path, body = found[0]
            origin = {"archive": str(path), "archive_sha256": ARCHIVES[path], "member": name}
        assert sha_body(body) == want
        blob_cache[name] = body
        blob_receipts.append({"source": origin, "sha256": want})
        return body

    by_case_file = {}
    for run_info in incoming["runs"]:
        if run_info["name"].startswith("eval-"): assert run_info["splits"] == ["dev"]
        sidecar_path = INCOMING / "extracted" / next(f["path"] for f in run_info["files"] if f["path"].endswith("/sidecars.jsonl"))
        sidecars = {}
        for value in rows(sidecar_path):
            key = value["case_id"], value["au_row_key"], value["axis_key"]
            if key in sidecars: raise ValueError("duplicate sidecar source key")
            sidecars[key] = value
        key = normalized(run_info["inputs"]["cases.jsonl"]["path"])
        if key in by_case_file: raise ValueError("duplicate proof run input")
        by_case_file[key] = run_info, sidecars

    store, receipts, audit, request_receipts = BundleStore(PREP), [], [], {}
    for task in tasks.values():
        ref = task["source_ref"]
        case_ref = ref.get("input_case_source")
        if not isinstance(case_ref, dict) or "member" not in case_ref:
            audit.append({"task_id": task["task_id"], "request_id": task["request_id"],
                          "case_id": task["case_id"], "accepted": False,
                          "reason": "no_complete_typed_sidecar_source_route"})
            continue
        source_key = normalized(case_ref["member"])
        found = by_case_file.get(source_key)
        if found is None: continue
        run_info, sidecars = found
        sidecar = sidecars.get((task["case_id"], task["au_row_key"], task["axis_key"]))
        if sidecar is None: continue
        detail = {"task_id": task["task_id"], "request_id": task["request_id"], "case_id": task["case_id"],
                  "proof_run": run_info["name"], "sidecar_verdict": sidecar["verdict"]}
        if sidecar["verdict"] != "proven_equivalent":
            audit.append({**detail, "accepted": False, "reason": "sidecar_remains_model_candidate"})
            continue
        specs = run_info["inputs"]
        assert specs["cases.jsonl"]["sha256"] == case_ref["member_sha256"]
        case_line = blob(specs["cases.jsonl"]).splitlines()[case_ref["line"] - 1]
        assert sha_body(case_line) == case_ref["sha256"]
        assert json.loads(case_line) == source_cases[task["case_id"]]
        product_ref = ref["input_product_source"]
        assert normalized(product_ref["path"]).endswith(normalized(specs["products.jsonl"]["path"]))
        product_line = blob(specs["products.jsonl"]).splitlines()[product_ref["line"] - 1]
        assert sha_body(product_line) == product_ref["line_sha256"]
        assert json.loads(product_line) == source_products[task["dossier_id"]]
        handoff_body = blob(specs["handoff.jsonl"])
        # Parse only the declared selected case/row. Other dossiers stay opaque.
        selected = [json.loads(line) for line in handoff_body.splitlines()
                    if task["case_id"].encode() in line and task["au_row_key"].encode() in line]
        selected = [r for r in selected if r["case_id"] == task["case_id"] and r["au_row_key"] == task["au_row_key"]]
        assert len(selected) == 1 and selected[0] == task["source_handoff"]
        align_summary = json.loads(blob(specs["align_summary.json"]))
        assert align_summary["code_sha256"] == run_info["align_code_sha256"]
        if "groups.jsonl" in specs:
            group_ref = ref["input_group_source"]
            assert specs["groups.jsonl"]["sha256"] == group_ref["sha256"]
            groups = [json.loads(line) for line in blob(specs["groups.jsonl"]).splitlines()
                      if task["dossier_id"].encode() in line]
            groups = [g for g in groups if g["dossier_id"] == task["dossier_id"]]
            assert len(groups) == 1 and groups[0] == group_ref["group"] and groups[0]["split"] == "dev"
        try:
            receipt = VERIFY.validate_sidecar(sidecar, task, requests[task["request_id"]],
                source_cases[task["case_id"]], source_products[task["dossier_id"]], store)
        except ValueError as exc:
            audit.append({**detail, "accepted": False, "reason": str(exc)})
            continue
        receipt.update(task_id=task["task_id"], request_id=task["request_id"],
                       sidecar_sha256=sha_body(json.dumps(sidecar, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()),
                       source_run=run_info["name"], pinned_source_inputs=specs)
        receipts.append(receipt)
        request_receipts.setdefault(task["request_id"], {})[task["task_id"]] = receipt
        audit.append({**detail, "accepted": True, "reason": "verified_whole_raw_axis_affix_and_complete_families"})
    for archive in archives.values(): archive.close()
    output_dir.mkdir(parents=True, exist_ok=False)
    code_dir = output_dir / "code"
    code_dir.mkdir()
    for path in (Path(__file__), Path(VERIFY.__file__), Path(VERIFY.sources.__file__)):
        shutil.copyfile(path, code_dir / path.name)
        frozen_files.append(code_dir / path.name)
    E.write_rows(output_dir / "verified-proof-receipts.jsonl", receipts)
    E.write_rows(output_dir / "proof-audit.jsonl", audit)
    E.write_rows(output_dir / "source-blob-receipts.jsonl", blob_receipts)
    predicted, changes = deepcopy(original), []
    for rid, verified in request_receipts.items():
        bindings = original[rid]["source_task_bindings"]
        if set(verified) != {b["task_id"] for b in bindings}: continue
        if original[rid]["state"] == "support": continue
        assert rid not in eligible
        request = requests[rid]
        text = request["target_selected_axes_text"]
        quotes = [{"document_id": request["target_document_id"], "document_sha256": request["target_document_sha256"],
                   "field": "selected_axes", "start": 0, "end": len(text), "quote": text}]
        assert text and all(E.GATE.verify_quote(q, request, documents) for q in quotes)
        predicted[rid] = {"request_id": rid, "request_sha256": request["request_sha256"],
                          "selected_value": request["selected_value"], "direction": request["direction"],
                          "source_request": deepcopy(request), "source_task_bindings": deepcopy(bindings),
                          "state": "support", "winning_value": request["selected_value"], "quote_refs": quotes,
                          "reason": "verified_whole_raw_option_axis_affix", "variant": "typed-value-equivalence-v1",
                          "scope_status": "unproved", "production_adoption_allowed": False,
                          "verified_value_equivalence": list(verified.values()), "source_model_prediction": deepcopy(original[rid])}
        changes.append({"request_id": rid, "before_state": original[rid]["state"], "after_state": "support",
                        "task_ids": sorted(verified), "actual_model_scores_changed": False})
    E.GATE.validate_predictions(requests, documents, tasks, predicted, original, "baseline")
    assert all(predicted[rid] == original[rid] for rid in set(requests) - {r["request_id"] for r in changes})
    E.write_rows(output_dir / "predictions.jsonl", predicted.values())
    E.write_rows(output_dir / "request-changes.jsonl", changes)
    frozen_files.extend(output_dir / name for name in ("verified-proof-receipts.jsonl", "proof-audit.jsonl",
                         "source-blob-receipts.jsonl", "predictions.jsonl", "request-changes.jsonl"))
    snapshot = VERIFY_MODEL.snapshot_paths({str(p): p for p in frozen_files})
    E.write_json(output_dir / "execution-freeze.json", {"schema_version": "typed-value-equivalence-inputs-v1",
        "experiment_status": "exploratory_dev_informed", "labels_read": False, "independent_test": False,
        "new_model_inference": False, "production_adoption_allowed": False, "source_snapshot": snapshot,
        "changed_request_count": len(changes), "verified_receipt_count": len(receipts),
        "case_count": 83, "request_count": 134, "original_model_scores_unchanged": True,
        "acceptance": "raw whole exact own-axis-name affix; complete literal families bijective; no residual; all request bindings verified",
        "legacy_gate_reason_note": "gate uses its existing experimental_quoted_model_support name for the support carrier; prediction provenance identifies verified raw SKU proofs"})
    E.GATE.finish(PREP, output_dir / "predictions.jsonl", output_dir / "gates")
    gates = E.GATE.indexed(output_dir / "gates/decisions.jsonl", "case_id")
    assert set(gates) == set(cases)
    gate_snapshot = VERIFY_MODEL.snapshot_paths({str(p): p for p in (output_dir / "gates").rglob("*") if p.is_file()})
    E.write_json(output_dir / "all-gates-frozen.json", {"labels_read": False, "all_gates_frozen": True,
                 "case_count": 83, "request_count": 134, "gate_snapshot": gate_snapshot})
    _, _, _, references, _ = REFERENCES.verify_original_sources(PREP, OLD_EVAL, ROUTES)
    score_ids = {r["case_id"] for r in E.eligibility_rows(membership, hosts)[0] if r["primary_score_eligible"]}
    scored = {k: v for k, v in references.items() if k in score_ids and not v.get("reference_uncertain", False)}
    assert len(scored) == 73
    old_gates = E.GATE.indexed(CURRENT / "forward-nli-reverse-jev/gates/decisions.jsonl", "case_id")
    metrics, details = {}, []
    for stratum in E.STRATA:
        value = E.metrics_for(gates, {k: v for k, v in scored.items() if membership[k]["membership_strata"] == [stratum]})
        metrics[stratum] = {k: v for k, v in value.items() if k != "case_results"}
        details.extend({"stratum": stratum, **r} for r in value["case_results"])
    value = E.metrics_for(gates, scored)
    metrics["scored_primary_reference_union"] = {k: v for k, v in value.items() if k != "case_results"}
    before = {r["case_id"]: r for r in E.metrics_for(old_gates, scored)["case_results"]}
    case_changes = [{"case_id": r["case_id"], "before": before[r["case_id"]], "after": r}
                    for r in value["case_results"] if r != before[r["case_id"]]]
    E.write_rows(output_dir / "case-results.jsonl", details)
    E.write_rows(output_dir / "changes-vs-current-candidate.jsonl", case_changes)
    VERIFY_MODEL.verify_snapshot(snapshot, phase="after proof integration and scoring")
    VERIFY_MODEL.verify_snapshot(gate_snapshot, phase="after proof scoring")
    E.verify_cpu_run(PREP, CPU, manifest, requests)
    E.write_json(output_dir / "summary.json", {"schema_version": "typed-value-equivalence-result-v1",
        "experiment_status": "exploratory_dev_informed", "independent_test": False, "production_adoption_allowed": False,
        "new_model_inference": False, "labels_read_after_all_gates_frozen": True, "reference_kind": "existing machine labels",
        "source_and_output_hashes_verified_after_scoring": True, "verified_receipt_count": len(receipts),
        "changed_request_count": len(changes), "scored_case_count": 73, "all_case_count": 83,
        "metrics": metrics, "changed_scored_case_count": len(case_changes),
        "output_sha256": {str(p.relative_to(output_dir)): E.sha(p) for p in output_dir.rglob("*") if p.is_file()}})
    print(json.dumps({"metrics": metrics["scored_primary_reference_union"], "changed_requests": changes,
                      "changed_cases": [r["case_id"] for r in case_changes]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    run(**vars(parser.parse_args()))
