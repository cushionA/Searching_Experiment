"""Reused machine-label diagnostics after source-only predictions are frozen."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=ROOT / ".lab-output/sku-nonllm-binary-gate-20261010-v2")
    parser.add_argument("--labels", type=Path, default=ROOT / ".lab-output/sku-real-luna-labels-20261010-v3/labels.jsonl")
    args = parser.parse_args()
    output = args.run / "reused-label-diagnostics.json"
    if output.exists():
        raise FileExistsError(output)
    manifest = json.loads((args.run / "manifest.json").read_text())
    files = ["predictions/legacy-baseline.jsonl", "predictions/legacy-improved.jsonl"]
    for name in files:
        if digest(args.run / name) != manifest["files"][name]:
            raise ValueError("Frozen predictions changed: " + name)
    labels = {}
    with args.labels.open() as f:
        for line in f:
            label = json.loads(line)
            if label["case_id"] in labels:
                raise ValueError("duplicate label case")
            labels[label["case_id"]] = label
    results, errors = {}, []
    for name in files:
        counts = Counter()
        by_category = {k: Counter() for k in ("curtain", "non_curtain")}
        with (args.run / name).open() as f:
            for line in f:
                pred = json.loads(line)
                label = labels.get(pred["case_id"])
                if label is None:
                    raise ValueError("Missing label: " + pred["case_id"])
                category = "curtain" if any(r.get("component") == "lace" for r in pred["requirements"]) else "non_curtain"
                exact = (label["decision"] == "matched" and pred["au_row_key"] in label.get("matching_au_row_keys", []))
                key = ("correct_accept" if exact else "accept_labeled_" + label["decision"]) if pred["decision"] == "accept" else (
                    "missed_labeled_match" if label["decision"] == "matched" else "drop_labeled_" + label["decision"])
                counts[key] += 1
                by_category[category][key] += 1
                if pred["decision"] == "accept" and not exact:
                    errors.append({"file": name, "case_id": pred["case_id"], "predicted_au_row_key": pred["au_row_key"],
                                   "label_decision": label["decision"], "label_rows": label.get("matching_au_row_keys", [])})
        accepts = sum(v for k, v in counts.items() if k == "correct_accept" or k.startswith("accept_labeled_"))
        labeled_matches = counts["correct_accept"] + counts["missed_labeled_match"]
        results[name] = {"counts": dict(counts), "by_category": {k: dict(v) for k, v in by_category.items()},
                         "accepted_row_precision_vs_reused_labels": counts["correct_accept"] / accepts if accepts else None,
                         "match_recall_vs_reused_labels": counts["correct_accept"] / labeled_matches if labeled_matches else None}
    report = {"classification": "reused Luna machine-label diagnostics; human-unverified; not independent gold or holdout",
              "predictions_persisted_and_hash_verified_before_labels": True,
              "labels_sha256": digest(args.labels), "labels_count": len(labels), "results": results, "accept_disagreements": errors}
    with output.open("x") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
