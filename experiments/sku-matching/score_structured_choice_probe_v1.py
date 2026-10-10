#!/usr/bin/env python3
"""Score frozen CPU probes using independent machine-only condition annotations.

Window choices diagnose the task format. They never prove applicability or
authorize a whole-SKU match. Thresholds are fixed before inference, not fitted.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def relation(values, selected):
    values = set(values)
    return "unknown" if len(values) != 1 else "support" if selected in values else "conflict"


def score(run_dirs: list[Path], annotations: Path, output: Path):
    if output.exists():
        raise FileExistsError(output)
    # Freeze verification precedes reading the annotation file.
    runs = []
    for directory in run_dirs:
        summary = json.loads((directory / "summary.json").read_text())
        freeze = json.loads((directory / "freeze.json").read_text())
        if any(summary.get(k) != v for k, v in freeze.items()):
            raise ValueError("completed run differs from pre-inference freeze")
        for name, digest in summary["output_sha256"].items():
            if sha(directory / name) != digest:
                raise ValueError("frozen model output SHA mismatch")
        for name, digest in freeze["code_sha256"].items():
            if sha(directory / "code" / name) != digest:
                raise ValueError("frozen model code SHA mismatch")
        runs.append((directory, summary, read(directory / "requests.jsonl"), read(directory / "predictions.jsonl")))
    labels = read(annotations)
    by_label = {(r["case_id"], r["au_row_key"], r["axis_name"]): r for r in labels}
    if len(by_label) != len(labels):
        raise ValueError("duplicate annotated condition")
    report = {"gold_status": "independent_machine_annotations_not_human_gold", "whole_sku_accuracy_measured": False,
              "annotation_sha256": sha(annotations), "annotation_count": len(labels), "production_eligible": False,
              "scope_verified_by_this_probe": False, "models": []}
    for directory, summary, requests, records in runs:
        grouped, title_argmax = {}, {}
        requests_by_id = {r["id"]: r for r in requests}
        for record in records:
            request_id = record["id"] if summary["backend"] == "jev" else record["request_id"]
            request = requests_by_id[request_id]
            p = request["provenance"]
            key = (p["case_id"], p["au_row_key"], p["axis_name"])
            label = by_label[key]
            if label["selected_value"] != p["selected_value"]:
                raise ValueError("selected value differs from independent annotation")
            for threshold in summary["thresholds_for_diagnostics"]:
                winners = grouped.setdefault((p["arm"], threshold, key), set())
                if summary["backend"] == "jev":
                    choice = record.get("argmax_label")
                    if choice and choice != "unknown" and record["argmax_probability"] >= threshold:
                        winners.add(p["option_values"][int(choice.split(":")[1])])
                elif (record.get("probabilities") or {}).get("support", 0) >= threshold:
                    winners.add(record["choice_description"])
            if summary["backend"] == "jev" and p["window"]["field_kind"] == "plain_title":
                choice = record.get("argmax_label")
                title_argmax[(p["arm"], key)] = (set() if not choice or choice == "unknown" else
                                                       {p["option_values"][int(choice.split(":")[1])]})
        measurements = []
        for arm in sorted({p[0] for p in grouped}):
            for threshold in summary["thresholds_for_diagnostics"]:
                confusion, rows = Counter(), []
                for key, label in by_label.items():
                    values = grouped[(arm, threshold, key)]
                    actual = relation(values, label["selected_value"])
                    confusion[(label["relation"], actual)] += 1
                    rows.append({"case_id": key[0], "au_row_key": key[1], "axis_name": key[2],
                                 "selected_value": label["selected_value"], "expected": label["relation"],
                                 "predicted": actual, "window_winner_values": sorted(values), "scope_proven": False})
                tp = confusion[("support", "support")]
                support_n = sum(n for (e, a), n in confusion.items() if a == "support")
                gold_support_n = sum(n for (e, a), n in confusion.items() if e == "support")
                measurements.append({"arm": arm, "threshold": threshold,
                    "primary": threshold == summary["primary_threshold"],
                    "confusion": [{"expected": e, "predicted": a, "count": n} for (e, a), n in sorted(confusion.items())],
                    "true_support": tp, "false_support": support_n - tp,
                    "support_precision": tp / support_n if support_n else None,
                    "support_recall": tp / gold_support_n if gold_support_n else None,
                    "agreement": sum(confusion[(v, v)] for v in ("support", "conflict", "unknown")) / len(labels),
                    "rows": rows})
        title_measurements = []
        for arm in sorted({p[0] for p in title_argmax}):
            confusion = Counter((label["relation"], relation(title_argmax[(arm, key)], label["selected_value"]))
                                for key, label in by_label.items())
            title_measurements.append({"arm": arm, "threshold": None, "mode": "title_argmax_without_rejection_diagnostic_only",
                "confusion": [{"expected": e, "predicted": a, "count": n} for (e, a), n in sorted(confusion.items())]})
        report["models"].append({"backend": summary["backend"], "run_dir": str(directory),
                                  "pins": summary["pins"], "measurements": measurements,
                                  "title_argmax_diagnostics": title_measurements})
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dirs", type=Path, nargs="+", required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    result = score(**vars(parser.parse_args()))
    print(json.dumps({"annotation_count": result["annotation_count"], "models": len(result["models"])}))
