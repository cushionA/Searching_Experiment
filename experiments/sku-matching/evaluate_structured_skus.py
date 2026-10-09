"""Source-backed fixed-page SKU task trials; labels enter after prediction.

These experiments reuse an already inspected split. They are diagnostics, not a
fresh holdout or a population-level precision guarantee. Prices and inventory
remain outside both the model input and semantic identity decisions.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import importlib
from importlib.metadata import version, PackageNotFoundError
import json
import os
from pathlib import Path
import platform
import re
import time
from typing import Any

from evaluate_luna_real_skus import (
    DEFAULT_INPUTS, DEFAULT_AU_ARRAYS, MODELS, read_jsonl, load_context,
    join_labels, metrics, sha256, collect_input_snapshot, verify_input_manifests,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_LABELS = ROOT / ".lab-output/sku-real-luna-labels-20261010-v1/labels.jsonl"
MODES = ("canonical", "canonical-raw")
THRESHOLDS = tuple(round(i / 100, 2) for i in range(70, 100))
NON_IDENTITY_FIELDS = {"components", "color_options", "size_options", "component_rules"}


def write_new(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_jsonl_new(path: Path, rows: list[dict]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def identity_attrs(entity: dict) -> dict:
    attrs = {k: v for k, v in entity.get("attrs", {}).items()
             if k not in NON_IDENTITY_FIELDS and not k.endswith("_options")}
    if "normalized_size" in attrs:
        attrs.pop("size", None)
    if "normalized_color" in attrs:
        attrs.pop("color", None)
    return attrs


def feature_text(entity: dict, mode: str = "canonical") -> str:
    """Expose only selected, inherited identity fields to CPU models."""
    if mode not in MODES:
        raise ValueError(f"Unsupported feature mode: {mode}")
    fields = identity_attrs(entity)
    parts = [f"{key}={json.dumps(fields[key], ensure_ascii=False, sort_keys=True)}"
             for key in sorted(fields)]
    if entity.get("unknown_fields"):
        parts.append("unresolved=" + ",".join(sorted(set(entity["unknown_fields"]))))
    if entity.get("source_conflicts"):
        parts.append("source_conflicts=" + json.dumps([
            {"field": x["field"], "values": x["values"]} for x in entity["source_conflicts"]
        ], ensure_ascii=False, sort_keys=True))
    if mode == "canonical-raw":
        parts.append("selected SKU=" + entity.get("raw_sku", ""))
    return " / ".join(parts) or "selected specifications unknown"


def _unambiguous_difference(key: str, left: Any, right: Any) -> bool:
    """A non-identical spelling alone is insufficient to disprove an alias."""
    if key in {"hook_count", "tassel_count", "page_dimensions_cm"}:
        # Page-level discrepancies can be a typo or a measurement convention;
        # report uncertainty instead of claiming a confirmed different SKU.
        return False
    if isinstance(left, bool) and isinstance(right, bool):
        return True
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return True
    if key in {"normalized_size", "size", "drape_count", "lace_count", "count",
               "product_capacity"}:
        # Dimensions must have the same role/unit before this function is used;
        # a named size versus a number remains unresolved.
        numeric = r"\d+(?:\.\d+)?(?:[×x, ]\d+(?:\.\d+)?)*(?:cm|mm|m|枚|個|本|ml|l|kg|g)?"
        return bool(re.fullmatch(numeric, str(left), re.I)
                    and re.fullmatch(numeric, str(right), re.I))
    return False


def gate(task: dict, candidate: dict) -> dict:
    """Match known fields; reject explicit conflicts; abstain on missing facts.

    This is a conservative evidence gate, not a learned identity classifier.
    It never equates similar colors, and never treats an absent lace word as no
    lace. Every selected field on either side must be resolved to accept.
    """
    query = task["rakuten"]
    left, right = identity_attrs(query), identity_attrs(candidate)
    missing, conflicts, spelling = [], [], []
    for key in sorted(set(left) | set(right)):
        lval, rval = left.get(key), right.get(key)
        if lval is None or rval is None:
            missing.append(key)
        elif lval != rval:
            (conflicts if _unambiguous_difference(key, lval, rval) else spelling).append(key)
    unresolved = sorted(set(query.get("unknown_fields", []))
                        | set(candidate.get("unknown_fields", [])))
    unresolved.extend("source_conflict:" + x["field"] for entity in (query, candidate)
                      for x in entity.get("source_conflicts", []))
    # Unknown axis semantics can still be checked exactly when its observed
    # axis name and value are identical on both sides. Unmapped extra axes must
    # not be dropped merely to raise the acceptance rate.
    for key in set(query.get("unresolved_axes", [])) | set(candidate.get("unresolved_axes", [])):
        if key not in left or key not in right or left[key] != right[key]:
            unresolved.append(key)
    if conflicts:
        decision = "unmatched"
    elif missing or spelling or unresolved or not left or not right:
        decision = "review"
    else:
        decision = "matched"
    return {"decision": decision, "conflicting_fields": conflicts,
            "missing_fields": missing, "unresolved_spelling_fields": spelling,
            "unresolved_fields": sorted(set(unresolved))}


def rule_prediction(task: dict) -> dict:
    checked = [(candidate, gate(task, candidate)) for candidate in task["au_candidates"]]
    matches = [(c, g) for c, g in checked if g["decision"] == "matched"]
    reviews = [(c, g) for c, g in checked if g["decision"] == "review"]
    # Duplicated source rows require a review: returning an arbitrary row can
    # hide ambiguous field normalization or distinct unknown configurations.
    if len(matches) == 1:
        candidate, result = matches[0]
    elif matches:
        candidate, result = matches[0]
        result = {**result, "decision": "review", "ambiguity": "multiple_compatible_au_rows"}
    elif reviews:
        candidate, result = min(reviews, key=lambda x: (len(x[1]["missing_fields"])
                                                       + len(x[1]["unresolved_spelling_fields"]), x[0]["row_key"]))
    else:
        candidate, result = checked[0]
    return {"case_id": task["case_id"], "top_row_key": candidate["row_key"], **result,
            "compatible_row_count": len(matches), "review_row_count": len(reviews)}


def predict_with_gate(task: dict, raw: dict, threshold: float) -> dict:
    candidate = next(c for c in task["au_candidates"] if c["row_key"] == raw["top_row_key"])
    constraint = gate(task, candidate)
    decision = constraint["decision"]
    if decision == "matched" and raw["score"] < threshold:
        decision = "review"
    return {**raw, "gate": constraint, "decision": decision,
            "threshold": threshold}


def select_threshold(tasks: list[dict], gold: list[dict], raw: list[dict]) -> dict:
    options = []
    for threshold in THRESHOLDS:
        predictions = [predict_with_gate(t, p, threshold) for t, p in zip(tasks, raw, strict=True)]
        options.append({"threshold": threshold, **metrics(gold, predictions)})
    eligible = [m for m in options if m["known_case_precision"] is not None
                and m["known_case_precision"] >= .99 and m["review_unknown_accept_count"] == 0]
    selected = max(eligible or options, key=lambda m: (m["known_case_recall"] or 0,
                                                       m["known_case_precision"] or 0,
                                                       m["threshold"]))
    return {"threshold": selected["threshold"], "dev_metrics": selected, "grid": options,
            "status": "dev_target_met" if eligible else "dev_precision_target_not_met",
            "criterion": "max dev recall with precision>=0.99 and no unknown acceptance; otherwise report failure"}


def diagnostic_metrics(tasks: list[dict], gold: list[dict], predictions: list[dict]) -> dict:
    overall = metrics(gold, predictions)
    overall["review_prediction_count"] = sum(p["decision"] == "review" for p in predictions)
    overall["unmatched_prediction_count"] = sum(p["decision"] == "unmatched" for p in predictions)
    overall["case_count"] = len(tasks)
    strata = {}
    names = sorted({s for task in tasks for s in task.get("strata", [])})
    for name in names:
        idx = [i for i, t in enumerate(tasks) if name in t.get("strata", [])]
        strata[name] = {"case_count": len(idx), **metrics([gold[i] for i in idx], [predictions[i] for i in idx])}
    dossiers = {}
    for dossier_id in sorted({t["dossier_id"] for t in tasks}):
        idx = [i for i, t in enumerate(tasks) if t["dossier_id"] == dossier_id]
        dossiers[dossier_id] = {"case_count": len(idx), **metrics([gold[i] for i in idx], [predictions[i] for i in idx])}
    recalls = [m["known_case_recall"] for m in dossiers.values() if m["known_case_recall"] is not None]
    return {"overall": overall, "strata": strata, "per_dossier": dossiers,
            "dossier_macro_recall": sum(recalls) / len(recalls) if recalls else None}


def prepare(args) -> None:
    from structured_sku_task import build_task
    if args.output.exists():
        raise ValueError(f"Refusing existing output: {args.output}")
    source_paths = [args.inputs_dir / "cases.jsonl", args.inputs_dir / "manifest.json", args.au_arrays]
    source_paths += sorted((args.inputs_dir / "dossiers").glob("*.json"))
    before = {str(p): sha256(p) for p in source_paths}
    ready = load_context(args.inputs_dir, args.au_arrays)
    originals = {r["case_id"]: r for r in read_jsonl(args.inputs_dir / "cases.jsonl")}
    dossiers = {d["dossier_id"]: d for d in (
        json.loads(p.read_text(encoding="utf-8")) for p in (args.inputs_dir / "dossiers").glob("*.json"))}
    tasks = []
    evidence_entities = {}
    for case in ready:
        task = build_task({**case, "rakuten": originals[case["case_id"]]["rakuten"]}, dossiers[case["dossier_id"]])
        task.update({k: case[k] for k in ("split", "group_id", "dossier_id", "product_id")})
        keys = [c["row_key"] for c in task["au_candidates"]]
        if keys != [c["row_key"] for c in case["candidates"]]:
            raise ValueError(f"Actual AU SKU pool was changed: {case['case_id']}")
        # Source evidence is shared across repeated references to an AU row.
        # Keep it in a separate cited sidecar rather than copying descriptions
        # and raw-source metadata into every inference record.
        for entity in [task["rakuten"], *task["au_candidates"]]:
            payload = {"raw_sku": entity["raw_sku"], "attrs": entity["attrs"],
                       "evidence": entity.pop("evidence", {})}
            import hashlib
            key = "evidence-" + hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            evidence_entities[key] = {"evidence_id": key, **payload}
            entity["evidence_ref"] = key
            entity.pop("facts", None)
            entity["source_conflicts"] = [{"field": x["field"], "values": x["values"]}
                                          for x in entity.get("source_conflicts", [])]
        tasks.append(task)
    after = {str(p): sha256(p) for p in source_paths}
    if before != after:
        raise ValueError("Original inputs changed during preparation")
    args.output.mkdir(parents=True, exist_ok=False)
    write_jsonl_new(args.output / "tasks.jsonl", tasks)
    write_jsonl_new(args.output / "evidence.jsonl", [evidence_entities[k] for k in sorted(evidence_entities)])
    write_new(args.output / "manifest.json", {
        "prepared_at_utc": datetime.now(timezone.utc).isoformat(), "case_count": len(tasks),
        "synthetic_data_included": False, "labels_read_for_preparation": False,
        "source_sha256_pre": before, "source_sha256_post": after,
        "task_builder_sha256": sha256(HERE / "structured_sku_task.py"),
        "code_sha256": sha256(Path(__file__)), "tasks_sha256": sha256(args.output / "tasks.jsonl"),
        "evidence_sha256": sha256(args.output / "evidence.jsonl"), "unique_evidence_entities": len(evidence_entities),
        "scope": "fixed AU context; all actual AU SKU rows; one record per Rakuten source SKU; no URL routing",
        "price_grain": "AU page price and Rakuten per-SKU prices remain in original eligibility sidecars",
        "strata_counts": dict(Counter(s for t in tasks for s in t.get("strata", []))),
        "test_status": "reused previously inspected test set; exploratory diagnostic only"})
    print(json.dumps({"tasks": len(tasks), "path": str(args.output)}, ensure_ascii=False), flush=True)


def evaluate(args) -> None:
    if args.output.exists():
        raise ValueError(f"Refusing existing output: {args.output}")
    tasks = read_jsonl(args.tasks)
    context = load_context(args.inputs_dir, args.au_arrays)
    if [t["case_id"] for t in tasks] != [c["case_id"] for c in context]:
        raise ValueError("Task case IDs and frozen source order disagree")
    before = collect_input_snapshot(args.inputs_dir, args.labels, args.au_arrays)
    verify_input_manifests(args.inputs_dir, args.labels, args.au_arrays, before)
    tasks_sha = sha256(args.tasks)
    task_manifest = json.loads(args.tasks.with_name("manifest.json").read_text(encoding="utf-8"))
    if task_manifest["tasks_sha256"] != tasks_sha or task_manifest["task_builder_sha256"] != sha256(HERE / "structured_sku_task.py"):
        raise ValueError("Task preparation source/code SHA has changed")
    args.output.mkdir(parents=True, exist_ok=False)
    raw_modes, timing, token_audit, model_info = {}, {}, {}, {}
    if args.model == "rules":
        raw_modes["rules"] = [rule_prediction(task) for task in tasks]
    else:
        os.environ.update(CUDA_VISIBLE_DEVICES="", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
        module, classname, dirname = MODELS[args.model]
        start = time.perf_counter()
        model = getattr(importlib.import_module(module), classname)(ROOT / ".deps" / dirname, threads=2)
        load_time = time.perf_counter() - start
        for mode in MODES:
            queries = [feature_text(t["rakuten"], mode) for t in tasks]
            pools = [[feature_text(c, mode) for c in t["au_candidates"]] for t in tasks]
            all_texts = [text for q, pool in zip(queries, pools, strict=True) for text in [q, *pool]]
            unique = list(dict.fromkeys(all_texts))
            start = time.perf_counter()
            vectors = model.encode(unique, batch_size=8)
            elapsed = time.perf_counter() - start
            lookup = {text: i for i, text in enumerate(unique)}
            rows = []
            for task, query, pool in zip(tasks, queries, pools, strict=True):
                scores = vectors[lookup[query]] @ vectors[[lookup[x] for x in pool]].T
                order = sorted(range(len(scores)), key=lambda i: (-float(scores[i]), i))
                rows.append({"case_id": task["case_id"], "score": float(scores[order[0]]),
                             "top_row_key": task["au_candidates"][order[0]]["row_key"],
                             "top10": [{"row_key": task["au_candidates"][i]["row_key"], "score": float(scores[i])}
                                       for i in order[:10]]})
            raw_modes[mode] = rows
            timing[mode] = {"encode_seconds": elapsed, "unique_texts": len(unique), "text_references": len(all_texts)}
            lengths = model.token_lengths(unique)
            token_audit[mode] = {"max_length": model.MAX_LENGTH, "max_tokens": int(max(lengths)),
                                 "unique_inputs_over_limit": int((lengths > model.MAX_LENGTH).sum())}
        manifest_path = HERE / ("model-manifest.json" if args.model == "minilm" else f"manifests/{args.model}.json")
        model_info = {"manifest": json.loads(manifest_path.read_text()), "manifest_sha256": sha256(manifest_path),
                      "model_weights_sha256": {p.relative_to(ROOT / ".deps" / dirname).as_posix(): sha256(p)
                                                for p in sorted((ROOT / ".deps" / dirname).rglob("*"))
                                                if p.is_file() and p.name not in {"README.md", "manifest.json"}},
                      "load_seconds": load_time, "backend_code_sha256": sha256(HERE / f"{module}.py")}
    # Save label-free predictions before opening labels for metrics/selection.
    for mode, rows in raw_modes.items():
        write_new(args.output / f"predictions-{mode}.json", {"mode": mode, "rows": rows})
    gold = join_labels(context, args.labels)
    dev_idx = [i for i, t in enumerate(tasks) if t["split"] == "dev"]
    test_idx = [i for i, t in enumerate(tasks) if t["split"] == "test"]
    summary_modes = {}
    for mode, rows in raw_modes.items():
        if args.model == "rules":
            predictions = rows
            selected = None
        else:
            selected = select_threshold([tasks[i] for i in dev_idx], [gold[i] for i in dev_idx], [rows[i] for i in dev_idx])
            predictions = [predict_with_gate(t, p, selected["threshold"]) for t, p in zip(tasks, rows, strict=True)]
        write_jsonl_new(args.output / f"decisions-{mode}.jsonl", predictions)
        summary_modes[mode] = {"dev_threshold_selection": selected,
                               "dev": diagnostic_metrics([tasks[i] for i in dev_idx], [gold[i] for i in dev_idx], [predictions[i] for i in dev_idx]),
                               "test_reused_diagnostic": diagnostic_metrics([tasks[i] for i in test_idx], [gold[i] for i in test_idx], [predictions[i] for i in test_idx]),
                               "all": diagnostic_metrics(tasks, gold, predictions)}
    selected_mode = None if args.model == "rules" else max(MODES, key=lambda m: (
        summary_modes[m]["dev"]["overall"]["known_case_recall"] or 0,
        summary_modes[m]["dev"]["overall"]["gold_matched_top1_row_accuracy_before_threshold"] or 0,
        m == "canonical"))
    after = collect_input_snapshot(args.inputs_dir, args.labels, args.au_arrays)
    if before != after or sha256(args.tasks) != tasks_sha:
        raise ValueError("Frozen input changed during evaluation")
    runtime = {}
    for package in ("numpy", "onnxruntime", "torch", "transformers", "tokenizers"):
        try:
            runtime[package] = version(package)
        except PackageNotFoundError:
            runtime[package] = None
    write_new(args.output / "summary.json", {
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(), "model": args.model,
        "label_source": "Luna annotation; human unverified", "synthetic_input_count": 0,
        "test_status": "reused previously inspected test; no fresh holdout claim",
        "threads": 2, "batch_size": 8, "platform": platform.platform(), "python": platform.python_version(),
        "runtime_versions": runtime, "model_info": model_info, "input_sha256": before,
        "frozen_inputs_unchanged": True, "tasks_sha256": tasks_sha,
        "code_sha256": {Path(__file__).name: sha256(Path(__file__)), "structured_sku_task.py": sha256(HERE / "structured_sku_task.py")},
        "timing": timing, "token_length_audit": token_audit, "modes": summary_modes,
        "selected_mode_dev_only": selected_mode,
        "warning": "A similarity score cannot override missing sales composition or an explicit field conflict."})
    print(json.dumps({"model": args.model, "selected_mode": selected_mode,
                      "metrics": {m: v["test_reused_diagnostic"]["overall"] for m, v in summary_modes.items()}}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--inputs-dir", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--au-arrays", type=Path, default=DEFAULT_AU_ARRAYS)
    parser.add_argument("--tasks", type=Path)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--model", choices=["rules", *MODELS])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.prepare:
        prepare(args)
    else:
        if not args.tasks or not args.model:
            parser.error("--tasks and --model are required for evaluation")
        evaluate(args)


if __name__ == "__main__":
    main()
