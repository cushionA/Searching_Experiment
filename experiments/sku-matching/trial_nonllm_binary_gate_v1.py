"""Freeze and replay source-only binary SKU improvements; no inference/labels."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import zipfile

import sku_nonllm_gate_v1 as gate

ROOT = Path(__file__).resolve().parents[2]


def read(data):
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)


def dump(path, value):
    write_new(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def jsonlines(rows):
    return ("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows)).encode()


def load_inputs(novel_dir, old_zip):
    novel = {name: (novel_dir / name).read_bytes() for name in ("cases.jsonl", "products.jsonl")}
    with zipfile.ZipFile(old_zip) as archive:
        prefix = ".lab-output/sku-gate-tasks-20261010-v2/inputs/"
        legacy = {name: archive.read(prefix + name) for name in novel}
    return {"novel": novel, "legacy": legacy}


def run_bundle(raw, gates, store, restore):
    contexts = read(raw["products.jsonl"])
    cases = read(raw["cases.jsonl"])
    facts = {}
    restored_count = 0
    for context in contexts:
        if restore:
            context["au_description_lines"] = gate.restore_au_lines(context, store, gates)
            restored_count += len(context["au_description_lines"])
        facts[context["dossier_id"]] = gates.PairFacts(context)
    start = time.perf_counter()
    predictions = [gate.strict_case(case, facts[case["dossier_id"]], gates, store) for case in cases]
    return predictions, {"cases": len(cases), "products": len(contexts),
                         "decisions": dict(Counter(r["decision"] for r in predictions)),
                         "reasons": dict(Counter(r["reason"] for r in predictions)),
                         "all_row_references": sum(len(r["full_au_row_keys"]) for r in predictions),
                         "restored_au_description_lines": restored_count,
                         "elapsed_seconds": time.perf_counter() - start,
                         "by_product": {ctx["au_product"]["product_id"]: dict(Counter(
                             p["decision"] for p in predictions if p["dossier_id"] == ctx["dossier_id"]))
                                        for ctx in contexts}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".lab-output/sku-nonllm-binary-gate-20261010-v1")
    parser.add_argument("--novel-dir", type=Path, default=ROOT / ".lab-output/sku-novel-cpu-gate-20261010-v2")
    parser.add_argument("--legacy-zip", type=Path, default=gate.DEFAULT_GATE_CODE / "results/20261010-sku-gate-tasks.zip")
    args = parser.parse_args()
    # Shared Claude code is experiments/sku-matching; archive is at repo results/.
    if not args.legacy_zip.exists():
        args.legacy_zip = gate.DEFAULT_GATE_CODE.parents[1] / "results/20261010-sku-gate-tasks.zip"
    if args.output.exists():
        raise FileExistsError(args.output)
    bundles = load_inputs(args.novel_dir, args.legacy_zip)
    gates = gate.load_gate(extensions=False)
    store = gates.src.RawStore(ROOT)
    code = [Path(__file__), Path(gate.__file__), Path(gate.extension.__file__)] + [
        gate.DEFAULT_GATE_CODE / n for n in ("sku_gates.py", "sku_gate_atoms.py", "sku_gate_sources.py")]
    args.output.mkdir(parents=True)
    freeze = {"frozen_at_utc": datetime.now(timezone.utc).isoformat(), "labels_read": False,
              "synthetic_inputs": False, "model_used": False, "network_used": False,
              "inputs": {name: {file: sha(data) for file, data in raw.items()} for name, raw in bundles.items()},
              "code": {str(p): sha(p.read_bytes()) for p in code},
              "contract": "one selected Rakuten SKU vs complete fixed-URL AU pool; accept/drop; unknown auto-drop",
              "settings": {"all_other_rows_must_conflict": True, "au_only_conditions_mandatory": True,
                           "literal_verified_evidence_required": True, "negative_from_missing_word": False}}
    dump(args.output / "freeze.json", freeze)
    for path in code:
        namespace = "claude-gate" if path.parent == gate.DEFAULT_GATE_CODE else "runner"
        write_new(args.output / "code_snapshot" / namespace / path.name, path.read_bytes())
    for name, raw in bundles.items():
        for file, data in raw.items():
            write_new(args.output / "input" / name / file, data)
    results, summaries = {}, {}
    for name, raw in bundles.items():
        predictions, summary = run_bundle(raw, gates, store, restore=False)
        results[name] = predictions
        summaries[name] = {"baseline_strict": summary}
        write_new(args.output / "predictions" / (name + "-baseline.jsonl"), jsonlines(predictions))
    gates = gate.load_gate()
    for name, raw in bundles.items():
        predictions, summary = run_bundle(raw, gates, store, restore=True)
        summaries[name]["improved"] = summary
        before = {p["case_id"]: p for p in results[name]}
        summaries[name]["transitions"] = dict(Counter(before[p["case_id"]]["decision"] + "->" + p["decision"] for p in predictions))
        changes = [{"case_id": p["case_id"], "au_product_id": p["au_product_id"],
                    "before": before[p["case_id"]]["decision"], "after": p["decision"], "au_row_key": p["au_row_key"]}
                   for p in predictions if p["decision"] != before[p["case_id"]]["decision"] or
                   p["au_row_key"] != before[p["case_id"]]["au_row_key"]]
        write_new(args.output / "predictions" / (name + "-improved.jsonl"), jsonlines(predictions))
        write_new(args.output / "changes" / (name + ".jsonl"), jsonlines(changes))
    dump(args.output / "summary.json", {"labels_read": False, "accuracy_claim": False, "results": summaries,
                                       "raw_source_hashes": store._sha})
    inventory = {str(p.relative_to(args.output)): sha(p.read_bytes()) for p in args.output.rglob("*") if p.is_file()}
    dump(args.output / "manifest.json", {"files": inventory, "labels_read": False, "accuracy_claim": False})
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
