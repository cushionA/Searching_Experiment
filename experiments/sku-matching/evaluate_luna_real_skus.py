"""Evaluate pinned SKU rankers against independently annotated real SKU cases.

Labels are joined only after candidate scoring and are used for metrics. A fixed
AU product's complete SKU array is scored for each Rakuten query; neither URL
routing nor price/stock eligibility participates in model inputs or ranking.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import importlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import platform
import re
import resource
import time
import unicodedata
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_INPUTS = ROOT / ".lab-output/sku-real-luna-annotation-inputs-20261010-v2"
DEFAULT_AU_ARRAYS = ROOT / ".lab-output/sku-observed-product-pairs-20261010-final-v4/au_product_sku_arrays.jsonl"
MODELS = {
    "minilm": ("embeddings", "EmbeddingModel", "sku-matching-model"),
    "ruri": ("backend_ruri", "Model", "sku-ruri-model"),
    "bekko": ("backend_bekko", "Model", "sku-bekko-model"),
    "granite": ("backend_granite", "Model", "sku-granite-model"),
}
DECISIONS = ("matched", "unmatched", "review")
THRESHOLDS = tuple(round(x / 100, 2) for x in range(70, 100))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_input_snapshot(inputs_dir: Path, labels_path: Path, au_arrays_path: Path) -> dict[str, Any]:
    """Hash every fixed input and manifest before inference, then compare after."""
    annotation_files = {}
    for name in ("cases.jsonl", "eligibility.jsonl", "au_eligibility.jsonl",
                 "dossier_index.json", "TASK_SPEC.txt"):
        path = inputs_dir / name
        if path.is_file():
            annotation_files[name] = sha256(path)
    dossiers = {p.relative_to(inputs_dir).as_posix(): sha256(p)
                for p in sorted((inputs_dir / "dossiers").glob("*.json"))}
    annotation_manifests = {
        p.relative_to(inputs_dir).as_posix(): sha256(p)
        for p in sorted(inputs_dir.rglob("*manifest*.json"))
        if p.is_file()
    }
    # Some manifests are conventionally named manifest.json and do not include
    # the word "manifest" in a surrounding path segment.
    for p in sorted(inputs_dir.glob("*/manifest.json")):
        annotation_manifests[p.relative_to(inputs_dir).as_posix()] = sha256(p)
    label_files = {labels_path.name: sha256(labels_path)}
    label_manifests = {}
    if labels_path.parent.is_dir():
        label_manifest_path = labels_path.parent / "manifest.json"
        if label_manifest_path.is_file():
            label_manifest = json.loads(label_manifest_path.read_text(encoding="utf-8"))
            for name in label_manifest.get("output_sha256", {}):
                artifact = labels_path.parent / name
                if not artifact.is_file():
                    raise ValueError(f"Labels manifest output is missing: {artifact}")
                label_files[name] = sha256(artifact)
        for p in sorted(labels_path.parent.rglob("*manifest*.json")):
            if p.is_file():
                label_manifests[p.relative_to(labels_path.parent).as_posix()] = sha256(p)
        for p in sorted(labels_path.parent.glob("*/manifest.json")):
            if p.is_file():
                label_manifests[p.relative_to(labels_path.parent).as_posix()] = sha256(p)
    arrays_manifest_path = au_arrays_path.parent / "manifest.json"
    arrays_manifest = ({str(arrays_manifest_path): sha256(arrays_manifest_path)}
                      if arrays_manifest_path.is_file() else {})
    return {
        "annotation_files": annotation_files,
        "dossiers": dossiers,
        "annotation_manifests": annotation_manifests,
        "labels": label_files,
        "labels_manifests": label_manifests,
        "au_product_sku_arrays": {str(au_arrays_path): sha256(au_arrays_path)},
        "au_product_sku_arrays_manifest": arrays_manifest,
    }


def verify_input_manifests(inputs_dir: Path, labels_path: Path, au_arrays_path: Path,
                           snapshot: dict[str, Any]) -> dict[str, Any]:
    """Check declared artifact hashes before trusting inputs for evaluation."""
    root_manifest_path = inputs_dir / "manifest.json"
    if not root_manifest_path.is_file():
        raise ValueError(f"Missing annotation input manifest: {root_manifest_path}")
    root_manifest = json.loads(root_manifest_path.read_text(encoding="utf-8"))
    if root_manifest.get("case_count") != 1383:
        raise ValueError("Annotation input manifest case_count differs from the frozen 1383-case set")
    declared_outputs = root_manifest.get("output_sha256", {})
    for filename in ("cases.jsonl", "eligibility.jsonl", "au_eligibility.jsonl"):
        actual = snapshot["annotation_files"].get(filename)
        declared = declared_outputs.get(filename)
        if not actual or not declared or actual != declared:
            raise ValueError(f"Annotation input manifest SHA mismatch for {filename}")
    declared_dossiers = root_manifest.get("dossier_sha256", {})
    actual_dossiers = snapshot["dossiers"]
    if not declared_dossiers or declared_dossiers != actual_dossiers:
        raise ValueError("Annotation input manifest dossier SHA set or digest mismatch")

    for shard_name, shard_meta in root_manifest.get("shards", {}).items():
        shard_manifest_path = inputs_dir / shard_name / "manifest.json"
        shard_cases_path = inputs_dir / shard_name / "cases.jsonl"
        if not shard_manifest_path.is_file() or not shard_cases_path.is_file():
            raise ValueError(f"Missing frozen annotation shard files for {shard_name}")
        shard_manifest = json.loads(shard_manifest_path.read_text(encoding="utf-8"))
        digest = sha256(shard_cases_path)
        if digest != shard_meta.get("cases_sha256") or digest != shard_manifest.get("cases_sha256"):
            raise ValueError(f"Annotation shard manifest SHA mismatch for {shard_name}")

    label_manifest_path = labels_path.parent / "manifest.json"
    if not label_manifest_path.is_file():
        raise ValueError(f"Missing frozen labels manifest: {label_manifest_path}")
    label_manifest = json.loads(label_manifest_path.read_text(encoding="utf-8"))
    declared_labels = label_manifest.get("output_sha256", {})
    for name, expected in declared_labels.items():
        if snapshot["labels"].get(name) != expected:
            raise ValueError(f"Labels manifest SHA mismatch for {name}")
    expected_label_sha = (declared_labels.get(labels_path.name)
                          or label_manifest.get("labels_sha256")
                          or label_manifest.get("labels_file_sha256"))
    if not expected_label_sha or expected_label_sha != snapshot["labels"][labels_path.name]:
        raise ValueError("Labels manifest SHA mismatch or has no declared labels digest")
    label_protocol = label_manifest.get("annotation_protocol", {})
    if label_manifest.get("case_count") != 1383:
        raise ValueError("Labels manifest case_count differs from the frozen 1383-case set")
    if label_manifest.get("schema_version") != "luna-sku-label-manifest-v1":
        raise ValueError("Unexpected labels manifest schema version")
    manifest_input_dir = label_manifest.get("input_dir")
    if not manifest_input_dir or not Path(manifest_input_dir).is_absolute():
        raise ValueError("Labels manifest must record its original absolute input_dir")
    if (label_protocol.get("model") != "gpt-6-luna"
            or label_protocol.get("human_verified") is not False
            or label_protocol.get("existing_model_predictions_used") is not False
            or label_protocol.get("synthetic_data_included") is not False):
        raise ValueError("Labels manifest annotation protocol is missing required independence flags")
    expected_input_manifest_sha = label_manifest.get("input_manifest_sha256")
    actual_input_manifest_sha = snapshot["annotation_manifests"].get("manifest.json")
    if expected_input_manifest_sha != actual_input_manifest_sha:
        raise ValueError("Labels manifest does not reference this frozen annotation input manifest")
    expected_cases_sha = label_manifest.get("cases_sha256")
    actual_cases_sha = snapshot["annotation_files"].get("cases.jsonl")
    if expected_cases_sha != actual_cases_sha:
        raise ValueError("Labels manifest cases_sha256 differs from frozen annotation cases")

    arrays_manifest_path = au_arrays_path.parent / "manifest.json"
    if not arrays_manifest_path.is_file():
        raise ValueError(f"Missing AU-array source manifest: {arrays_manifest_path}")
    arrays_manifest = json.loads(arrays_manifest_path.read_text(encoding="utf-8"))
    expected_array_sha = arrays_manifest.get("outputs", {}).get(au_arrays_path.name)
    if expected_array_sha != snapshot["au_product_sku_arrays"][str(au_arrays_path)]:
        raise ValueError("AU product SKU array manifest SHA mismatch")
    if arrays_manifest.get("synthetic_data_included") is not False:
        raise ValueError("AU product SKU array manifest does not confirm a real-data-only input")
    return {"manifest_input_dir": manifest_input_dir,
            "selected_input_dir": str(inputs_dir.resolve()),
            "location_rebased": Path(manifest_input_dir).resolve() != inputs_dir.resolve()}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{n}: expected object")
                rows.append(row)
    return rows


def au_row_key(product_id: str, cell: dict[str, Any]) -> str:
    source = cell["source_grain"]
    return (f"au:{product_id}:{source['sku_id']}:{source['row_index']}"
            f":{source['column_index']}")


def axis_text(values: list[dict[str, Any]], name_key: str, value_key: str) -> str:
    """Preserve the observed axis order and values as model-visible text."""
    return " / ".join(
        f"{str(axis.get(name_key) or axis.get('axis_key') or axis.get('key') or '').strip()}="
        f"{str(axis.get(value_key) or '').strip()}"
        for axis in values if str(axis.get(value_key) or "").strip()
    )


def model_input_texts(case: dict[str, Any], mode: str) -> tuple[str, list[str]]:
    """Build inputs strictly from the precomputed semantic title and axis text."""
    if mode not in {"sku", "title-sku"}:
        raise ValueError(f"Unsupported model input mode: {mode}")
    query = case["query_sku"] if mode == "sku" else case["query_title_sku"]
    key = "text_sku" if mode == "sku" else "text_title_sku"
    return query, [candidate[key] for candidate in case["candidates"]]


def load_context(inputs_dir: Path, au_arrays_path: Path):
    cases = read_jsonl(inputs_dir / "cases.jsonl")
    if len(cases) != 1383:
        raise ValueError(f"Expected 1383 frozen cases; found {len(cases)}")
    dossiers_dir = inputs_dir / "dossiers"
    dossiers: dict[str, dict[str, Any]] = {}
    for path in dossiers_dir.glob("*.json"):
        dossier = json.loads(path.read_text(encoding="utf-8"))
        dossiers[dossier["dossier_id"]] = dossier
    arrays = read_jsonl(au_arrays_path)
    arrays_by_product = {str(x["au_product_id"]): x for x in arrays}
    if len({x["case_id"] for x in cases}) != len(cases):
        raise ValueError("Duplicate case_id in input cases")

    ready = []
    for case in cases:
        dossier = dossiers.get(case["dossier_id"])
        if dossier is None:
            raise ValueError(f"Missing dossier {case['dossier_id']}")
        product_id = str(dossier["au_product"]["product_id"])
        product = arrays_by_product.get(product_id)
        if product is None:
            raise ValueError(f"No fixed AU product array for {product_id}")
        cells = product["au_sku_cells_raw"]
        candidates = []
        for cell in cells:
            key = au_row_key(product_id, cell)
            axes = axis_text(cell.get("axes_raw", []), "axis_name_raw", "value_raw")
            candidates.append({"row_key": key,
                               "text_sku": axes,
                               "text_title_sku": f"{product['au_product_title_raw']} / {axes}",
                               "title_only": product["au_product_title_raw"],
                               # Retained only for a separate eligibility/reporting layer.
                               "stock": cell.get("stock_raw")})
        rakuten = case["rakuten"]
        source = rakuten.get("source", {})
        for field in ("sku_record_key", "source_row_index", "url", "sha256"):
            if source.get(field) is None:
                raise ValueError(f"Rakuten source.{field} is required for {case['case_id']}")
        axis_names = {a.get("key"): a.get("label") or a.get("name")
                      for a in rakuten.get("axes_labels", []) if a.get("key")}
        query_axes = []
        for option in rakuten.get("option_values", []):
            option = dict(option)
            option["axis_name"] = option.get("axis_name") or axis_names.get(option.get("axis_key")) or option.get("axis_key")
            query_axes.append(option)
        query_sku = axis_text(query_axes, "axis_name", "value")
        ready.append({
            "case_id": case["case_id"], "group_id": case.get("group_id"),
            "split": case.get("split"), "shard_id": case.get("shard_id"),
            "dossier_id": case["dossier_id"],
            "pair_id": dossier.get("pair_ref"),
            "product_id": product_id,
            "rakuten_sku_key": rakuten.get("source", {}).get("sku_record_key"),
            "rakuten_url": rakuten.get("source", {}).get("url"),
            "rakuten_source_row_index": rakuten.get("source", {}).get("source_row_index"),
            "rakuten_raw_sha256": rakuten.get("source", {}).get("sha256"),
            "query_sku": query_sku,
            "query_title_sku": f"{rakuten.get('title_raw', '')} / {query_sku}",
            "query_title_only": rakuten.get("title_raw", ""),
            "candidates": candidates,
        })
    validate_group_splits(ready)
    return ready


def join_labels(cases: list[dict[str, Any]], labels_path: Path) -> list[dict[str, Any]]:
    """Join label fields only after all model scores have been generated."""
    labels = read_jsonl(labels_path)
    labels_by_id = {row["case_id"]: row for row in labels}
    if len(labels_by_id) != len(labels):
        raise ValueError("Duplicate case_id in labels")
    if {case["case_id"] for case in cases} != set(labels_by_id):
        raise ValueError("Label case IDs must exactly match the fixed input case set")
    joined = []
    for case in cases:
        label = labels_by_id[case["case_id"]]
        decision = label.get("decision")
        if decision not in DECISIONS:
            raise ValueError(f"Invalid semantic label for {case['case_id']}: {decision!r}")
        keys = label.get("matching_au_row_keys", [])
        if not isinstance(keys, list) or any(not isinstance(k, str) for k in keys):
            raise ValueError(f"Invalid matching_au_row_keys for {case['case_id']}")
        if decision == "matched" and not keys:
            raise ValueError(f"Matched label needs at least one AU row key: {case['case_id']}")
        if decision != "matched" and keys:
            raise ValueError(f"Only matched labels may name AU rows: {case['case_id']}")
        candidate_keys = {candidate["row_key"] for candidate in case["candidates"]}
        unknown = set(keys) - candidate_keys
        if unknown:
            raise ValueError(f"Gold AU row keys absent from fixed candidate pool for {case['case_id']}: {sorted(unknown)}")
        joined.append({**case, "decision": decision, "gold_row_keys": keys})
    return joined


def validate_group_splits(cases: list[dict[str, Any]]) -> None:
    """Fail closed if an AU family or Rakuten source URL crosses shards."""
    family_shards: dict[str, set[str]] = defaultdict(set)
    url_shards: dict[str, set[str]] = defaultdict(set)
    group_shards: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        split = case.get("split", case.get("shard_id"))
        if split not in {"dev", "test"}:
            raise ValueError(f"split must be dev or test: {case['case_id']}")
        family_shards[case["product_id"]].add(split)
        if case.get("group_id"):
            group_shards[case["group_id"]].add(split)
        if case.get("rakuten_url"):
            url_shards[case["rakuten_url"]].add(split)
    leaked_families = [k for k, v in family_shards.items() if len(v) > 1]
    leaked_urls = [k for k, v in url_shards.items() if len(v) > 1]
    leaked_groups = [k for k, v in group_shards.items() if len(v) > 1]
    if leaked_families or leaked_urls or leaked_groups:
        raise ValueError(f"family/group/source URL crosses dev/test: groups={leaked_groups[:5]}, "
                         f"families={leaked_families[:5]}, urls={leaked_urls[:5]}")


def metrics(cases: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    matrix = {gold: {pred: 0 for pred in DECISIONS} for gold in DECISIONS}
    positives = negatives = reviews = false_accept_review = accepted = 0
    wrong_selected = top1_correct = match_cases = candidate_miss = 0
    correct_row_accepts = wrong_row_accepts = 0
    for case, pred in zip(cases, predictions, strict=True):
        gold, decision = case["decision"], pred["decision"]
        matrix[gold][decision] += 1
        accepted += decision == "matched"
        positives += gold == "matched"
        negatives += gold == "unmatched"
        reviews += gold == "review"
        false_accept_review += gold == "review" and decision == "matched"
        if gold == "matched":
            match_cases += 1
            candidate_miss += pred["top_row_key"] not in case["gold_row_keys"]
            top1_correct += pred["top_row_key"] in case["gold_row_keys"]
            if decision == "matched":
                if pred["top_row_key"] in case["gold_row_keys"]:
                    correct_row_accepts += 1
                else:
                    wrong_selected += 1
                    wrong_row_accepts += 1
    detection_tp = matrix["matched"]["matched"]
    detection_fp = matrix["unmatched"]["matched"]
    detection_precision = detection_tp / (detection_tp + detection_fp) if detection_tp + detection_fp else None
    detection_recall = detection_tp / positives if positives else None
    known_false_accepts = detection_fp + wrong_row_accepts
    precision = correct_row_accepts / (correct_row_accepts + known_false_accepts) if correct_row_accepts + known_false_accepts else None
    recall = correct_row_accepts / positives if positives else None
    return {
        "confusion_matrix": matrix,
        "known_case_precision": precision,
        "known_case_recall": recall,
        "known_case_f1": (2 * precision * recall / (precision + recall)
                          if precision is not None and recall is not None and precision + recall else None),
        "verified_different_fpr": detection_fp / negatives if negatives else None,
        "matched_detection_precision": detection_precision,
        "matched_detection_recall": detection_recall,
        "correct_row_match_count": correct_row_accepts,
        "known_false_accept_count_including_wrong_au_row": known_false_accepts,
        "review_gold_unknown_count": reviews,
        "review_unknown_accept_count": false_accept_review,
        "accepted_count": accepted,
        "wrong_au_sku_selected_after_matched_detection": wrong_selected,
        "gold_matched_top1_row_accuracy_before_threshold": top1_correct / match_cases if match_cases else None,
        "gold_matched_candidate_miss_count": candidate_miss,
    }


def select_threshold(dev_cases: list[dict[str, Any]], raw_predictions: list[dict[str, Any]]) -> dict[str, Any]:
    grid = []
    for threshold in THRESHOLDS:
        predictions = [{**p, "decision": "matched" if p["score"] >= threshold else "unmatched"}
                       for p in raw_predictions]
        m = metrics(dev_cases, predictions)
        grid.append({"threshold": threshold, **m})
    safe = [x for x in grid if x["known_case_precision"] is not None and x["known_case_precision"] >= .99]
    if safe:
        chosen = max(safe, key=lambda x: (x["known_case_recall"] or 0, x["threshold"]))
        criterion = "max_recall_among_dev_thresholds_with_known_case_precision_at_least_0.99"
        status = "dev_precision_target_met"
    else:
        chosen = max(grid, key=lambda x: (x["known_case_f1"] or -1, x["threshold"]))
        criterion = "best_dev_known_case_f1_because_precision_0.99_target_not_met"
        status = "hold_precision_target_not_met"
    return {"threshold": chosen["threshold"], "selection_status": status,
            "selection_criterion": criterion,
            "claim": "development-set selection only; no population-level precision guarantee",
            "selected_dev_metrics": chosen, "grid": grid}


def json_write_new(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs-dir", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--model", choices=MODELS, required=True)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--au-arrays", type=Path, default=DEFAULT_AU_ARRAYS)
    args = parser.parse_args()
    if args.threads != 2 or args.batch_size != 8:
        parser.error("fair comparison requires --threads 2 --batch-size 8")
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    cases_path = args.inputs_dir / "cases.jsonl"
    eligibility_path = args.inputs_dir / "eligibility.jsonl"
    au_eligibility_path = args.inputs_dir / "au_eligibility.jsonl"
    input_snapshot_pre = collect_input_snapshot(args.inputs_dir, args.labels, args.au_arrays)
    manifest_check = verify_input_manifests(args.inputs_dir, args.labels, args.au_arrays, input_snapshot_pre)
    cases = load_context(args.inputs_dir, args.au_arrays)
    eligibility_by_id = {row["case_id"]: row for row in read_jsonl(eligibility_path)}
    au_eligibility_by_id = {str(row["au_product_id"]): row for row in read_jsonl(au_eligibility_path)}
    if set(eligibility_by_id) != {case["case_id"] for case in cases}:
        parser.error("eligibility case IDs must match the fixed input case set")
    if not cases:
        parser.error("no annotated cases")
    module_name, class_name, default_model_dir = MODELS[args.model]
    output_parent = args.output.parent
    output_parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    model = getattr(importlib.import_module(module_name), class_name)(
        args.model_dir or ROOT / ".deps" / default_model_dir, threads=args.threads)
    load_seconds = time.perf_counter() - started

    result_rows: dict[str, list[dict[str, Any]]] = {"sku": [], "title-sku": []}
    timing: dict[str, Any] = {}
    token_audits: dict[str, Any] = {}
    for mode in result_rows:
        all_texts = []
        query_texts = []
        candidate_texts = []
        for case in cases:
            q, ct = model_input_texts(case, mode)
            query_texts.append(q)
            all_texts.append(q)
            candidate_texts.append(ct)
            all_texts.extend(ct)
        unique = list(dict.fromkeys(all_texts))
        started = time.perf_counter()
        vectors = model.encode(unique, batch_size=args.batch_size)
        elapsed = time.perf_counter() - started
        lookup = {text: i for i, text in enumerate(unique)}
        for case, query, candidate_texts_for_case in zip(cases, query_texts, candidate_texts, strict=True):
            scores = vectors[lookup[query]] @ vectors[[lookup[x] for x in candidate_texts_for_case]].T
            order = sorted(range(len(scores)), key=lambda i: (-float(scores[i]), i))
            top = order[0]
            result_rows[mode].append({
                "case_id": case["case_id"], "dossier_id": case["dossier_id"],
                "pair_id": case["pair_id"], "source_url": case["rakuten_url"],
                "sku_record_key": case["rakuten_sku_key"],
                "source_row_index": case["rakuten_source_row_index"],
                "rakuten_raw_sha256": case["rakuten_raw_sha256"],
                "score": float(scores[top]),
                "top_row_key": case["candidates"][top]["row_key"],
                "top10": [{"row_key": case["candidates"][i]["row_key"], "score": float(scores[i])}
                          for i in order[:10]],
            })
        timing[mode] = {"seconds": elapsed, "input_text_count": len(all_texts),
                        "unique_input_text_count": len(unique), "deduplicated_texts": len(all_texts) - len(unique),
                        "normalized_distinct_input_text_count": len({
                            re.sub(r"\s+", " ", unicodedata.normalize("NFKC", x)).strip().casefold()
                            for x in all_texts})}
        if mode == "title-sku":
            max_length = getattr(model, "MAX_LENGTH", getattr(model, "max_length", None))
            if max_length is None or not hasattr(model, "token_lengths"):
                raise RuntimeError("Backend must expose MAX_LENGTH and token_lengths for truncation audit")
            lengths = model.token_lengths(all_texts)
            prefixes = []
            for case in cases:
                prefixes.append(case["query_title_only"] + " /")
                prefixes.extend(candidate["title_only"] + " /" for candidate in case["candidates"])
            prefix_lengths = model.token_lengths(prefixes)
            token_audits[mode] = {
                "max_length": max_length,
                "model_input_count": len(all_texts),
                "over_max_length_count": int((lengths > max_length).sum()),
                "sku_starts_at_or_after_truncation_boundary_count": int((prefix_lengths >= max_length).sum()),
                "over_limit_input_indices": [int(i) for i, n in enumerate(lengths) if n > max_length],
                "prefix_token_length_at_or_after_limit_indices": [int(i) for i, n in enumerate(prefix_lengths) if n >= max_length],
                "note": "Full title-plus-SKU strings are retained as model input; right truncation may remove the appended SKU in listed cases.",
            }

    # Gold labels enter the evaluation only after all prediction rows and token
    # audits above have been produced.
    metric_cases = join_labels(cases, args.labels)
    dev_idxs = [i for i, c in enumerate(cases) if c["split"] == "dev"]
    test_idxs = [i for i, c in enumerate(cases) if c["split"] == "test"]
    if not dev_idxs or not test_idxs:
        parser.error("both dev and test cases are required")
    modes_result = {}
    for mode, rows in result_rows.items():
        dev_cases = [metric_cases[i] for i in dev_idxs]
        dev_raw = [rows[i] for i in dev_idxs]
        selected = select_threshold(dev_cases, dev_raw)
        test_cases = [metric_cases[i] for i in test_idxs]
        threshold = selected["threshold"]
        test_preds = [{**rows[i], "decision": "matched" if rows[i]["score"] >= threshold else "unmatched"}
                      for i in test_idxs]
        modes_result[mode] = {
            "threshold_selection_dev_only": selected,
            "frozen_test_threshold": threshold,
            "test_metrics": metrics(test_cases, test_preds),
            "test_rows": [{**p, "decision": p["decision"]} for p in test_preds],
        }
    # Hash every actual model file, separately from the pinned metadata manifest.
    model_dir = args.model_dir or ROOT / ".deps" / default_model_dir
    weight_hashes = {}
    for path in sorted(p for p in model_dir.rglob("*") if p.is_file()):
        if path.name in {"manifest.json", "README.md"}:
            continue
        weight_hashes[str(path.relative_to(model_dir))] = sha256(path)
    manifest = HERE / ("model-manifest.json" if args.model == "minilm" else f"manifests/{args.model}.json")
    backend_path = HERE / f"{module_name}.py"
    input_snapshot_post = collect_input_snapshot(args.inputs_dir, args.labels, args.au_arrays)
    if input_snapshot_post != input_snapshot_pre:
        raise RuntimeError("A frozen evaluation input changed while the model was running; refusing to write results")
    input_hashes = {
        "annotation_files": input_snapshot_pre["annotation_files"],
        "annotation_manifests": input_snapshot_pre["annotation_manifests"],
        "dossiers": input_snapshot_pre["dossiers"],
        "labels": input_snapshot_pre["labels"],
        "labels_manifests": input_snapshot_pre["labels_manifests"],
        "au_product_sku_arrays": input_snapshot_pre["au_product_sku_arrays"],
        "au_product_sku_arrays_manifest": input_snapshot_pre["au_product_sku_arrays_manifest"],
    }
    result = {
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "label_source": "Luna-annotated semantic labels; machine annotation, not human gold",
        "model": args.model, "model_manifest_sha256": sha256(manifest),
        "model_weight_sha256": weight_hashes,
        "model_metadata": json.loads(manifest.read_text(encoding="utf-8")),
        "code_sha256": {str(Path(__file__).name): sha256(Path(__file__)),
                        backend_path.name: sha256(backend_path)},
        "input_sha256": input_hashes,
        "input_integrity": {"pre_run_snapshot": input_snapshot_pre,
                             "post_run_snapshot": input_snapshot_post,
                             "unchanged_during_run": True,
                             "manifest_checks": "annotation outputs, every dossier, shard cases, labels, and labels-to-input manifest linkage verified",
                             **manifest_check},
        "platform": platform.platform(), "python": platform.python_version(),
        "threads": args.threads, "batch_size": args.batch_size,
        "cold_load_seconds": load_seconds, "inference_timing": timing,
        "token_length_audit": token_audits,
        "unique_rakuten_sku_references": len({c["rakuten_sku_key"] for c in cases}),
        "rakuten_sku_reference_count": len(cases),
        "rakuten_sku_re_reference_count": len(cases) - len({c["rakuten_sku_key"] for c in cases}),
        "unique_au_sku_row_references": len({candidate["row_key"] for c in cases for candidate in c["candidates"]}),
        "au_sku_row_reference_count_across_queries": sum(len(c["candidates"]) for c in cases),
        "au_sku_row_re_reference_count_across_queries": (
            sum(len(c["candidates"]) for c in cases) -
            len({candidate["row_key"] for c in cases for candidate in c["candidates"]})),
        "unique_au_rows_sold_out": len({candidate["row_key"] for c in cases for candidate in c["candidates"]
                                         if (candidate.get("stock") or {}).get("isSoldOut") is True}),
        "case_count": len(cases), "dev_case_count": len(dev_idxs), "test_case_count": len(test_idxs),
        "runtime_versions": {},
        "model_input_contract": {
            "sku_only": "raw query option values and raw AU axes only",
            "title_sku": "raw query title plus options; AU product title plus raw axes",
            "excluded_from_model_input": ["price", "stock", "availability", "URL", "gold labels", "annotated attributes", "evidence"],
            "candidate_pool": "complete AU SKU array for the dossier's fixed AU product, for every query",
            "routing": "no URL-based AU product routing; fixed pair/dossier context only",
            "stock_policy": "stock retained in candidate records for separate output eligibility; no ranking or semantic label effect",
        },
        "interpretation_limits": [
            "Luna annotations are machine-annotated labels, not human-verified gold.",
            "Review labels have unknown semantic truth and are excluded from known-case precision denominators; accepted review cases are reported separately.",
            "Wrong AU SKU selection is reported separately from matched detection.",
            "Thresholds are selected on dev only and frozen for test; dev precision is not a population guarantee.",
            "Product price is at AU product grain and is never represented as a per-SKU price.",
        ],
        "modes": modes_result,
        "process_peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
    }
    for package in ("numpy", "onnxruntime", "tokenizers", "torch", "transformers"):
        try:
            result["runtime_versions"][package] = version(package)
        except PackageNotFoundError:
            pass
    args.output.mkdir(parents=True, exist_ok=False)
    for mode, rows in result_rows.items():
        json_write_new(args.output / f"predictions-{mode}.json", {"mode": mode, "rows": rows})
        eligibility_rows = []
        threshold = modes_result[mode]["frozen_test_threshold"]
        for case, prediction in zip(cases, rows, strict=True):
            selected = next(c for c in case["candidates"] if c["row_key"] == prediction["top_row_key"])
            rakuten_eligibility = eligibility_by_id[case["case_id"]]["rakuten"]
            au_eligibility = au_eligibility_by_id[case["product_id"]]
            eligibility_rows.append({
                "case_id": case["case_id"],
                "semantic_decision": "matched" if prediction["score"] >= threshold else "unmatched",
                "proposed_au_row_key": prediction["top_row_key"], "au_stock_raw": selected.get("stock"),
                "au_product_price_jpy_once": au_eligibility.get("au_product_price_jpy_once"),
                "au_price_grain": "product page price; not a per-SKU price",
                "rakuten_price_jpy": rakuten_eligibility.get("price_jpy"),
                "rakuten_availability_raw": rakuten_eligibility.get("availability"),
                "eligibility_policy": "separate downstream output; no price or stock value changes semantic ranking",
            })
        json_write_new(args.output / f"eligibility-{mode}.json", {"mode": mode, "rows": eligibility_rows})
    json_write_new(args.output / "summary.json", result)
    print(json.dumps({"output": str(args.output), "model": args.model,
                      "test": {m: v["test_metrics"] for m, v in modes_result.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
