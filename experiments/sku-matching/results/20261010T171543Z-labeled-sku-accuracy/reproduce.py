#!/usr/bin/env python3
"""Read immutable checkpoints and grade in a new directory outside the checkout."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
EXPERIMENT = HERE.parents[1]
ROOT = EXPERIMENT.parents[1]
LUNA = EXPERIMENT / "results/20261010T160225Z-luna-task-improvement"
GEMINI = EXPERIMENT / "results/20261010T154257Z-gemini-structured-values-vertex"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalized(path, mode):
    value = read(path)
    cases = value["cases"] if isinstance(value, dict) else value
    if mode == "final":
        return cases
    if mode == "candidate":
        return [{"case_id": c["case_id"],
                 "decision": "adopt" if c["decision"] == "candidate" else c["decision"],
                 "row_key": c["candidate_row_key"] if c["decision"] == "candidate" else None}
                for c in cases]
    prefix = "reviewed_" if mode == "review" else ""
    return [{"case_id": c["case_id"], "decision": c[prefix + "decision"],
             "row_key": c[prefix + "adopted_row_key"]} for c in cases]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-dir", required=True, type=Path)
    args = parser.parse_args()
    output = args.evaluation_dir.resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError("use a new evaluation directory outside the checkout")
    inventory = read(HERE / "source-inventory.json")
    grader = EXPERIMENT / "evaluate_frozen_sku_accuracy.py"
    assert digest(grader) == inventory["grader_sha256"], "grader changed"
    for rel, expected in inventory["frozen_prediction_inputs"].items():
        assert digest(ROOT / rel) == expected, rel
    output.mkdir(parents=True, exist_ok=False)
    labels = []
    for entry in inventory["labels"]:
        archive = EXPERIMENT / "results" / entry["archive"]
        assert digest(archive) == entry["archive_sha256"], archive
        with ZipFile(archive) as z:
            data = z.read(entry["label_member"])
            manifest_data = z.read(str(Path(entry["label_member"]).parent / "manifest.json"))
        assert hashlib.sha256(data).hexdigest() == entry["label_sha256"]
        assert hashlib.sha256(manifest_data).hexdigest() == entry["manifest_sha256"]
        path = output / (entry["tag"] + "-labels.jsonl")
        path.write_bytes(data)
        labels.append(path)

    specs = [
        ("latest_pipeline", [(LUNA / "summary.json", "final")]),
        ("luna_matching_stage", [(LUNA / s / "evaluation.json", "candidate")
                                 for s in ("r02-reference-development", "r03-reference-holdout")]),
        ("luna_baseline", [(LUNA / "r00-baseline/evaluation.json", "raw")]),
        ("gemini_raw", [(GEMINI / "summary.json", "raw")]),
        ("gemini_reviewed", [(GEMINI / "review-summary.json", "review")]),
        ("latest_fixed6", [(LUNA / "r02-reference-development/reviewed-summary.json", "final")]),
        ("latest_additional12", [(LUNA / "r03-reference-holdout/reviewed-summary.json", "final")]),
    ]
    inputs = [LUNA / s / "inputs.json" for s in ("r02-reference-development", "r03-reference-holdout")]
    metrics = {}
    start = time.monotonic()
    for tag, sources in specs:
        cases = [case for path, mode in sources for case in normalized(path, mode)]
        predictions = output / (tag + "-predictions.json")
        save(predictions, {"cases": cases})
        command = [sys.executable, "-B", str(grader), "--predictions", str(predictions)]
        for path in labels:
            command += ["--labels", str(path)]
        for path in inputs:
            command += ["--inputs", str(path)]
        command += ["--output", str(output / tag)]
        subprocess.run(command, check=True, cwd=output)
        metrics[tag] = read(output / tag / "summary.json")["metrics"]
    elapsed = time.monotonic() - start
    for rel, expected in inventory["frozen_prediction_inputs"].items():
        assert digest(ROOT / rel) == expected, rel
    save(output / "comparisons.json", metrics)
    saved = HERE / "comparisons.json"
    if saved.exists():
        assert metrics == read(saved), "saved metrics differ"
        assert ((output / "latest_pipeline/case-results.jsonl").read_bytes()
                == (HERE / "case-results.jsonl").read_bytes()), "saved case scores differ"
    print(json.dumps({"evaluation_dir": str(output), "grading_seconds": round(elapsed, 6),
                      "latest_pipeline": metrics["latest_pipeline"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
