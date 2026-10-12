#!/usr/bin/env python3
"""Grade the frozen, source-blind unused TEST40 cohort after explicit root GO.

The selected membership and its policy are fixed before inference. This program
checks the complete prediction/gate/source freeze receipt before opening either
answer archive, then resolves only selected case IDs through the private exact
answer-file/line map. It never changes predictions, gates, or policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / ".lab-output/sku-unused-holdout-choice-20261013-v1"
MEMBERSHIP = BASE / "selection-membership.jsonl"
CASE_PLANS = BASE / "source-inputs/case-plans.jsonl"
ROW_PLANS = BASE / "source-inputs/row-plans.jsonl"
SELECTION_POLICY = BASE / "selection-policy.json"
ORIGINAL_CASE_RECEIPTS = BASE / "original-case-line-receipts.jsonl"
SOURCE_INPUT_MANIFEST = BASE / "source-inputs/manifest.json"
OLD_MEMBERSHIP = ROOT / ".lab-output/sku-final-composition-subset-20261013-v1/plan/subset-membership.jsonl"
EXPECTED_MEMBERSHIP_SHA256 = "717c3403c11a7f00aa5e66ee03402717214744fe689ecee209354c7e0fac6da7"
EXPECTED_POLICY_SHA256 = "66fb8338069130e1c4b24dbde463291ccee3f8ebcd5708144a260da67b35256d"
EXPECTED_OLD_MEMBERSHIP_SHA256 = "212123b8851168a8895e8a035f8a1a032c173c496b76024b707030efca93778d"
CASE_COUNT = 40
PRODUCT_COUNT = 20
V2_ARCHIVE = ROOT / ".lab-output/sku-claude-additional-review-20261012-v1/20261011-sku-independent-real-eval-v2.zip"
V3_ARCHIVE = ROOT / ".lab-output/sku-claude-additional-review-20261012-v1/20261012-sku-independent-real-eval-v3.zip"

if str(ROOT / "experiments/sku-matching") not in sys.path:
    sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
from route_mixed_sku_references_v1 import normalize_independent_label, selected_records_from_bytes
from grade_final_test_subset_v1 import (
    LABEL_ENVELOPE_KEYS, V2_LABEL_ARCHIVE_SHA256, V3_LABEL_ARCHIVE_SHA256,
    canonical_decision, read_jsonl, score_variant, sha, unique_index,
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def safe_repo_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"freeze path must be repo-relative and traversal-free: {value}")
    target = ROOT / path
    if not target.is_file() or target.is_symlink():
        raise FileNotFoundError(f"frozen file missing or unsafe: {value}")
    return target


def validate_selection(membership_path: Path, case_plans_path: Path, row_plans_path: Path,
                       selection_policy_path: Path, old_membership_path: Path,
                       original_case_receipts_path: Path) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    """Validate the pre-inference draw, exact source identity, and full AU-row coverage."""
    if sha(membership_path.read_bytes()) != EXPECTED_MEMBERSHIP_SHA256:
        raise ValueError("unused TEST membership differs from the pre-inference frozen draw")
    if sha(old_membership_path.read_bytes()) != EXPECTED_OLD_MEMBERSHIP_SHA256:
        raise ValueError("prior consumed-product membership differs from its frozen pin")
    policy = read_json(selection_policy_path)
    base_policy = {k: v for k, v in policy.items()
                   if k not in {"policy_sha256", "status", "dev_test_product_separation_audit"}}
    canonical = json.dumps(base_policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                           allow_nan=False).encode("utf-8")
    if (policy.get("schema_version") != "unused-holdout-choice-nli-selection-policy-v1"
            or policy.get("status") != "frozen_before_selection"
            or policy.get("policy_sha256") != EXPECTED_POLICY_SHA256
            or sha(canonical) != EXPECTED_POLICY_SHA256):
        raise ValueError("selection policy is not the fixed pre-inference policy")
    if policy.get("source_files", {}).get("old_subset_membership_sha256") != EXPECTED_OLD_MEMBERSHIP_SHA256:
        raise ValueError("pre-inference policy does not bind complete prior-product exclusion")
    if (policy.get("scope_guards", {}).get("labels_read") is not False
            or policy.get("scope_guards", {}).get("gold_or_references_read") is not False
            or policy.get("scope_guards", {}).get("predictions_or_scores_read") is not False
            or policy.get("scope_guards", {}).get("classification_or_scoring_run") is not False):
        raise ValueError("selection policy does not prove label-blind pre-inference selection")
    members = read_jsonl(membership_path)
    cases = read_jsonl(case_plans_path)
    rows = read_jsonl(row_plans_path)
    source_manifest = read_json(SOURCE_INPUT_MANIFEST)
    if (source_manifest.get("schema_version") != "full-condition-inputs-v2"
            or source_manifest.get("split") != "test" or source_manifest.get("independent_test") is not True
            or source_manifest.get("labels_read") is not False
            or source_manifest.get("labels_gold_references_predictions_scores_read") is not False
            or source_manifest.get("all_rows_for_selected_cases_retained") is not True
            or source_manifest.get("all_original_task_bindings_retained") is not True
            or source_manifest.get("selection_policy_sha256") != EXPECTED_POLICY_SHA256
            or source_manifest.get("selected_membership_sha256") != EXPECTED_MEMBERSHIP_SHA256):
        raise ValueError("selected source manifest is not the frozen label-blind TEST handoff")
    for filename, path in (("case-plans.jsonl", case_plans_path), ("row-plans.jsonl", row_plans_path)):
        if source_manifest.get("outputs_sha256", {}).get(filename) != sha(path.read_bytes()):
            raise ValueError(f"selected source manifest does not bind {filename}")
    if len(members) != CASE_COUNT:
        raise ValueError("frozen unused TEST membership must contain exactly 40 cases")
    member_map = unique_index(members, "case_id", "unused TEST membership")
    case_map = unique_index(cases, "case_id", "unused TEST case plans")
    if set(member_map) != set(case_map):
        raise ValueError("selected case plans do not exactly cover the frozen membership")
    old_products = {str(x.get("au_product_id")) for x in read_jsonl(old_membership_path)}
    if len(old_products) != PRODUCT_COUNT:
        raise ValueError("prior consumed membership does not identify the frozen 20 products")
    products = Counter()
    for cid, member in member_map.items():
        if member.get("source_version") not in {"v2", "v3"}:
            raise ValueError(f"unsupported source version in frozen membership: {cid}")
        if member.get("au_product_id") is None or member.get("au_product_id") == "":
            raise ValueError(f"missing product identity in membership: {cid}")
        product = str(member["au_product_id"])
        if product in old_products:
            raise ValueError(f"selected product was consumed by the old test cohort: {product}")
        products[product] += 1
        case = case_map[cid]
        for key in ("case_id", "dossier_id", "au_product_id", "source_version"):
            if case.get(key) != member.get(key):
                raise ValueError(f"membership/case-plan identity mismatch ({key}): {cid}")
        if case.get("split") != "test":
            raise ValueError(f"selected source case is not explicitly TEST: {cid}")
    if len(products) != PRODUCT_COUNT or set(products.values()) != {2}:
        raise ValueError("frozen unused TEST draw must contain two cases from each of 20 products")
    source_rows: dict[str, set[str]] = {cid: set() for cid in member_map}
    for row in rows:
        cid = row.get("case_id")
        if cid not in member_map:
            raise ValueError(f"row plan contains an unselected case: {cid}")
        key = row.get("au_row_key", row.get("row_key"))
        if not isinstance(key, str) or not key or key in source_rows[cid]:
            raise ValueError(f"missing or duplicate original AU row key: {cid}")
        source_rows[cid].add(key)
    for cid, case in case_map.items():
        declared = case.get("row_keys")
        if (not isinstance(declared, list) or not declared
                or len(declared) != len(set(declared)) or set(declared) != source_rows[cid]
                or case.get("all_original_au_rows_covered") is not True):
            raise ValueError(f"selected case omits or synthesizes an original AU row: {cid}")
    receipts = unique_index(read_jsonl(original_case_receipts_path), "case_id", "original case-line receipts")
    if set(receipts) != set(member_map):
        raise ValueError("original source case-line receipts do not exactly cover selected membership")
    for cid, member in member_map.items():
        receipt = receipts[cid]
        digest = receipt.get("original_case_line_sha256")
        if receipt.get("split") != "test" or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"selected case is not bound to its exact original TEST source line: {cid}")
    return members, source_rows


def validate_freeze_receipt(receipt_path: Path, *, membership_path: Path, case_plans_path: Path,
                            row_plans_path: Path, selection_policy_path: Path, old_membership_path: Path,
                            original_case_receipts_path: Path, source_manifest_path: Path,
                            expected_variants: set[str] | None = None) -> dict[str, Any]:
    """Check root freeze receipt and every prediction/gate/source pin before label access."""
    receipt = read_json(receipt_path)
    if (receipt.get("schema_version") != "sku-unused-holdout-grading-freeze-v1"
            or receipt.get("freeze_status") != "all_predictions_and_gates_frozen"
            or receipt.get("all_predictions_frozen") is not True
            or receipt.get("all_gates_frozen") is not True
            or receipt.get("source_split") != "test"
            or receipt.get("independent_test") is not True
            or receipt.get("labels_read") is not False
            or receipt.get("gold_or_references_read") is not False
            or receipt.get("membership_sha256") != EXPECTED_MEMBERSHIP_SHA256
            or receipt.get("selection_policy_sha256") != EXPECTED_POLICY_SHA256
            or receipt.get("policy_or_threshold_changed") is not False
            or receipt.get("classifier_outputs_changed_after_freeze") is not False):
        raise ValueError("freeze receipt does not bind the predeclared independent TEST cohort and frozen outputs")
    variants = receipt.get("variants")
    if (not isinstance(variants, dict) or not variants
            or (expected_variants is not None and set(variants) != expected_variants)):
        raise ValueError("frozen TEST variant inventory is missing or differs from requested variants")
    gate_files = receipt.get("gate_files")
    if not isinstance(gate_files, list) or not gate_files or any(not isinstance(p, str) for p in gate_files):
        raise ValueError("freeze receipt lacks the complete gate inventory")
    input_files = receipt.get("input_files")
    required_inputs = {"membership": membership_path, "case_plans": case_plans_path,
                       "row_plans": row_plans_path, "selection_policy": selection_policy_path,
                       "original_case_receipts": original_case_receipts_path,
                       "old_membership": old_membership_path, "source_manifest": source_manifest_path}
    if not isinstance(input_files, dict):
        raise ValueError("freeze receipt lacks source-selection inputs")
    for name, path in required_inputs.items():
        rel = path.resolve().relative_to(ROOT).as_posix()
        if input_files.get(name) != rel:
            raise ValueError(f"freeze receipt does not bind exact {name} path")
    pins = receipt.get("frozen_files")
    if not isinstance(pins, dict) or not pins:
        raise ValueError("freeze receipt lacks frozen byte/SHA pins")
    needed = set(input_files.values()) | set(gate_files)
    for name, entry in variants.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("predictions"), str):
            raise ValueError(f"prediction path missing for variant {name}")
        needed.add(entry["predictions"])
        listed_gates = entry.get("gate_files")
        if not isinstance(listed_gates, list) or not listed_gates or not set(listed_gates) <= set(gate_files):
            raise ValueError(f"variant gate inventory is incomplete: {name}")
        basenames = [Path(x).name.lower().replace("-", "_") for x in listed_gates]
        if (not any("decision" in x for x in basenames)
                or not any("row" in x and "result" in x for x in basenames)):
            raise ValueError(f"variant is missing case-decision or all-row gate outputs: {name}")
    required = receipt.get("required_artifact_paths")
    if not isinstance(required, list) or not required or any(not isinstance(x, str) for x in required):
        raise ValueError("freeze receipt must enumerate proof, source, model, runtime, and code artifacts")
    needed.update(required)
    code_paths = (Path(__file__).resolve().relative_to(ROOT).as_posix(),
                  "tests/test_lab_unused_holdout_sku_gate.py",
                  "experiments/sku-matching/route_mixed_sku_references_v1.py",
                  "experiments/sku-matching/grade_final_test_subset_v1.py")
    for rel in code_paths:
        if rel not in pins:
            raise ValueError(f"freeze receipt does not pin grader/parser/test code: {rel}")
        needed.add(rel)
    for rel in needed:
        if rel not in pins:
            raise ValueError(f"freeze receipt has no pin for required artifact: {rel}")
    for rel, pin in pins.items():
        path = safe_repo_path(rel)
        parts = {x.lower() for x in Path(rel).parts}
        if parts & {"labels", "labels-token", "gold", "truth", "answers", "answer-shards"}:
            raise ValueError(f"label-bearing artifacts must remain outside the pre-grade freeze: {rel}")
        if (not isinstance(pin, dict) or type(pin.get("bytes")) is not int
                or not isinstance(pin.get("sha256"), str)):
            raise ValueError(f"malformed frozen byte/SHA pin: {rel}")
        if path.stat().st_size != pin["bytes"] or sha(path.read_bytes()) != pin["sha256"]:
            raise ValueError(f"frozen artifact byte/SHA mismatch: {rel}")
    return {"receipt_sha256": sha(receipt_path.read_bytes()), "membership_sha256": EXPECTED_MEMBERSHIP_SHA256,
            "selection_policy_sha256": EXPECTED_POLICY_SHA256,
            "frozen_file_sha256": {k: v["sha256"] for k, v in sorted(pins.items())},
            "variant_names": sorted(variants), "gate_files": list(gate_files)}


def load_selected_references(archive_v2: Path, archive_v3: Path, membership: list[dict[str, Any]],
                             row_keys_by_case: dict[str, set[str]]) -> dict[str, dict[str, Any]]:
    """Parse selected exact source answer pointers; an unselected split stays unopened."""
    member_map = unique_index(membership, "case_id", "membership")
    split_by_case = {cid: row["source_version"] for cid, row in member_map.items()}
    if set(split_by_case) != set(member_map) or set(split_by_case.values()) - {"v2", "v3"}:
        raise ValueError("membership source-version map is malformed")
    archives = {"v2": Path(archive_v2), "v3": Path(archive_v3)}
    expected_shas = {"v2": V2_LABEL_ARCHIVE_SHA256, "v3": V3_LABEL_ARCHIVE_SHA256}
    prefixes = {"v2": ".lab-output/sku-independent-real-20261011/eval-v2/labels-token/",
                "v3": ".lab-output/sku-independent-real-20261011/eval-v3/labels-token/"}
    output: dict[str, dict[str, Any]] = {}
    for split in ("v2", "v3"):
        selected = {cid for cid, source in split_by_case.items() if source == split}
        if not selected:
            continue
        archive = archives[split]
        archive_sha = sha(archive.read_bytes())
        if archive_sha != expected_shas[split]:
            raise ValueError(f"pinned {split} answer archive SHA mismatch")
        prefix = prefixes[split]
        resolved_member, manifest_member = prefix + "private/resolved.jsonl", prefix + "private/label-manifest.json"
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
            if resolved_member not in names or manifest_member not in names:
                raise FileNotFoundError(f"{split} private exact answer mapping is missing")
            manifest = json.loads(zf.read(manifest_member))
            answer_pins = manifest.get("answers_sha256")
            resolved_bytes = zf.read(resolved_member)
            if (not isinstance(answer_pins, dict) or not answer_pins
                    or manifest.get("resolved_sha256") != sha(resolved_bytes)):
                raise ValueError(f"{split} label manifest does not pin exact answer mapping")
            resolved = selected_records_from_bytes(resolved_bytes, selected,
                "independent_test_machine_reference", {"archive": archive, "archive_sha": archive_sha,
                "member": resolved_member, "member_sha": sha(resolved_bytes)}, normalize_independent_label)
            if set(resolved) != selected:
                raise ValueError(f"{split} exact source answer mapping does not cover selected cases")
            used: set[tuple[str, int]] = set()
            cache: dict[str, bytes] = {}
            for cid, record in resolved.items():
                answer_file, answer_line = record.get("answer_file"), record.get("answer_line")
                if (not isinstance(answer_file, str) or Path(answer_file).name != answer_file
                        or answer_file not in answer_pins or type(answer_line) is not int or answer_line < 1):
                    raise ValueError(f"invalid exact answer file/line pointer: {cid}")
                if (answer_file, answer_line) in used:
                    raise ValueError(f"duplicate exact source answer pointer: {answer_file}:{answer_line}")
                used.add((answer_file, answer_line))
                member = prefix + answer_file
                if member not in names:
                    raise FileNotFoundError(f"exact pinned answer member is absent: {member}")
                if answer_file not in cache:
                    data = zf.read(member)
                    if sha(data) != answer_pins[answer_file]:
                        raise ValueError(f"pinned answer member SHA mismatch: {member}")
                    cache[answer_file] = data
                lines = cache[answer_file].splitlines(keepends=True)
                if answer_line > len(lines):
                    raise ValueError(f"exact answer line is outside its pinned member: {member}:{answer_line}")
                raw = lines[answer_line - 1].rstrip(b"\r\n")
                answer = json.loads(raw)
                if (not isinstance(answer, dict) or set(answer) != LABEL_ENVELOPE_KEYS
                        or not isinstance(answer.get("s"), str)
                        or not isinstance(answer.get("au_rows"), list)
                        or any(not isinstance(x, str) for x in answer["au_rows"])
                        or not isinstance(answer.get("rationale"), list)):
                    raise ValueError(f"unexpected frozen answer envelope at {member}:{answer_line}")
                if (answer.get("expected") != record.get("expected")
                        or answer.get("data_quality_flag") != record.get("data_quality_flag")):
                    raise ValueError(f"exact answer mapping does not preserve label fields: {cid}")
                keys = record.get("au_row_keys")
                if (not isinstance(keys, list) or any(not isinstance(k, str) or not k for k in keys)
                        or not set(keys) <= row_keys_by_case.get(cid, set())):
                    raise ValueError(f"reference row keys are not actual frozen AU rows: {cid}")
                record["_reference_split"] = split
                record["source_answer_record"] = answer
                record["source_answer_sha256"] = sha(raw)
                record["source_answer_route"] = {"archive": str(archive), "archive_sha256": archive_sha,
                    "member": member, "member_sha256": answer_pins[answer_file], "answer_file": answer_file,
                    "answer_line": answer_line, "answer_line_sha256": sha(raw),
                    "resolved_member": resolved_member, "resolved_member_sha256": manifest["resolved_sha256"],
                    "case_binding": "private/resolved.jsonl exact case_id to answer_file+answer_line"}
                output[cid] = record
    if set(output) != set(member_map):
        raise ValueError("selected references do not exactly cover frozen membership")
    return output


def grade_from_files(*, root_go: bool, membership_path: Path, case_plans_path: Path,
                     row_plans_path: Path, selection_policy_path: Path, old_membership_path: Path,
                     original_case_receipts_path: Path, receipt_path: Path,
                     archive_v2: Path = V2_ARCHIVE, archive_v3: Path = V3_ARCHIVE,
                     expected_variants: set[str] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if root_go is not True:
        raise PermissionError("unused independent TEST scoring requires explicit --root-go after freeze")
    frozen = validate_freeze_receipt(receipt_path, membership_path=membership_path,
        case_plans_path=case_plans_path, row_plans_path=row_plans_path,
        selection_policy_path=selection_policy_path, old_membership_path=old_membership_path,
        original_case_receipts_path=original_case_receipts_path, source_manifest_path=SOURCE_INPUT_MANIFEST,
        expected_variants=expected_variants)
    membership, row_keys = validate_selection(membership_path, case_plans_path, row_plans_path,
        selection_policy_path, old_membership_path, original_case_receipts_path)
    receipt = read_json(receipt_path)
    variant_predictions = {name: read_jsonl(safe_repo_path(entry["predictions"]))
                          for name, entry in receipt["variants"].items()}
    gate_receipts = [{"path": rel, "sha256": sha(safe_repo_path(rel).read_bytes()),
                      "bytes": safe_repo_path(rel).stat().st_size} for rel in receipt["gate_files"]]
    references = load_selected_references(archive_v2, archive_v3, membership, row_keys)
    expected = {x["case_id"] for x in membership}
    if set(references) != expected or len(references) != CASE_COUNT:
        raise ValueError("selected exact references do not fully cover the fixed 40-case membership")
    # Requiring exact case coverage and valid decisions before scoring prevents omissions hiding in the score helper.
    for name, predictions in variant_predictions.items():
        index = unique_index(predictions, "case_id", f"{name} predictions")
        if set(index) != expected:
            raise ValueError(f"{name} prediction coverage does not exactly match frozen membership")
        for row in predictions:
            canonical_decision(row)
    results = {name: score_variant(membership, rows, references)
               for name, rows in sorted(variant_predictions.items())}
    score = {"schema_version": "sku-unused-holdout-test-score-v1", "phase": "independent_test_score",
        "source_split": "test", "independent_test": True, "membership_sha256": EXPECTED_MEMBERSHIP_SHA256,
        "selection_policy_sha256": EXPECTED_POLICY_SHA256, "selected_case_count": CASE_COUNT,
        "selected_product_count": PRODUCT_COUNT, "reference_kind": "frozen independent machine reference",
        "labels_read_after_all_predictions_gates_and_source_inputs_frozen": True,
        "predictions_changed": False, "gates_changed": False, "thresholds_selected": False,
        "production_adoption_allowed": False, "same_membership_for_all_variants": True,
        "freeze_receipt": frozen, "reference_archive_sha256": {"v2": sha(archive_v2.read_bytes()),
            "v3": sha(archive_v3.read_bytes())}, "gate_file_sha256": gate_receipts,
        "variant_names": sorted(results), "variants": results, "selected_reference_count": len(references)}
    return score, [references[cid] for cid in sorted(references)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-go", action="store_true", help="required after frozen predictions, gates, and source pins")
    parser.add_argument("--membership", type=Path, default=MEMBERSHIP)
    parser.add_argument("--case-plans", type=Path, default=CASE_PLANS)
    parser.add_argument("--row-plans", type=Path, default=ROW_PLANS)
    parser.add_argument("--selection-policy", type=Path, default=SELECTION_POLICY)
    parser.add_argument("--old-membership", type=Path, default=OLD_MEMBERSHIP)
    parser.add_argument("--original-case-receipts", type=Path, default=ORIGINAL_CASE_RECEIPTS)
    parser.add_argument("--freeze-receipt", type=Path, required=True)
    parser.add_argument("--expected-variant", action="append", default=[])
    parser.add_argument("--v2-label-archive", type=Path, default=V2_ARCHIVE)
    parser.add_argument("--v3-label-archive", type=Path, default=V3_ARCHIVE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--references-output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.resolve() == args.references_output.resolve():
        raise ValueError("score and selected-reference outputs must be separate files")
    if args.output.exists() or args.references_output.exists():
        raise FileExistsError("refusing to overwrite prior TEST score/reference outputs")
    score, refs = grade_from_files(root_go=args.root_go, membership_path=args.membership,
        case_plans_path=args.case_plans, row_plans_path=args.row_plans,
        selection_policy_path=args.selection_policy, old_membership_path=args.old_membership,
        original_case_receipts_path=args.original_case_receipts, receipt_path=args.freeze_receipt,
        archive_v2=args.v2_label_archive, archive_v3=args.v3_label_archive,
        expected_variants=set(args.expected_variant) if args.expected_variant else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.references_output.parent.mkdir(parents=True, exist_ok=True)
    with args.references_output.open("xb") as stream:
        for row in refs:
            stream.write((json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode())
    with args.output.open("xb") as stream:
        stream.write((json.dumps(score, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    print(json.dumps({"score": str(args.output), "selected_references": str(args.references_output),
        "selected_reference_count": len(refs), "source_split": "test", "independent_test": True,
        "labels_read_after_freeze": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
