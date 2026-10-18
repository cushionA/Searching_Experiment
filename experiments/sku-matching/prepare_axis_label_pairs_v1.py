"""Prepare source-only AU/Rakuten axis-label pairs for correspondence experiments.

The output intentionally contains axis labels and provenance only. It never copies
SKU values, product text, prior decisions, or evaluation labels.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "sku-axis-label-pairs-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Read JSONL while retaining exact source-line hashes for provenance."""
    rows: list[dict[str, Any]] = []
    line_hashes: list[str] = []
    with path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, 1):
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"JSONL row must be an object at {path}:{line_number}")
            rows.append(row)
            line_hashes.append(hashlib.sha256(raw_line.rstrip(b"\r\n")).hexdigest())
    return rows, line_hashes


def _id(value: Any, *, what: str) -> str:
    if value is None or str(value) == "":
        raise ValueError(f"missing {what}")
    return str(value)


def _line_index(rows: list[dict[str, Any]], source_path: Path, identity: str) -> dict[str, tuple[dict[str, Any], int]]:
    index: dict[str, tuple[dict[str, Any], int]] = {}
    # read_jsonl drops blank lines, so get physical line numbers independently.
    with source_path.open("rb") as stream:
        line_numbers = [n for n, line in enumerate(stream, 1) if line.strip()]
    if len(rows) != len(line_numbers):
        raise ValueError(f"JSONL line accounting mismatch: {source_path}")
    for row, line_number in zip(rows, line_numbers, strict=True):
        key = _id(row.get(identity), what=f"{identity} in {source_path}:{line_number}")
        if key in index:
            raise ValueError(f"duplicate {identity}: {key}")
        index[key] = (row, line_number)
    return index


def _source_ref(
    *,
    input_path: Path,
    input_sha256: str,
    line_sha256: str,
    line_number: int,
    json_pointer: str,
    label: str,
    span: Any,
) -> dict[str, Any]:
    ref: dict[str, Any] = {
        "input_file": input_path.as_posix(),
        "input_sha256": input_sha256,
        "line_sha256": line_sha256,
        "line_number": line_number,
        "json_pointer": json_pointer,
        "label_quote": label,
    }
    # Keep only source-span coordinates and the source label quote; never copy
    # adjacent source text, values, body fragments, or descriptions.
    if isinstance(span, dict):
        raw_ref = {
            key: span[key]
            for key in ("raw_file", "sha256", "locator", "start", "end")
            if key in span and isinstance(span[key], (str, int, float, bool, type(None)))
        }
        quote = span.get("quote")
        if isinstance(quote, str):
            raw_ref["label_quote"] = quote
        if raw_ref:
            ref["raw_label_span"] = raw_ref
    return ref


def _label(axis: dict[str, Any], field: str) -> str:
    if field not in axis:
        raise ValueError(f"missing axis label field: {field}")
    value = axis[field]
    if not isinstance(value, str):
        raise ValueError(f"axis label field must be a string: {field}")
    return value


def _validate_span_quote(axis: dict[str, Any], *, label_field: str, span_field: str, where: str) -> None:
    span = axis.get(span_field)
    if isinstance(span, dict) and "quote" in span and span["quote"] != axis[label_field]:
        raise ValueError(f"source span quote differs from {label_field} at {where}")


def _axis_sequence(axes: Any, field: str, span_field: str, *, where: str) -> tuple[str, ...]:
    if not isinstance(axes, list):
        raise ValueError("axis collection must be a list")
    if any(not isinstance(axis, dict) for axis in axes):
        raise ValueError("axis entries must be objects")
    labels = []
    for position, axis in enumerate(axes):
        label = _label(axis, field)
        _validate_span_quote(axis, label_field=field, span_field=span_field, where=f"{where}/{position}")
        labels.append(label)
    return tuple(labels)


def prepare_pairs(products_path: Path, cases_path: Path, membership_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    products, product_line_hashes = read_jsonl(products_path)
    cases, case_line_hashes = read_jsonl(cases_path)
    membership, _ = read_jsonl(membership_path)
    products_sha256 = sha256_file(products_path)
    cases_sha256 = sha256_file(cases_path)
    membership_sha256 = sha256_file(membership_path)

    product_index = _line_index(products, products_path, "dossier_id")
    case_index = _line_index(cases, cases_path, "case_id")
    membership_index = _line_index(membership, membership_path, "case_id")
    au_product_to_dossier: dict[str, str] = {}
    for dossier_id, (product, _) in product_index.items():
        au_product = product.get("au_product")
        au_product_id = _id(au_product.get("product_id") if isinstance(au_product, dict) else None,
                            what=f"AU product_id for dossier {dossier_id}")
        if au_product_id in au_product_to_dossier:
            raise ValueError(f"duplicate AU product_id: {au_product_id}")
        au_product_to_dossier[au_product_id] = dossier_id
    product_line_hash_by_dossier = {
        key: product_line_hashes[i]
        for i, key in enumerate(row["dossier_id"] for row in products)
    }
    case_line_hash_by_id = {
        key: case_line_hashes[i]
        for i, key in enumerate(row["case_id"] for row in cases)
    }

    membership_case_ids = set(membership_index)
    unknown_members = sorted(membership_case_ids - set(case_index))
    if unknown_members:
        raise ValueError(f"membership references unknown case IDs ({len(unknown_members)})")

    cases_by_dossier: dict[str, list[tuple[dict[str, Any], int]]] = defaultdict(list)
    for case_id, (case, line_number) in case_index.items():
        dossier_id = _id(case.get("dossier_id"), what=f"dossier_id for case {case_id}")
        if dossier_id not in product_index:
            raise ValueError(f"case references unknown dossier_id: {dossier_id}")
        product = product_index[dossier_id][0]
        expected_au_id = str(product["au_product"]["product_id"])
        if str(case.get("au_product_id")) != expected_au_id:
            raise ValueError(f"case AU product binding differs for case {case_id}")
        cases_by_dossier[dossier_id].append((case, line_number))

    selected_dossiers: set[str] = set()
    for case_id, (member, _) in membership_index.items():
        case = case_index[case_id][0]
        if str(member.get("dossier_id")) != str(case.get("dossier_id")):
            raise ValueError(f"membership dossier binding differs for case {case_id}")
        selected_dossiers.add(str(case["dossier_id"]))

    output_rows: list[dict[str, Any]] = []
    missing_au_labels = 0
    missing_rakuten_labels = 0
    for dossier_id, (product, product_line_number) in product_index.items():
        pair_cases = cases_by_dossier.get(dossier_id, [])
        if not pair_cases:
            raise ValueError(f"product has no source cases: {dossier_id}")
        au_rows = product.get("au_rows")
        if not isinstance(au_rows, list) or not au_rows:
            raise ValueError(f"product has no AU source rows: {dossier_id}")
        au_sequences = [
            _axis_sequence(row.get("axes"), "axis_name", "axis_name_span", where=f"{dossier_id}/au_rows/{row_pos}/axes")
            for row_pos, row in enumerate(au_rows)
        ]
        if any(sequence != au_sequences[0] for sequence in au_sequences[1:]):
            raise ValueError(f"AU axis label/order differs across rows for dossier {dossier_id}")

        # Use each source position as the identity. Equal labels at different
        # positions remain separate axes by construction.
        au_axes: list[dict[str, Any]] = []
        source_product_sha = product_line_hash_by_dossier[dossier_id]
        for axis_pos, axis_name in enumerate(au_sequences[0]):
            source_axis = au_rows[0]["axes"][axis_pos]
            if not axis_name.strip():
                missing_au_labels += 1
            au_axes.append({
                "axis_id": f"au-position-{axis_pos}",
                "label": axis_name,
                "source_ref": _source_ref(
                    input_path=products_path,
                    input_sha256=products_sha256,
                    line_sha256=source_product_sha,
                    line_number=product_line_number,
                    json_pointer=f"/au_rows/0/axes/{axis_pos}/axis_name",
                    label=axis_name,
                    span=source_axis.get("axis_name_span"),
                ),
            })

        canonical_rak_axes: tuple[tuple[str, str, str], ...] | None = None
        canonical_rak_sources: list[tuple[dict[str, Any], int, int, str]] = []
        for case, case_line_number in pair_cases:
            axes = case.get("rakuten_selected", {}).get("axes") if isinstance(case.get("rakuten_selected"), dict) else None
            if not isinstance(axes, list):
                raise ValueError(f"Rakuten axes missing for case {case.get('case_id')}")
            identities: list[tuple[str, str, str]] = []
            seen_axis_ids: set[str] = set()
            for axis_pos, axis in enumerate(axes):
                if not isinstance(axis, dict):
                    raise ValueError(f"Rakuten axis is not an object for case {case.get('case_id')}")
                axis_index = axis.get("axis_index")
                axis_key = _id(axis.get("axis_key"), what=f"Rakuten axis_key for case {case.get('case_id')}")
                axis_id = f"rakuten-index-{axis_index}-key-{axis_key}"
                if axis_id in seen_axis_ids:
                    raise ValueError(f"duplicate Rakuten axis identity for case {case.get('case_id')}")
                seen_axis_ids.add(axis_id)
                label = _label(axis, "axis_label")
                _validate_span_quote(axis, label_field="axis_label", span_field="axis_label_span",
                                     where=f"{case.get('case_id')}/rakuten_selected/axes/{axis_pos}")
                identities.append((str(axis_index), axis_key, label))
            identity_tuple = tuple(identities)
            if canonical_rak_axes is None:
                canonical_rak_axes = identity_tuple
                canonical_rak_sources = [
                    (axis, axis_pos, case_line_number, str(case["case_id"]))
                    for axis_pos, axis in enumerate(axes)
                ]
            elif identity_tuple != canonical_rak_axes:
                raise ValueError(f"Rakuten axis identity/labels differ across cases for dossier {dossier_id}")

        assert canonical_rak_axes is not None
        rakuten_axes: list[dict[str, Any]] = []
        for axis, axis_pos, case_line_number, case_id in canonical_rak_sources:
            axis_index = axis.get("axis_index")
            axis_key = str(axis["axis_key"])
            label = _label(axis, "axis_label")
            if not label.strip():
                missing_rakuten_labels += 1
            rakuten_axes.append({
                "axis_id": f"rakuten-index-{axis_index}-key-{axis_key}",
                "label": label,
                "source_ref": _source_ref(
                    input_path=cases_path,
                    input_sha256=cases_sha256,
                    line_sha256=case_line_hash_by_id[case_id],
                    line_number=case_line_number,
                    json_pointer=f"/rakuten_selected/axes/{axis_pos}/axis_label",
                    label=label,
                    span=axis.get("axis_label_span"),
                ),
            })

        case_ids = sorted(str(case["case_id"]) for case, _ in pair_cases)
        source_splits = sorted({str(case.get("split", "")) for case, _ in pair_cases})
        output_rows.append({
            "pair_id": dossier_id,
            "au_axes": au_axes,
            "rakuten_axes": rakuten_axes,
            "selected_dev_subset": dossier_id in selected_dossiers,
            "source_case_count": len(pair_cases),
            "source_case_ids": case_ids,
            "source_splits": source_splits,
        })

    pair_count_by_gate = Counter(
        "au_le_rakuten" if len(row["au_axes"]) <= len(row["rakuten_axes"]) else "au_gt_rakuten"
        for row in output_rows
    )
    stats = {
        "schema_version": SCHEMA_VERSION,
        "input_files": {
            "products": {"path": products_path.as_posix(), "sha256": products_sha256, "line_count": len(products)},
            "cases": {"path": cases_path.as_posix(), "sha256": cases_sha256, "line_count": len(cases)},
            "membership": {"path": membership_path.as_posix(), "sha256": membership_sha256, "line_count": len(membership)},
        },
        "source_pair_count": len(output_rows),
        "selected_dev_subset_pair_count": sum(row["selected_dev_subset"] for row in output_rows),
        "source_case_count": len(cases),
        "pairs_by_axis_count_gate": dict(pair_count_by_gate),
        "missing_au_axis_label_count": missing_au_labels,
        "missing_rakuten_axis_label_count": missing_rakuten_labels,
        "au_unique_label_count": len({axis["label"] for row in output_rows for axis in row["au_axes"] if axis["label"].strip()}),
        "rakuten_unique_label_count": len({axis["label"] for row in output_rows for axis in row["rakuten_axes"] if axis["label"].strip()}),
        "source_split_values": sorted({split for row in output_rows for split in row["source_splits"]}),
        "selected_subset_source_split_values": sorted({
            split for row in output_rows if row["selected_dev_subset"] for split in row["source_splits"]
        }),
        "selected_subset_is_independent_validation": False,
        "model_inference_run": False,
        "semantic_label_completion": False,
        "model_input_fields": ["au_axes[].label", "rakuten_axes[].label"],
        "excluded_from_model_input": ["axis values", "SKU values", "product descriptions", "source bodies", "gold labels", "prior predictions", "grades"],
    }
    return output_rows, stats


def write_new(path: Path, text: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--membership", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="must be a new directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir: Path = args.output_dir
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing output directory: {output_dir}")
    rows, provenance = prepare_pairs(args.products, args.cases, args.membership)
    output_dir.mkdir(parents=True, exist_ok=False)
    write_new(output_dir / "axis-pairs.jsonl", "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    write_new(output_dir / "provenance.json", json.dumps(provenance, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "output_dir": output_dir.as_posix(),
        "source_pair_count": provenance["source_pair_count"],
        "selected_dev_subset_pair_count": provenance["selected_dev_subset_pair_count"],
        "pairs_by_axis_count_gate": provenance["pairs_by_axis_count_gate"],
        "missing_au_axis_label_count": provenance["missing_au_axis_label_count"],
        "missing_rakuten_axis_label_count": provenance["missing_rakuten_axis_label_count"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
