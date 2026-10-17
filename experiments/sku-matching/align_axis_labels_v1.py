"""Align AU and Rakuten SKU axis labels with a pinned local embedding model.

Only raw axis labels are passed to the encoder. Values and source evidence are
carried through as input provenance only and never participate in matching.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TIE_TOLERANCE = 1e-7


def _read_records(path: Path) -> tuple[list[dict[str, Any]], str]:
    raw = path.read_bytes()
    records: list[dict[str, Any]] = []
    seen_pairs: set[str] = set()
    for line_no, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_no}: invalid JSON: {exc}") from exc
        _validate_record(record, line_no)
        pair_id = record["pair_id"]
        if pair_id in seen_pairs:
            raise ValueError(f"line {line_no}: duplicate pair_id {pair_id!r}")
        seen_pairs.add(pair_id)
        records.append(record)
    return records, hashlib.sha256(raw).hexdigest()


def _validate_record(record: Any, line_no: int = 0) -> None:
    prefix = f"line {line_no}: " if line_no else ""
    if not isinstance(record, dict):
        raise ValueError(prefix + "record must be an object")
    if not isinstance(record.get("pair_id"), str) or not record["pair_id"].strip():
        raise ValueError(prefix + "pair_id must be a non-empty string")
    for side in ("au_axes", "rakuten_axes"):
        axes = record.get(side)
        if not isinstance(axes, list):
            raise ValueError(prefix + f"{side} must be an array")
        ids: set[str] = set()
        for index, axis in enumerate(axes):
            if not isinstance(axis, dict):
                raise ValueError(prefix + f"{side}[{index}] must be an object")
            axis_id = axis.get("axis_id")
            if not isinstance(axis_id, str) or not axis_id.strip():
                raise ValueError(prefix + f"{side}[{index}].axis_id must be a non-empty string")
            if axis_id in ids:
                raise ValueError(prefix + f"{side} has duplicate axis_id {axis_id!r}")
            ids.add(axis_id)
            if "label" not in axis or not isinstance(axis["label"], str):
                raise ValueError(prefix + f"{side}[{index}].label must be a string")


def align_pair(record: dict[str, Any], scores: Callable[[str, str], float]) -> dict[str, Any]:
    """Pure alignment core; scorer receives only two raw label strings."""
    _validate_record(record)
    au = record["au_axes"]
    rk = record["rakuten_axes"]
    result: dict[str, Any] = {
        "pair_id": record["pair_id"],
        "au_axis_count": len(au),
        "rakuten_axis_count": len(rk),
        "mappings": [],
        "asymmetry_candidates": [],
        "ambiguous_rakuten_axes": [],
        "collision_definition": "Two or more AU axes share the same stable top1 Rakuten axis; co-leading tie candidates alone are not a collision.",
    }
    if len(au) == 0:
        result.update(status="skipped", skip_reason="zero_au_axes")
        return result
    if len(au) > len(rk):
        result.update(status="skipped", skip_reason="au_axis_count_exceeds_rakuten")
        return result
    result["status"] = "eligible"
    possible_targets: set[str] = set()
    ambiguous_relations: dict[str, set[str]] = {}
    for source in au:
        label = source["label"]
        base: dict[str, Any] = {
            "au_axis_id": source["axis_id"], "au_label": label or None,
            "semantic_equivalence_verified": False,
        }
        if not label.strip():
            base.update(status="unknown_label", best=None, second=None, margin=None,
                        tied_candidates=[], scores=[])
            baseline = [target for target in rk if target["label"] == label and label.strip()]
            base["exact_raw_label_baseline"] = _baseline(baseline)
            result["mappings"].append(base)
            continue
        candidates = [target for target in rk if target["label"].strip()]
        ranked = [(target, float(scores(label, target["label"]))) for target in candidates]
        if any(not math.isfinite(score) for _, score in ranked):
            raise ValueError("scorer returned a non-finite cosine score")
        # Python's stable sort preserves original Rakuten axis order on exact ties.
        ranked.sort(key=lambda item: -item[1])
        if ranked:
            target, top_score = ranked[0]
            tied = [t for t, score in ranked if abs(top_score - score) <= TIE_TOLERANCE]
            second = ranked[1] if len(ranked) > 1 else None
            base.update(
                status="mapped", best={"axis_id": target["axis_id"], "label": target["label"], "score": top_score},
                second=(None if second is None else {"axis_id": second[0]["axis_id"], "label": second[0]["label"], "score": second[1]}),
                margin=(None if second is None else top_score - second[1]),
                tied_candidates=[{"axis_id": t["axis_id"], "label": t["label"],
                                  "score": next(s for c, s in ranked if c is t)} for t in tied],
                scores=[{"axis_id": t["axis_id"], "label": t["label"], "score": score}
                        for t, score in ranked],
                tie=len(tied) > 1,
            )
            possible_targets.update(t["axis_id"] for t in tied)
            if len(tied) > 1:
                for tied_target in tied:
                    ambiguous_relations.setdefault(tied_target["axis_id"], set()).add(source["axis_id"])
        else:
            base.update(status="unknown_label", best=None, second=None, margin=None,
                        tied_candidates=[], scores=[])
        base["exact_raw_label_baseline"] = _baseline(
            [target for target in rk if target["label"] == label]
        )
        result["mappings"].append(base)
    selected: dict[str, list[str]] = {}
    for mapping in result["mappings"]:
        if mapping.get("best"):
            selected.setdefault(mapping["best"]["axis_id"], []).append(mapping["au_axis_id"])
    for mapping in result["mappings"]:
        chosen = mapping.get("best")
        collision_ids = selected.get(chosen["axis_id"], []) if chosen else []
        mapping["collision"] = len(collision_ids) > 1
        mapping["collision_au_axis_ids"] = collision_ids if len(collision_ids) > 1 else []
        mapping["collision_basis"] = "stable_top1"
    result["asymmetry_candidates"] = [
        {"axis_id": target["axis_id"], "label": target["label"] or None,
         "status": "candidate_only", "semantic_equivalence_verified": False}
        for target in rk if target["axis_id"] not in possible_targets
    ]
    result["ambiguous_rakuten_axes"] = [
        {"axis_id": target["axis_id"], "label": target["label"] or None,
         "au_axis_ids": [source["axis_id"] for source in au
                         if source["axis_id"] in ambiguous_relations[target["axis_id"]]],
         "semantic_equivalence_verified": False}
        for target in rk if target["axis_id"] in ambiguous_relations
    ]
    return result


def _baseline(matches: list[dict[str, Any]]) -> dict[str, Any]:
    if not matches:
        return {"status": "no_exact_match", "axis_id": None, "candidate_axis_ids": []}
    return {"status": "tie" if len(matches) > 1 else "matched",
            "axis_id": matches[0]["axis_id"],
            "candidate_axis_ids": [item["axis_id"] for item in matches]}


def align_records(records: list[dict[str, Any]], embeddings: dict[str, Iterable[float]]) -> list[dict[str, Any]]:
    """Apply cosine scores to precomputed label vectors (numpy imported lazily)."""
    import numpy as np
    vectors = {label: np.asarray(vector, dtype=np.float64) for label, vector in embeddings.items()}
    norms = {}
    for label, vector in vectors.items():
        if vector.ndim != 1 or not np.isfinite(vector).all():
            raise ValueError(f"invalid embedding for label {label!r}")
        norm = float(np.linalg.norm(vector))
        if not math.isfinite(norm) or norm <= 0:
            raise ValueError(f"zero or invalid embedding norm for label {label!r}")
        norms[label] = vector / norm
    def cosine(left: str, right: str) -> float:
        value = float(np.dot(norms[left], norms[right]))
        if not math.isfinite(value):
            raise ValueError("non-finite cosine score")
        return value
    return [align_pair(record, cosine) for record in records]


def _model(model_name: str, model_dir: Path, threads: int):
    if model_name == "minilm":
        from embeddings import EmbeddingModel
        manifest_path = HERE / "model-manifest.json"
        model = EmbeddingModel(model_dir, threads=threads)
    else:
        from backend_ruri import Model
        manifest_path = HERE / "manifests/ruri.json"
        model = Model(model_dir, threads=threads)
    return model, json.loads(manifest_path.read_text(encoding="utf-8"))


def run(input_path: Path, output_dir: Path, model_name: str = "minilm", model_dir: Path | None = None,
        threads: int = 2, batch_size: int = 8) -> dict[str, Any]:
    start = time.monotonic()
    input_path = input_path.resolve()
    output_dir = output_dir.resolve()
    if input_path == output_dir or input_path in output_dir.parents:
        raise ValueError("input and output paths must be distinct")
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists; refusing overwrite: {output_dir}")
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    if threads < 1 or batch_size < 1:
        raise ValueError("threads and batch-size must be at least 1")
    records, input_sha = _read_records(input_path)
    predictions = [None] * len(records)
    eligible_indices = []
    labels: set[str] = set()
    for i, record in enumerate(records):
        au, rk = record["au_axes"], record["rakuten_axes"]
        if not au or len(au) > len(rk):
            continue
        eligible_indices.append(i)
        labels.update(axis["label"] for axis in au + rk if axis["label"].strip())
    selected_model_dir = model_dir or ROOT / ".deps" / ("sku-matching-model" if model_name == "minilm" else "sku-ruri-model")
    model, selected_manifest = _model(model_name, selected_model_dir, threads)
    unique_labels = sorted(labels)
    lengths = model.token_lengths(unique_labels)
    max_length = int(getattr(model, "MAX_LENGTH"))
    if len(lengths) != len(unique_labels) or any(int(length) > max_length for length in lengths):
        raise ValueError(f"axis label exceeds model max length {max_length}")
    label_token_lengths = [int(length) for length in lengths]
    vectors = model.encode(unique_labels, batch_size=batch_size)
    if len(vectors) != len(unique_labels):
        raise ValueError("encoder returned a different vector count than label count")
    embedding_map = dict(zip(unique_labels, vectors, strict=True))
    for i, record in enumerate(records):
        if i in eligible_indices:
            predictions[i] = align_records([record], embedding_map)[0]
        else:
            predictions[i] = align_pair(record, lambda _a, _b: 0.0)
    # Output creation is deliberately last, after validation and model execution.
    output_dir.mkdir(parents=True, exist_ok=False)
    predictions_path = output_dir / "predictions.jsonl"
    with predictions_path.open("w", encoding="utf-8") as stream:
        for prediction in predictions:
            stream.write(json.dumps(prediction, ensure_ascii=False, sort_keys=True) + "\n")
    predictions_sha = hashlib.sha256(predictions_path.read_bytes()).hexdigest()
    script_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    both_manifests = {
        "minilm": json.loads((HERE / "model-manifest.json").read_text(encoding="utf-8")),
        "ruri": json.loads((HERE / "manifests/ruri.json").read_text(encoding="utf-8")),
    }
    eligible = [p for p in predictions if p["status"] == "eligible"]
    mappings = [m for p in eligible for m in p["mappings"]]
    exact_unique = sum(m["exact_raw_label_baseline"]["status"] == "matched" for m in mappings)
    exact_ties = sum(m["exact_raw_label_baseline"]["status"] == "tie" for m in mappings)
    agreement_denominator = exact_unique
    agreement_numerator = sum(
        m.get("best") is not None
        and m["exact_raw_label_baseline"]["status"] == "matched"
        and m["exact_raw_label_baseline"].get("axis_id") == m["best"]["axis_id"]
        for m in mappings
    )
    installed_versions = {}
    for package in ("numpy", "onnxruntime", "tokenizers", "torch", "transformers"):
        try:
            installed_versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            installed_versions[package] = None
    summary = {
        "schema_version": "sku-axis-label-alignment-v1", "model": model_name,
        "model_source": selected_manifest.get("repo"), "model_revision": selected_manifest.get("revision"),
        "model_manifests": both_manifests,
        "model_manifest_sha256": {"minilm": hashlib.sha256((HERE / "model-manifest.json").read_bytes()).hexdigest(),
                                  "ruri": hashlib.sha256((HERE / "manifests/ruri.json").read_bytes()).hexdigest()},
        "input_sha256": input_sha, "script_sha256": script_sha,
        "elapsed_total_seconds": time.monotonic() - start,
        "elapsed_scope": "run start through predictions.jsonl serialization and SHA-256; excludes summary.json serialization",
        "python_version": platform.python_version(), "installed_package_versions": installed_versions,
        "threads": threads, "batch_size": batch_size,
        "device": "cpu", "label_only": True, "encoded_unique_label_count": len(unique_labels),
        "label_token_lengths": label_token_lengths,
        "label_max_token_length": max(label_token_lengths, default=0),
        "model_max_token_length": max_length,
        "encoded_labels_exceeding_max_length": 0,
        "input_pairs": len(records), "eligible_pairs": len(eligible),
        "skipped_pairs": len(records) - len(eligible),
        "skipped_reasons": _skip_counts(predictions),
        "mapped_axis_count": sum(m["status"] == "mapped" for m in mappings),
        "collision_count": sum(bool(m.get("collision")) for m in mappings),
        "collision_definition": "Two or more AU axes share the same stable top1 Rakuten axis; co-leading tie candidates alone are not a collision.",
        "tie_count": sum(bool(m.get("tie")) for m in mappings),
        "tie_target_count": sum(len(p["ambiguous_rakuten_axes"]) for p in eligible),
        "tie_target_au_association_count": sum(len(a["au_axis_ids"]) for p in eligible for a in p["ambiguous_rakuten_axes"]),
        "ambiguous_pair_count": sum(bool(p["ambiguous_rakuten_axes"]) for p in eligible),
        "asymmetry_candidate_count": sum(len(p["asymmetry_candidates"]) for p in eligible),
        "exact_baseline_matched_axis_count": exact_unique + exact_ties,
        "exact_baseline_unique_match_axis_count": exact_unique,
        "exact_baseline_tie_axis_count": exact_ties,
        "unknown_label_axis_count": sum(m["status"] == "unknown_label" for m in mappings),
        "exact_baseline_agreement_count": agreement_numerator,
        "exact_baseline_agreement_denominator": agreement_denominator,
        "candidate_only": True, "automatic_adoption_allowed": False,
        "predictions_file": "predictions.jsonl",
        "predictions_sha256": predictions_sha,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def _skip_counts(predictions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for prediction in predictions:
        reason = prediction.get("skip_reason")
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model", choices=("minilm", "ruri"), default="minilm")
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args(argv)
    try:
        summary = run(args.input, args.output_dir, args.model, args.model_dir, args.threads, args.batch_size)
    except (ValueError, FileExistsError, FileNotFoundError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
