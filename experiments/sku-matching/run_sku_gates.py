"""Build, freeze, and run the fixed-pair SKU gates (methods A and B).

Steps (each refuses to overwrite existing outputs):
  prepare  build product contexts and case inputs from the frozen checkpoint
  freeze   hash task code, schemas, tests, and inputs before any label is read
  predict  verify the freeze, then write A/B predictions for every source config

Labels are never opened here. Evaluation lives in evaluate_sku_gates.py and
checks this freeze first.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

import jsonschema

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sku_gate_sources as src  # noqa: E402
import sku_gates as gates  # noqa: E402

ROOT = HERE.parents[1]
ANNOTATION = ".lab-output/sku-real-luna-annotation-inputs-20261010-v3"
ARRAYS = ".lab-output/sku-observed-product-pairs-20261010-final-v4/au_product_sku_arrays.jsonl"
DEFAULT_OUT = ".lab-output/sku-gate-tasks-20261010-v1"
CODE_FILES = ["experiments/sku-matching/sku_gate_sources.py", "experiments/sku-matching/sku_gate_atoms.py",
              "experiments/sku-matching/sku_gates.py", "experiments/sku-matching/run_sku_gates.py",
              "tests/test_lab_sku_gates.py"]
SCHEMA_FILES = ["experiments/sku-matching/schemas/sku_gate_product_context.schema.json",
                "experiments/sku-matching/schemas/sku_gate_case_input.schema.json",
                "experiments/sku-matching/schemas/sku_gate_a_output.schema.json",
                "experiments/sku-matching/schemas/sku_gate_b_output.schema.json"]
LABEL_PATHS = (".lab-output/sku-real-luna-labels-20261010-v3",)


def schema(name: str):
    path = HERE / "schemas" / name
    return jsonschema.Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


def write_new(path: Path, text: str):
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_jsonl_new(path: Path, rows):
    write_new(path, "".join(gates.canonical_json(r) + "\n" for r in rows))


def _assert_no_labels(paths):
    for p in paths:
        if any(str(p).startswith(label) for label in LABEL_PATHS):
            raise RuntimeError(f"Label path used before freeze: {p}")


def prepare(out: Path):
    store = src.RawStore(ROOT)
    inputs = ROOT / ANNOTATION
    cases = src.read_jsonl(inputs / "cases.jsonl")
    arrays_index = {}
    for i, line in enumerate((ROOT / ARRAYS).read_text(encoding="utf-8").splitlines()):
        arrays_index.setdefault(json.loads(line)["au_product_id"], i + 1)
    contexts, case_inputs = {}, []
    for case in cases:
        if case["dossier_id"] not in contexts:
            dossier = json.loads((inputs / "dossiers" / f"{case['dossier_id']}.json").read_text(encoding="utf-8"))
            contexts[case["dossier_id"]] = gates.build_product_context(store, dossier, case, ARRAYS, arrays_index)
        case_inputs.append(gates.build_case_input(store, case, contexts[case["dossier_id"]]))
    ctx_validator, case_validator = schema("sku_gate_product_context.schema.json"), schema("sku_gate_case_input.schema.json")
    for ctx in contexts.values():
        ctx_validator.validate(ctx)
    for ci in case_inputs:
        case_validator.validate(ci)
    read_files = sorted(set(store._sha) | {f"{ANNOTATION}/cases.jsonl", ARRAYS} |
                        {f"{ANNOTATION}/dossiers/{d}.json" for d in contexts})
    _assert_no_labels(read_files)
    write_jsonl_new(out / "inputs" / "products.jsonl", [contexts[k] for k in sorted(contexts)])
    write_jsonl_new(out / "inputs" / "cases.jsonl", case_inputs)
    manifest = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "task_version": gates.TASK_VERSION,
                "labels_read": False, "case_count": len(case_inputs), "product_pairs": len(contexts),
                "au_rows": sum(len(c["au_rows"]) for c in contexts.values()),
                "source_sha256": {p: src.sha256_file(ROOT / p) for p in read_files},
                "outputs_sha256": {f"inputs/{n}": src.sha256_file(out / "inputs" / n) for n in ("products.jsonl", "cases.jsonl")},
                "excluded_non_identity_fields": list(gates.NON_IDENTITY_FIELDS)}
    write_new(out / "inputs" / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: manifest[k] for k in ("case_count", "product_pairs", "au_rows")}))


def _git(*args):
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def frozen_hashes(out: Path) -> dict:
    files = CODE_FILES + SCHEMA_FILES
    hashes = {p: src.sha256_file(ROOT / p) for p in files}
    for name in ("products.jsonl", "cases.jsonl", "manifest.json"):
        hashes[f"{out.relative_to(ROOT)}/inputs/{name}"] = src.sha256_file(out / "inputs" / name)
    return hashes


def freeze(out: Path, note: str):
    record = {"frozen_at_utc": datetime.now(timezone.utc).isoformat(), "task_version": gates.TASK_VERSION,
              "labels_read_before_freeze": False, "note": note,
              "git_head": _git("rev-parse", "HEAD"), "git_status_porcelain": _git("status", "--porcelain"),
              "python": platform.python_version(), "jsonschema": jsonschema.__version__ if hasattr(jsonschema, "__version__") else None,
              "methods": ["A", "B"], "source_configs": gates.SOURCE_CONFIGS, "primary_config": gates.PRIMARY_CONFIG,
              "sha256": frozen_hashes(out)}
    write_new(out / "freeze.json", json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(src.sha256_file(out / "freeze.json"))


def check_freeze(out: Path) -> dict:
    record = json.loads((out / "freeze.json").read_text(encoding="utf-8"))
    current = frozen_hashes(out)
    changed = sorted(k for k in record["sha256"] if record["sha256"][k] != current.get(k))
    if changed:
        raise RuntimeError(f"Frozen files changed: {changed}")
    return record


def predict(out: Path):
    check_freeze(out)
    contexts = {c["dossier_id"]: c for c in src.read_jsonl(out / "inputs" / "products.jsonl")}
    case_inputs = src.read_jsonl(out / "inputs" / "cases.jsonl")
    facts = {k: gates.PairFacts(v) for k, v in contexts.items()}
    validators = {"A": schema("sku_gate_a_output.schema.json"), "B": schema("sku_gate_b_output.schema.json")}
    manifest = {"started_at_utc": datetime.now(timezone.utc).isoformat(), "labels_read": False,
                "freeze_sha256": src.sha256_file(out / "freeze.json"), "files": {}}
    for config in gates.SOURCE_CONFIGS:
        for method in ("A", "B"):
            t0 = time.time()
            evaluators, rows = {}, []
            for ci in case_inputs:
                key = ci["dossier_id"]
                if key not in evaluators:
                    evaluators[key] = gates.make_evaluator(method, facts[key], config)
                result = gates.run_method(method, ci, facts[key], config, evaluators[key])
                validators[method].validate(result)
                if config != gates.PRIMARY_CONFIG:
                    result = {k: result[k] for k in ("schema_version", "task_version", "method", "source_config", "case_id",
                                                     "dossier_id", "au_product_id", "decision", "top_row_key", "reason",
                                                     "candidate_row_keys", "row_status_counts")}
                rows.append(result)
            name = f"predictions/{method}-{config}.jsonl"
            write_jsonl_new(out / name, rows)
            counts = {}
            for r in rows:
                counts[r["decision"]] = counts.get(r["decision"], 0) + 1
            manifest["files"][name] = {"sha256": src.sha256_file(out / name), "rows": len(rows), "decisions": counts,
                                       "seconds": round(time.time() - t0, 2), "full_rows": config == gates.PRIMARY_CONFIG}
            print(name, counts, flush=True)
    manifest["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_new(out / "predictions" / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=["prepare", "freeze", "predict"])
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    out = ROOT / args.out
    if args.step == "prepare":
        prepare(out)
    elif args.step == "freeze":
        freeze(out, args.note)
    else:
        predict(out)


if __name__ == "__main__":
    main()
