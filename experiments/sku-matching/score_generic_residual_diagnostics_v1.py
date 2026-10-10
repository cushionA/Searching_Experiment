#!/usr/bin/env python3
"""Score frozen residual diagnostics against explicitly machine-only labels."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def read(path):
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def score(runs: list[Path], annotations: Path, output: Path):
    if output.exists():
        raise FileExistsError(output)
    if any(not (run / "summary.json").is_file() for run in runs):
        raise ValueError("all predictions must be complete before reading labels")
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    summaries = [json.loads((run / "summary.json").read_text(encoding="utf-8")) for run in runs]
    for run, summary in zip(runs, summaries, strict=True):
        freeze = json.loads((run / "freeze.json").read_text(encoding="utf-8"))
        if any(summary.get(key) != value for key, value in freeze.items()):
            raise ValueError(f"summary differs from its prediction freeze: {run}")
        if sha(run / "requests.jsonl") != freeze["request_sha256"]:
            raise ValueError(f"request freeze mismatch: {run}")
        for filename, digest in freeze.get("code_snapshot_sha256", {}).items():
            if sha(run / "code" / filename) != digest:
                raise ValueError(f"code snapshot mismatch: {run / filename}")
        for filename, digest in summary["output_sha256"].items():
            if sha(run / filename) != digest:
                raise ValueError(f"prediction freeze mismatch: {run / filename}")
    annotation_manifest = json.loads((annotations.parent / "manifest.json").read_text(encoding="utf-8"))
    if sha(annotations) != annotation_manifest["outputs"][annotations.name]:
        raise ValueError("annotation manifest hash mismatch")
    for filename, digest in annotation_manifest["inputs"].items():
        if any(summary["input_sha256"].get(Path(filename).name) != digest for summary in summaries):
            raise ValueError("annotations and model runs use different frozen inputs")
    labels = read(annotations)
    if len({label["task_id"] for label in labels}) != len(labels):
        raise ValueError("duplicate annotation task ID")
    if ({label["task_id"] for label in labels} != set(annotation_manifest["sample_method"]["subset_task_ids"])
            or len(labels) != annotation_manifest["counters"]["annotated"]):
        raise ValueError("annotation sample differs from its manifest")
    report = {"evaluation_scope": "residual_conditions_only", "gold_status": "independent_machine_annotations_not_human_gold",
              "sku_accuracy_measured": False, "annotation_sha256": sha(annotations),
              "annotation_manifest": annotation_manifest, "annotation_count": len(labels), "models": []}
    for run, summary in zip(runs, summaries, strict=True):
        predictions = {row["task_id"]: row for row in read(run / "condition-diagnostics.jsonl")}
        # A diagnostic ablation, not permission to omit row applicability.
        content_relations = {}
        for record in read(run / "predictions.jsonl"):
            if record["purpose"] == "condition":
                content_relations.setdefault(record["task_id"], []).append(record["relation"])
        content_only = {task_id: "conflict" if "conflict" in values else "support" if "support" in values else "unknown"
                        for task_id, values in content_relations.items()}
        confusion, errors = Counter(), []
        content_confusion = Counter()
        for label in labels:
            prediction = predictions[label["task_id"]]
            for key in ("case_id", "au_row_key", "condition_id"):
                if label[key] != prediction[key]:
                    raise ValueError(f"annotation-to-prediction binding mismatch: {key}")
            expected, actual = label["label"], prediction["diagnostic_relation"]
            if expected not in {"support", "conflict", "unknown"} or actual not in {"support", "conflict", "unknown"}:
                raise ValueError("invalid residual relation")
            confusion[(expected, actual)] += 1
            content_confusion[(expected, content_only[label["task_id"]])] += 1
            if actual != expected:
                errors.append({"task_id": label["task_id"], "expected": expected, "predicted": actual,
                               "condition_text": prediction["condition_text"], "annotation": label})
        true_support = confusion[("support", "support")]
        predicted_support = sum(count for (expected, actual), count in confusion.items() if actual == "support")
        expected_support = sum(count for (expected, actual), count in confusion.items() if expected == "support")
        report["models"].append({"backend": summary["backend"], "run_dir": str(run),
                                 "prediction_sha256": sha(run / "predictions.jsonl"),
                                 "confusion": [{"expected": expected, "predicted": actual, "count": count}
                                               for (expected, actual), count in sorted(confusion.items())],
                                 "true_support": true_support, "predicted_support": predicted_support,
                                 "expected_support": expected_support, "false_support": predicted_support - true_support,
                                 "support_precision": true_support / predicted_support if predicted_support else None,
                                 "support_recall": true_support / expected_support if expected_support else None,
                                 "three_class_agreement": sum(confusion[(label, label)] for label in ("support", "conflict", "unknown")) / len(labels),
                                 "condition_only_without_scope_ablation": {
                                     "production_eligible": False,
                                     "confusion": [{"expected": expected, "predicted": actual, "count": count}
                                                   for (expected, actual), count in sorted(content_confusion.items())],
                                     "true_support": content_confusion[("support", "support")],
                                     "false_support": sum(count for (expected, actual), count in content_confusion.items()
                                                          if actual == "support" and expected != "support")},
                                 "all_condition_diagnostics": summary["diagnostic_relations"], "errors": errors})
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for model in report["models"]:
        print(json.dumps({key: model[key] for key in ("backend", "true_support", "false_support", "support_precision", "support_recall")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    score(**vars(parser.parse_args()))
