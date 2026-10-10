"""Freeze and run the integrated source-only SKU gate on preserved real inputs."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import sku_integrated_gate_v1 as gate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_INPUT = ROOT / ".lab-output/sku-integrated-inputs-20261010-v1/inputs"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return [json.loads(line) for line in Path(path).open() if line.strip()]


def dump(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_rows(path, rows):
    with Path(path).open("x") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def code_paths():
    return [HERE / "sku_integrated_gate_v1.py", Path(__file__),
            HERE / "prepare_integrated_sku_inputs_v1.py"] + list((HERE / "claude_v9_gate").glob("*.py"))


def compact(result):
    """Keep the complete pool; repeated conflict details need not inflate the archive."""
    result = dict(result)
    rows = []
    for row in result["rows"]:
        if row["status"] == "conflict":
            row = {k: v for k, v in row.items() if k not in ("atom_results", "au_only_atoms", "derived_conflicts")}
        rows.append(row)
    result["rows"] = rows
    result.pop("gate_results", None)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    meta = json.loads((args.input / "manifest.json").read_text())
    for name, digest in meta["output_sha256"].items():
        if sha(args.input.parent / name) != digest:
            raise ValueError("Prepared input changed: " + name)
    args.output.mkdir(parents=True)
    frozen_code = {str(p.relative_to(ROOT)): sha(p) for p in code_paths()}
    freeze = {"at_utc": datetime.now(timezone.utc).isoformat(), "model_used": False,
              "labels_opened": False, "development_inputs": True,
              "contract": "one selected priced Rakuten SKU against all rows of one fixed AU URL; accept/drop",
              "rules": {"all_other_rows_must_have_verified_conflict": True,
                        "no_url_rerouting": True, "page_spec_differences_are_notices": True,
                        "bounded_contents_absence": True},
              "input_sha256": {n: sha(args.input / n) for n in ("cases.jsonl", "products.jsonl", "manifest.json")},
              "code_sha256": frozen_code}
    dump(args.output / "freeze.json", freeze)
    modules = gate.load_gate()
    store = modules.src.RawStore(ROOT)
    contexts = {p["dossier_id"]: p for p in read(args.input / "products.jsonl")}
    facts = {k: modules.PairFacts(p) for k, p in contexts.items()}
    evaluators = {k: modules.make_evaluator("A", f, modules.PRIMARY_CONFIG) for k, f in facts.items()}
    cases = read(args.input / "cases.jsonl")
    predictions = []
    t0 = time.perf_counter()
    for case in cases:
        key = case["dossier_id"]
        result = gate.predict_case(case, facts[key], evaluator=evaluators[key], store=store)
        expected = {r["row_key"] for r in facts[key].rows}
        if {r["row_key"] for r in result["rows"]} != expected:
            raise ValueError("AU pool lost a row: " + case["case_id"])
        predictions.append(compact(result))
    elapsed = time.perf_counter() - t0
    for path, digest in frozen_code.items():
        if sha(ROOT / path) != digest:
            raise ValueError("Code changed during prediction: " + path)
    write_rows(args.output / "predictions.jsonl", predictions)
    by_id = {r["case_id"]: r for r in predictions}
    groups = {}
    for name, bundle in meta["bundle_inputs"].items():
        rows = [by_id[c] for c in bundle["case_ids"]]
        groups[name] = {"cases": len(rows), "counts": dict(Counter(r["decision"] for r in rows)),
                        "reasons": dict(Counter(r["reason"] for r in rows)),
                        "notice_accepts": sum(r["decision"] == "accept" and bool(r.get("notices")) for r in rows)}
    dump(args.output / "summary.json", {"groups": groups, "elapsed_seconds": elapsed,
        "case_count": len(cases), "all_au_row_references": sum(len(p["rows"]) for p in predictions),
        "accuracy_claim": False, "labels_opened": False, "raw_source_sha256": store._sha})
    dump(args.output / "manifest.json", {"files": {p.name: sha(p) for p in args.output.iterdir() if p.is_file()}})
    print(json.dumps(groups, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
