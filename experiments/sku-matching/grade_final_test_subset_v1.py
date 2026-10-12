#!/usr/bin/env python3
"""Score the frozen 40-case final subset after an explicit root GO.

This helper reads machine-reference answer shards only after it verifies that
the selected membership, every prediction variant, and every gate artifact are
bound by an all-predictions/all-gates-frozen receipt. It never changes a model,
threshold, or gate decision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_MEMBERSHIP_SHA256 = "212123b8851168a8895e8a035f8a1a032c173c496b76024b707030efca93778d"
EXPECTED_CASE_COUNT = 40
V2_LABEL_ARCHIVE = ".lab-output/sku-claude-additional-review-20261012-v1/20261011-sku-independent-real-eval-v2.zip"
V3_LABEL_ARCHIVE = ".lab-output/sku-claude-additional-review-20261012-v1/20261012-sku-independent-real-eval-v3.zip"
V2_LABEL_PREFIX = ".lab-output/sku-independent-real-20261011/eval-v2/labels-token/"
V3_LABEL_PREFIX = ".lab-output/sku-independent-real-20261011/eval-v3/labels-token/"
V2_LABEL_ARCHIVE_SHA256 = "6f65d988222c79c758614592340ba28565a51fa8891317724ba354c5fc38b457"
V3_LABEL_ARCHIVE_SHA256 = "52f6faa51127a9c12250a3cdb5eb3d1818a36461a8c09323e481748aeed1f37b"
LABEL_ENVELOPE_KEYS = {"s", "expected", "au_rows", "data_quality_flag", "rationale"}

if str(ROOT / "experiments/sku-matching") not in sys.path:
    sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
from route_mixed_sku_references_v1 import normalize_independent_label, selected_records_from_bytes


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl_bytes(data: bytes, where: str) -> list[dict[str, Any]]:
    values = []
    for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{where}:{number}: expected JSON object")
        values.append(value)
    return values


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return read_jsonl_bytes(path.read_bytes(), str(path))


def unique_index(rows: list[dict[str, Any]], key: str, where: str) -> dict[str, dict[str, Any]]:
    result = {}
    for row in rows:
        value = row.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"missing string {key} in {where}")
        if value in result:
            raise ValueError(f"duplicate {key} in {where}: {value}")
        result[value] = row
    return result


def validate_freeze_receipt(
    receipt_path: Path,
    membership_path: Path,
    prediction_paths: dict[str, Path],
    gate_paths: list[Path],
) -> dict[str, Any]:
    """Validate the freeze contract before any label archive is opened.

    Receipt contract: `freeze_status` is `all_predictions_and_gates_frozen`,
    `independent_test` is true, `membership_sha256` binds the 40-case subset,
    and `frozen_files` maps repo-relative artifact paths to SHA-256.
    """
    receipt = read_json(receipt_path)
    expected_status = receipt.get("freeze_status") == "all_predictions_and_gates_frozen"
    frozen_schema = receipt.get("schema_version") == "final-full-sku-gates-frozen-v1"
    if not expected_status and not frozen_schema:
        raise ValueError("freeze receipt must confirm all predictions and gates are frozen")
    if receipt.get("all_predictions_frozen") is not True or receipt.get("all_gates_frozen") is not True:
        raise ValueError("freeze receipt lacks both frozen-state assertions")
    if receipt.get("independent_test") is not True:
        raise ValueError("freeze receipt must identify the run as independent_test")
    if receipt.get("membership_sha256") != EXPECTED_MEMBERSHIP_SHA256:
        raise ValueError("freeze receipt membership SHA does not match the predeclared 40-case subset")
    grader_pin = receipt.get("grader_code_sha256")
    if grader_pin is not None and grader_pin != sha(Path(__file__).read_bytes()):
        raise ValueError("grader code SHA differs from the reviewed supplemental freeze receipt")
    parser_helper_pin = receipt.get("reference_parser_helper_sha256")
    if parser_helper_pin is not None:
        helper_path = ROOT / "experiments/sku-matching/route_mixed_sku_references_v1.py"
        if not helper_path.is_file() or parser_helper_pin != sha(helper_path.read_bytes()):
            raise ValueError("selected-reference parser helper SHA differs from the reviewed supplemental freeze receipt")
    if sha(membership_path.read_bytes()) != EXPECTED_MEMBERSHIP_SHA256:
        raise ValueError("40-case membership source SHA mismatch")
    pins = receipt.get("frozen_files", receipt.get("output_sha256"))
    if not isinstance(pins, dict) or not pins:
        raise ValueError("freeze receipt frozen_files/output_sha256 map is missing")
    # The final gate runner emits output_sha256 relative to the run directory and
    # a source_snapshot of frozen inputs/code; validate the whole receipt map.
    if "output_sha256" in receipt:
        for rel, expected in receipt["output_sha256"].items():
            path = receipt_path.parent / Path(rel)
            if not path.is_file() or path.is_symlink() or sha(path.read_bytes()) != expected:
                raise ValueError(f"frozen gate output SHA mismatch: {rel}")
    source_snapshot = receipt.get("source_snapshot", {})
    if not isinstance(source_snapshot, dict):
        raise ValueError("freeze receipt source_snapshot must be a mapping")
    source_code_pins = {}
    for recorded, row in source_snapshot.items():
        if not str(recorded).endswith(".py"):
            continue
        path = Path(recorded)
        if not path.is_absolute():
            path = ROOT / path
        expected = row.get("sha256") if isinstance(row, dict) else None
        if not path.is_file() or path.is_symlink() or not expected or sha(path.read_bytes()) != expected:
            raise ValueError(f"frozen source code pin mismatch: {recorded}")
        source_code_pins[str(path.resolve())] = expected
    # The production final-gate runner writes execution-freeze.json as a second
    # receipt binding all-gates-frozen.json by its complete byte hash.
    execution_receipt = receipt_path.parent / "execution-freeze.json"
    all_gates_receipt = receipt_path.parent / "all-gates-frozen.json"
    if execution_receipt.is_file() and all_gates_receipt.is_file():
        execution = read_json(execution_receipt)
        if execution.get("all_gates_frozen_manifest_sha256") != sha(all_gates_receipt.read_bytes()):
            raise ValueError("execution freeze does not bind all-gates-frozen.json")
    elif receipt_path.name == "execution-freeze.json":
        raise FileNotFoundError("execution freeze requires sibling all-gates-frozen.json")
    required = list(prediction_paths.values()) + gate_paths
    resolved = {}
    for path in required:
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(f"frozen prediction/gate artifact missing or unsafe: {path}")
        try:
            rel = path.resolve().relative_to(ROOT).as_posix()
        except ValueError as exc:
            raise ValueError(f"frozen artifact is outside repository: {path}") from exc
        receipt_rel = path.resolve().relative_to(receipt_path.parent.resolve()).as_posix() if path.resolve().is_relative_to(receipt_path.parent.resolve()) else None
        expected = pins.get(receipt_rel) if receipt_rel else None
        if expected is None:
            expected = pins.get(rel)
        actual = sha(path.read_bytes())
        if expected != actual:
            raise ValueError(f"artifact absent from freeze receipt or SHA mismatch: {rel}")
        resolved[rel] = actual
    declared_variants = receipt.get("variants")
    if declared_variants is not None and set(declared_variants) != set(prediction_paths):
        raise ValueError("variant names differ from the frozen gate receipt")
    return {"receipt_sha256": sha(receipt_path.read_bytes()), "frozen_artifacts": resolved,
            "source_code_pins": source_code_pins,
            "membership_sha256": EXPECTED_MEMBERSHIP_SHA256}


def load_token_reference_shards(
    archive_v2: Path,
    archive_v3: Path,
    expected_case_ids: set[str],
    split_by_case: dict[str, str],
    row_keys_by_case: dict[str, set[str]],
    prefixes: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve only selected case IDs through frozen private answer provenance.

    Public answer envelopes intentionally have no case_id. The frozen
    labels-token/private/resolved.jsonl table provides an exact (case_id,
    answer_file, answer_line) binding. The original answer line is then read at
    that exact location and checked against its pinned answer-member SHA.
    """
    prefixes = prefixes or {"v2": V2_LABEL_PREFIX, "v3": V3_LABEL_PREFIX}
    expected_archive_shas = {"v2": V2_LABEL_ARCHIVE_SHA256, "v3": V3_LABEL_ARCHIVE_SHA256}
    archives = {"v2": Path(archive_v2), "v3": Path(archive_v3)}
    if set(expected_case_ids) != set(split_by_case):
        raise ValueError("selected membership split map must exactly cover the 40 cases")
    result: dict[str, dict[str, Any]] = {}
    for split in ("v2", "v3"):
        selected = {case_id for case_id in expected_case_ids if split_by_case[case_id] == split}
        if not selected:
            raise ValueError(f"frozen subset has no cases for required split {split}")
        archive = archives[split]
        archive_sha = sha(archive.read_bytes())
        if archive_sha != expected_archive_shas[split]:
            raise ValueError(f"frozen {split} answer archive SHA differs from its external dependency pin")
        prefix = prefixes[split]
        resolved_member = prefix + "private/resolved.jsonl"
        manifest_member = prefix + "private/label-manifest.json"
        with zipfile.ZipFile(archive) as source:
            names = set(source.namelist())
            if resolved_member not in names or manifest_member not in names:
                raise FileNotFoundError(f"frozen {split} private answer provenance is missing")
            label_manifest = json.loads(source.read(manifest_member))
            answer_pins = label_manifest.get("answers_sha256")
            if not isinstance(answer_pins, dict) or not answer_pins:
                raise ValueError(f"frozen {split} label manifest lacks answer member pins")
            if label_manifest.get("resolved_sha256") != sha(source.read(resolved_member)):
                raise ValueError(f"frozen {split} resolved-to-answer mapping SHA mismatch")
            resolved_bytes = source.read(resolved_member)
            resolved = selected_records_from_bytes(
                resolved_bytes, selected, "independent_test_machine_reference",
                {"archive": archive, "archive_sha": archive_sha, "member": resolved_member,
                 "member_sha": sha(resolved_bytes)}, normalize_independent_label)
            if set(resolved) != selected:
                raise ValueError(f"frozen {split} private mapping does not exactly cover selected cases")
            used_answer_pointers: set[tuple[str, int]] = set()
            member_cache: dict[str, bytes] = {}
            for case_id, record in resolved.items():
                record["_reference_split"] = split
                answer_file = record.get("answer_file")
                answer_line = record.get("answer_line")
                if (not isinstance(answer_file, str) or Path(answer_file).name != answer_file
                        or answer_file not in answer_pins or type(answer_line) is not int or answer_line < 1):
                    raise ValueError(f"invalid frozen answer file/line pointer for selected case: {case_id}")
                pointer = (answer_file, answer_line)
                if pointer in used_answer_pointers:
                    raise ValueError(f"duplicate selected source answer pointer: {answer_file}:{answer_line}")
                used_answer_pointers.add(pointer)
                answer_member = prefix + answer_file
                if answer_member not in names:
                    raise FileNotFoundError(f"pinned answer member is absent: {answer_member}")
                if answer_file not in member_cache:
                    payload = source.read(answer_member)
                    if sha(payload) != answer_pins[answer_file]:
                        raise ValueError(f"answer member SHA differs from private label manifest: {answer_member}")
                    member_cache[answer_file] = payload
                payload = member_cache[answer_file]
                lines = payload.splitlines(keepends=True)
                if answer_line > len(lines):
                    raise ValueError(f"answer line pointer is outside the pinned member: {answer_member}:{answer_line}")
                raw_line = lines[answer_line - 1].rstrip(b"\r\n")
                answer = json.loads(raw_line)
                if not isinstance(answer, dict) or set(answer) != LABEL_ENVELOPE_KEYS:
                    raise ValueError(f"unexpected frozen answer envelope schema at {answer_member}:{answer_line}")
                if (not isinstance(answer.get("s"), str)
                        or not isinstance(answer.get("au_rows"), list)
                        or any(not isinstance(value, str) for value in answer["au_rows"])
                        or not isinstance(answer.get("rationale"), list)):
                    raise ValueError(f"invalid frozen answer envelope field types at {answer_member}:{answer_line}")
                if (answer.get("expected") != record.get("expected")
                        or answer.get("data_quality_flag") != record.get("data_quality_flag")):
                    raise ValueError(f"private mapping does not preserve original label fields: {answer_member}:{answer_line}")
                raw_row_sha = sha(raw_line)
                record["source_answer_record"] = answer
                record["source_answer_sha256"] = raw_row_sha
                record["source_answer_route"] = {
                    "archive": str(archive), "archive_sha256": archive_sha,
                    "member": answer_member, "member_sha256": answer_pins[answer_file],
                    "answer_file": answer_file, "answer_line": answer_line,
                    "answer_line_sha256": raw_row_sha,
                    "resolved_member": resolved_member,
                    "resolved_member_sha256": label_manifest["resolved_sha256"],
                    "case_binding": "private/resolved.jsonl exact case_id to answer_file+answer_line",
                }
                row_keys = record.get("au_row_keys")
                if not isinstance(row_keys, list) or any(not isinstance(k, str) or not k for k in row_keys):
                    raise ValueError(f"resolved AU row keys are malformed for selected case: {case_id}")
                if not set(row_keys).issubset(row_keys_by_case.get(case_id, set())):
                    raise ValueError(f"resolved AU row keys are absent from frozen case inputs: {case_id}")
                record["_reference_split"] = split
                result[case_id] = record
    if set(result) != expected_case_ids:
        raise ValueError("selected v2/v3 references do not exactly cover the frozen 40 cases")
    return result


def canonical_decision(row: dict[str, Any]) -> tuple[str, str | None]:
    raw = row.get("decision", row.get("gate_decision", row.get("strict_gate_decision")))
    mapped = {
        "accept": "matched", "matched": "matched",
        "drop": "unmatched", "unmatched": "unmatched",
        "review": "review", "unknown": "unknown", "error": "unknown",
    }.get(raw)
    if mapped is None:
        raise ValueError(f"unsupported prediction decision: {raw!r}")
    row_key = None
    for key in ("top_row_key", "au_row_key", "matched_au_row_key", "selected_row_key"):
        value = row.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise ValueError(f"prediction {key} must be string or null")
            row_key = value
            break
    if mapped != "matched" and row_key is not None:
        raise ValueError("non-matched prediction must not carry a selected AU row key")
    return mapped, row_key


def canonical_reference(row: dict[str, Any]) -> tuple[str | None, list[str] | None]:
    if "label" in row and "decision" in row and row["label"] != row["decision"]:
        raise ValueError("reference label/decision aliases conflict")
    label = row.get("label", row.get("decision", row.get("semantic_label")))
    if label not in {"matched", "unmatched", "review", "needs_review", "unknown", "uncertain", "needs_confirmation", None}:
        raise ValueError(f"unsupported reference label: {label!r}")
    row_values = [row.get(key) for key in ("matched_au_row_keys", "matching_au_row_keys", "gold_row_keys", "au_row_keys") if key in row]
    if row_values and any(value != row_values[0] for value in row_values[1:]):
        raise ValueError("reference row-key aliases conflict")
    keys = row_values[0] if row_values else None
    if keys is not None and (not isinstance(keys, list) or any(not isinstance(value, str) or not value for value in keys)):
        raise ValueError("reference AU row keys must be an array of nonempty strings")
    if label == "matched" and keys is not None and not keys:
        raise ValueError("matched reference with a supplied row-key list must have at least one row key")
    return label, keys


def score_variant(
    membership: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    references: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    members = unique_index(membership, "case_id", "membership")
    predicted = unique_index(predictions, "case_id", "predictions")
    if set(predicted) != set(members):
        missing = sorted(set(members) - set(predicted))
        extra = sorted(set(predicted) - set(members))
        raise ValueError(f"prediction coverage must exactly match frozen membership; missing={missing[:3]} extra={extra[:3]}")

    counts: Counter[str] = Counter()
    split_counts: dict[str, Counter[str]] = defaultdict(Counter)
    row_counts: Counter[str] = Counter()
    unknown_cases, differences, scored = [], [], []
    for case_id, member in members.items():
        prediction = predicted[case_id]
        decision, row_key = canonical_decision(prediction)
        reference = references.get(case_id)
        split = member.get("split", member.get("cohort", member.get("source_version", "unknown_split")))
        if reference is None:
            unknown_cases.append({"case_id": case_id, "split": split, "reason": "missing_reference"})
            continue
        label, expected_rows = canonical_reference(reference)
        expected_split = {"independent_real_eval_v2": "v2", "independent_real_eval_v3": "v3"}.get(split, split)
        if reference.get("_reference_split") not in {None, split, expected_split}:
            raise ValueError(f"reference split differs from frozen membership: {case_id}")
        if label not in {"matched", "unmatched"}:
            unknown_cases.append({"case_id": case_id, "split": split, "reason": "unresolved_reference"})
            continue
        accepted, matched = decision == "matched", label == "matched"
        cell = "tp" if accepted and matched else "fp" if accepted else "fn" if matched else "tn"
        counts[cell] += 1
        split_counts[str(split)][cell] += 1
        wrong_row = False
        if accepted and matched:
            if expected_rows is None:
                row_counts["accepted_matched_row_unscored"] += 1
            elif row_key in expected_rows:
                row_counts["correct_accepted_row"] += 1
            else:
                row_counts["wrong_accepted_row"] += 1
                wrong_row = True
        if cell in {"fp", "fn"} or wrong_row:
            differences.append({"case_id": case_id, "split": split, "label": label,
                                "prediction": decision, "predicted_row_key": row_key,
                                "reference_row_keys": expected_rows,
                                "difference": "wrong_accepted_row" if wrong_row else cell})
        scored.append(case_id)

    def metric(counter: Counter[str]) -> dict[str, Any]:
        cells = {key: counter[key] for key in ("tp", "fp", "fn", "tn")}
        known = sum(cells.values())
        return {**cells, "scored_case_count": known,
                "binary_accuracy": (cells["tp"] + cells["tn"]) / known if known else None,
                "precision": cells["tp"] / (cells["tp"] + cells["fp"]) if cells["tp"] + cells["fp"] else None,
                "recall": cells["tp"] / (cells["tp"] + cells["fn"]) if cells["tp"] + cells["fn"] else None}

    total = len(members)
    # Keep the existing INTEGRATE.score metrics layout while making the full
    # 40-case and unknown-reference denominators explicit.
    return {
        "schema_version": "claude-description-integration-v1",
        "phase": "score",
        "reference_kind": "claude-haiku-5-5 machine annotation; not human gold",
        "decisions_changed": False,
        "thresholds_selected": False,
        "production_adoption_allowed": False,
        "total_case_count": total,
        "known_reference_case_count": len(scored),
        "unknown_reference_count": len(unknown_cases),
        "unknown_reference_case_ids": [row["case_id"] for row in unknown_cases],
        "metrics": metric(counts),
        "cohort_metrics": {name: metric(value) for name, value in sorted(split_counts.items())},
        "row_metrics": dict(row_counts),
        "exact_membership_coverage": {"expected": total, "predictions": len(predicted),
                                      "scored_or_explicitly_unknown": len(scored) + len(unknown_cases),
                                      "case_omission_count": total - len(predicted)},
        "exact_accepted_row_key_validation": True,
        "excluded": unknown_cases,
        "differences": differences,
        "references_outside_received_decisions": len(set(references) - set(members)),
        "whole_sku_correctness_established": False,
    }


def score_all_variants(
    membership: list[dict[str, Any]],
    variant_predictions: dict[str, list[dict[str, Any]]],
    references: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not variant_predictions:
        raise ValueError("at least one prediction variant is required")
    results = {name: score_variant(membership, rows, references)
               for name, rows in sorted(variant_predictions.items())}
    return {"schema_version": "final-sku-subset-integrate-score-v1",
            "phase": "score", "independent_test": True,
            "reference_kind": "claude-haiku-5-5 machine annotation; not human gold",
            "test_total_case_count": len(membership),
            "labels_read_after_predictions_and_gates_frozen": True,
            "predictions_changed": False, "gates_changed": False,
            "thresholds_selected": False, "production_adoption_allowed": False,
            "same_membership_for_all_variants": True,
            "variants": results}


def read_label_archives(archive_v2: Path, archive_v3: Path,
                        membership: list[dict[str, Any]],
                        row_plans_path: Path) -> dict[str, dict[str, Any]]:
    members = unique_index(membership, "case_id", "membership")
    split_by_case = {}
    for case_id, member in members.items():
        split = member.get("source_version")
        if split not in {"v2", "v3"}:
            raise ValueError(f"frozen membership has unsupported source_version for {case_id}")
        split_by_case[case_id] = split
    row_keys_by_case: dict[str, set[str]] = defaultdict(set)
    for row in read_jsonl(row_plans_path):
        case_id = row.get("case_id")
        row_key = row.get("au_row_key", row.get("row_key"))
        if case_id in members and isinstance(row_key, str) and row_key:
            row_keys_by_case[case_id].add(row_key)
    references = load_token_reference_shards(archive_v2, archive_v3, set(members), split_by_case,
                                               row_keys_by_case)
    return references


def grade_from_files(
    *,
    root_go: bool,
    membership_path: Path,
    receipt_path: Path,
    variant_paths: dict[str, Path],
    gate_paths: list[Path],
    archive_v2: Path,
    archive_v3: Path,
) -> dict[str, Any]:
    # These checks intentionally run before opening either reference ZIP.
    if root_go is not True:
        raise PermissionError("grading requires the explicit --root-go authorization")
    receipt_info = validate_freeze_receipt(receipt_path, membership_path, variant_paths, gate_paths)
    membership = read_jsonl(membership_path)
    if len(membership) != EXPECTED_CASE_COUNT:
        raise ValueError(f"frozen membership must contain exactly {EXPECTED_CASE_COUNT} cases")
    members = unique_index(membership, "case_id", "membership")
    expected = set(members)
    variant_rows = {name: read_jsonl(path) for name, path in variant_paths.items()}
    # Only now, after freeze receipt validation and explicit root GO, open labels.
    row_plans_path = ROOT / ".lab-output/sku-final-composition-subset-20261013-v1/gate-prepared-assembly-v3/row_plans.jsonl"
    references = read_label_archives(archive_v2, archive_v3, membership, row_plans_path)
    output = score_all_variants(membership, variant_rows, references)
    output["freeze_receipt"] = receipt_info
    output["membership_sha256"] = EXPECTED_MEMBERSHIP_SHA256
    output["variant_input_sha256"] = {name: sha(path.read_bytes()) for name, path in variant_paths.items()}
    output["gate_input_sha256"] = {path.resolve().relative_to(ROOT).as_posix(): sha(path.read_bytes()) for path in gate_paths}
    output["label_archive_sha256"] = {"v2": sha(archive_v2.read_bytes()), "v3": sha(archive_v3.read_bytes())}
    output["reference_resolution"] = {
        "schema_version": "sku-selected-reference-resolution-v1",
        "answer_envelope_schema": {"field_names": sorted(LABEL_ENVELOPE_KEYS), "case_id_present": False},
        "case_binding": "labels-token/private/resolved.jsonl case_id -> answer_file + 1-based answer_line",
        "selected_reference_count": len(references),
        "references": [references[case_id] for case_id in sorted(references)],
        "all_case_bindings_unique": len({(r["_reference_split"], r["answer_file"], r["answer_line"]) for r in references.values()}) == len(references),
        "row_keys_verified_against_frozen_case_plans": True,
        "complete_selected_membership_coverage": set(references) == expected,
    }
    return output


def parse_variant(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("variant must be NAME=PATH")
    name, raw_path = value.split("=", 1)
    if not name or not raw_path:
        raise argparse.ArgumentTypeError("variant must be NAME=PATH")
    return name, Path(raw_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-go", action="store_true", help="required explicit root authorization after freeze")
    parser.add_argument("--membership", type=Path, default=ROOT / ".lab-output/sku-final-composition-subset-20261013-v1/plan/subset-membership.jsonl")
    parser.add_argument("--freeze-receipt", type=Path, required=True)
    parser.add_argument("--variant", action="append", type=parse_variant, required=True)
    parser.add_argument("--gate-file", action="append", type=Path, required=True)
    parser.add_argument("--v2-label-archive", type=Path, default=ROOT / V2_LABEL_ARCHIVE)
    parser.add_argument("--v3-label-archive", type=Path, default=ROOT / V3_LABEL_ARCHIVE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--references-output", type=Path, required=True,
                        help="write only the exact selected-case source references (never all-gold rows)")
    args = parser.parse_args(argv)
    variant_paths = dict(args.variant)
    if len(variant_paths) != len(args.variant):
        raise ValueError("variant names must be unique")
    if args.output.resolve() == args.references_output.resolve():
        raise ValueError("score and selected-reference output paths must be different")
    if args.output.exists() or args.references_output.exists():
        raise FileExistsError("refusing to overwrite grade output or selected references")
    result = grade_from_files(root_go=args.root_go, membership_path=args.membership,
                              receipt_path=args.freeze_receipt, variant_paths=variant_paths,
                              gate_paths=args.gate_file, archive_v2=args.v2_label_archive,
                              archive_v3=args.v3_label_archive)
    references = result.pop("reference_resolution")["references"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.references_output.parent.mkdir(parents=True, exist_ok=True)
    with args.references_output.open("xb") as stream:
        for row in references:
            stream.write((json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode())
    with args.output.open("xb") as stream:
        stream.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps({"output": str(args.output), "references_output": str(args.references_output),
                      "selected_reference_count": len(references), "test_total_case_count": result["test_total_case_count"],
                      "variant_count": len(result["variants"]), "label_archives_read": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
