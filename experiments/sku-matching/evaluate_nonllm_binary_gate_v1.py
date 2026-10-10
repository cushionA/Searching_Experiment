"""Posthoc diagnostics against reused Luna machine labels (never gold/holdout)."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEGACY_FILES = ["predictions/legacy-baseline.jsonl", "predictions/legacy-improved.jsonl"]
NOVEL_FILES = ["predictions/novel-baseline.jsonl", "predictions/novel-improved.jsonl"]
DEFAULT_RUN = ROOT / ".lab-output/sku-nonllm-binary-gate-20261010-v2"
DEFAULT_LABELS = ROOT / ".lab-output/sku-real-luna-labels-20261010-v3/labels.jsonl"
NOVEL_LABELS = ROOT / ".lab-output/sku-novel-luna-labels-20261010-v1/labels.jsonl"
LABEL_DECISIONS = {"matched", "unmatched", "review", "label_unresolved"}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_jsonl(path: Path):
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def adapt_label(label: dict) -> dict:
    """Return a normalized copy, accepting the legacy and novel label fields."""
    has_decision = "decision" in label
    has_label = "label" in label
    if not has_decision and not has_label:
        raise ValueError("label needs a decision or label field")
    if has_decision and has_label and label["decision"] != label["label"]:
        raise ValueError("conflicting decision and label fields")
    decision = label["decision"] if has_decision else label["label"]
    if not isinstance(decision, str) or decision not in LABEL_DECISIONS:
        raise ValueError("unknown label enum: " + repr(decision))
    normalized = dict(label)
    normalized["decision"] = decision
    return normalized


def verify_predictions(run: Path, files: list[str], manifest: dict) -> None:
    """Verify every requested frozen prediction before callers open labels."""
    expected = manifest.get("files", {})
    for name in files:
        if name not in expected:
            raise ValueError("Prediction missing from freeze manifest: " + name)
        if digest(run / name) != expected[name]:
            raise ValueError("Frozen predictions changed: " + name)


def evaluate_file(name: str, predictions: list[dict], labels: dict):
    ids = [p["case_id"] for p in predictions]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate prediction case in " + name)
    if set(ids) != set(labels):
        missing = sorted(set(labels) - set(ids))
        extra = sorted(set(ids) - set(labels))
        raise ValueError(f"prediction/label case sets differ for {name}: missing={missing}, extra={extra}")

    counts = Counter()
    by_category = {k: Counter() for k in ("curtain", "non_curtain")}
    errors = []
    for pred in predictions:
        label = labels[pred["case_id"]]
        category = "curtain" if any(r.get("component") == "lace" for r in pred["requirements"]) else "non_curtain"
        exact = label["decision"] == "matched" and pred["au_row_key"] in label.get("matching_au_row_keys", [])
        if pred["decision"] == "accept":
            key = "correct_accept" if exact else "accept_labeled_" + label["decision"]
        else:
            key = "missed_labeled_match" if label["decision"] == "matched" else "drop_labeled_" + label["decision"]
        counts[key] += 1
        by_category[category][key] += 1
        if pred["decision"] == "accept" and not exact:
            errors.append({"file": name, "case_id": pred["case_id"], "predicted_au_row_key": pred["au_row_key"],
                           "label_decision": label["decision"], "label_rows": label.get("matching_au_row_keys", [])})

    accepts = sum(v for k, v in counts.items() if k == "correct_accept" or k.startswith("accept_labeled_"))
    # Every gold matched case belongs in recall's denominator, including wrong-row accepts.
    gold_matches = sum(label["decision"] == "matched" for label in labels.values())
    results = {"counts": dict(counts), "by_category": {k: dict(v) for k, v in by_category.items()},
               "accepted_row_precision_vs_reused_labels": counts["correct_accept"] / accepts if accepts else None,
               "match_recall_vs_reused_labels": counts["correct_accept"] / gold_matches if gold_matches else None}
    return results, errors


def run_evaluation(run: Path, labels_path: Path, files: list[str], output_name: str, label_group: str):
    output = run / output_name
    if output.exists():
        raise FileExistsError(output)
    manifest = json.loads((run / "manifest.json").read_text())
    # Do not inspect or open label contents until all requested prediction hashes pass.
    verify_predictions(run, files, manifest)
    label_sha = digest(labels_path)
    labels_list = read_jsonl(labels_path)
    labels = {}
    for source_label in labels_list:
        label = adapt_label(source_label)
        if label["case_id"] in labels:
            raise ValueError("duplicate label case")
        labels[label["case_id"]] = label

    results, errors = {}, []
    for name in files:
        prediction_rows = read_jsonl(run / name)
        result, disagreements = evaluate_file(name, prediction_rows, labels)
        results[name] = result
        errors.extend(disagreements)
    report = {
        "classification": "posthoc reused Luna machine-label source annotation; human-unverified; not gold; not an independent holdout",
        "human_workflow": "NONE",
        "independence_note": "Novel inputs reuse the existing evaluation inputs; annotation blindness does not make them an independent holdout.",
        "predictions_persisted_and_hash_verified_before_labels": True,
        "evaluator_sha256": digest(Path(__file__)),
        "label_groups": {label_group: {"labels_sha256": label_sha, "labels_count": len(labels)}},
        "results": results,
        "accept_disagreements": errors,
    }
    with output.open("x") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--novel-labels", action="store_true", help="evaluate frozen novel-baseline/improved predictions against the blind Luna annotations")
    args = parser.parse_args()
    if args.novel_labels:
        run_evaluation(args.run, NOVEL_LABELS, NOVEL_FILES, "novel-label-diagnostics.json", "novel")
    else:
        run_evaluation(args.run, args.labels, LEGACY_FILES, "reused-label-diagnostics.json", "legacy")


if __name__ == "__main__":
    main()
