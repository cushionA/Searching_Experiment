"""Build a pinned, source-only literal-family proof bundle for a frozen subset."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "claude_v9_gate"))
import sku_gate_sources as sources  # noqa: E402
import sku_literal_family_proof_v1 as proof  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def build(subset: Path, source_root: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    inputs = subset / "inputs"
    input_manifest = json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))
    if input_manifest.get("labels_read") is not False or input_manifest.get("predictions_read") is not False:
        raise ValueError("subset input manifest does not certify label/prediction isolation")
    expected = input_manifest.get("output_sha256") or {}
    names = ("tasks.jsonl", "source-cases.jsonl", "source-products.jsonl", "row-plans.jsonl",
             "selected-source-manifest.jsonl", "source-input-manifests.json", "manifest.json")
    pins = {}
    for name in names:
        path = inputs / name
        digest = _sha(path)
        if name != "manifest.json" and expected.get(name) != digest:
            raise ValueError(f"frozen subset input hash mismatch: {name}")
        pins[name] = {"sha256": digest, "bytes": path.stat().st_size}
    tasks = _rows(inputs / "tasks.jsonl")
    cases = _rows(inputs / "source-cases.jsonl")
    products = _rows(inputs / "source-products.jsonl")
    row_plans = _rows(inputs / "row-plans.jsonl")
    selected_sources = _rows(inputs / "selected-source-manifest.jsonl")
    source_input_manifest = json.loads((inputs / "source-input-manifests.json").read_text(encoding="utf-8"))
    source_manifest = source_root.parent / "inventory.json"
    source_index = source_root.parent / "source-index.json"
    for path in (source_manifest, source_index, HERE / "sku_literal_family_proof_v1.py",
                 HERE / "build_sku_literal_family_proofs_v1.py"):
        pins[str(path)] = {"sha256": _sha(path), "bytes": path.stat().st_size}
    store = sources.RawStore(source_root)
    proofs = proof.build_proofs(tasks, cases, products, store, source_input_manifest)
    statuses = Counter(item["status"] for item in proofs)
    reasons = Counter(item.get("reason") for item in proofs)
    output.mkdir(parents=True, exist_ok=False)
    proof_path = output / "proofs.jsonl"
    proof_path.write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in proofs),
                          encoding="utf-8")
    raw_receipts = {}
    for item in proofs:
        for span in (item.get("raw_span_receipts") or {}).values():
            for entry in span if isinstance(span, list) else [span]:
                if isinstance(entry, dict) and entry.get("raw_file") and entry.get("sha256"):
                    raw_receipts[entry["raw_file"]] = entry["sha256"]
    raw_receipts = {path: {"sha256": digest, "verified": store.sha(path) == digest}
                    for path, digest in sorted(raw_receipts.items())}
    manifest = {
        "schema_version": "sku-literal-family-proof-bundle-v1",
        "proof_version": proof.PROOF_VERSION,
        "purpose": "source-only exact literal support/conflict evidence; not a classifier output",
        "labels_read": False, "predictions_read": False, "model_inference_run": False,
        "old_gate_or_classifier_modified": False,
        "input_subset_schema": input_manifest.get("schema_version"),
        "input_subset_case_count": input_manifest.get("selected_case_count"),
        "task_count": len(tasks), "row_plan_count": len(row_plans),
        "selected_source_manifest_count": len(selected_sources),
        "paired_proof_record_count": len(proofs), "proof_status_counts": dict(statuses),
        "reason_counts": {str(k): v for k, v in reasons.items()},
        "complete_family_rule": "exact same ordered raw strings; no sorting, normalization, partial matching, core, residual, or semantic inference",
        "proof_scope": "one uniquely named axis and one exact selected value on each side; row/case/task and raw-source spans must bind",
        "unknown_is_not_conflict": True,
        "input_pins": pins,
        "used_raw_source_receipts": raw_receipts,
        "proofs_sha256": _sha(proof_path),
        "proofs_bytes": proof_path.stat().st_size,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subset", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.subset, args.source_root, args.output)
    print(json.dumps({"status": "complete", "case_count": result["input_subset_case_count"],
                      "proof_status_counts": result["proof_status_counts"],
                      "paired_proof_record_count": result["paired_proof_record_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
