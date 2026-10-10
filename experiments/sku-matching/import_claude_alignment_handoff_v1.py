#!/usr/bin/env python3
"""Validate Claude's fixed-page alignment archive and export whole-axis cards.

This importer performs no model inference, label scoring or SKU adoption. Archive
instructions are retained as data; nothing is extracted or executed. Aligned
conditions remain unverified upstream observations in a separate context file.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT_MEMBER = "SKU_GATE_CHECKPOINT.json"
SELECTED_ARM = "align-japanese-reranker-small-v2-cpu-reverse-all-v4axes"
HANDOFF_MEMBERS = tuple(
    f".lab-output/sku-align-20261010-v1/{cohort}/{SELECTED_ARM}/handoff.jsonl"
    for cohort in ("legacy", "family")
)
UPSTREAM_PR = "https://github.com/cushionA/Searching_Experiment/pull/26"
UPSTREAM_HEAD = "983c6086a718190ede41ca14424114e1a6085f18"
PENDING_GROUPS = ("one_sided_conditions", "extra_in_value_conditions", "unresolved_conditions")
ALL_GROUPS = ("aligned_conditions",) + PENDING_GROUPS


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_json(data: bytes, source: str):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}: {source}")
            result[key] = value
        return result
    return json.loads(data.decode("utf-8"), object_pairs_hook=unique_keys)


def jsonl(data: bytes, source: str):
    for line_number, line in enumerate(data.splitlines(), 1):
        if not line.strip():
            raise ValueError(f"empty JSONL row: {source}:{line_number}")
        value = parse_json(line, f"{source}:{line_number}")
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row must be an object: {source}:{line_number}")
        yield line_number, value, line


def read_archive(archive: Path, expected_sha256: str | None = None):
    data = archive.read_bytes()
    digest = sha256(data)
    if expected_sha256 is not None and digest != expected_sha256.lower():
        raise ValueError("archive SHA256 differs from the external expected digest")
    payload = {}
    with ZipFile(archive) as zipped:
        seen = set()
        for info in zipped.infolist():
            name = info.filename
            path = PurePosixPath(name)
            if (not path.parts or name in seen or path.is_absolute() or ".." in path.parts
                    or "\\" in name or ":" in path.parts[0]
                    or str(path) != name.rstrip("/")):
                raise ValueError(f"unsafe or duplicate ZIP member: {name}")
            seen.add(name)
            if info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"encrypted or symlink ZIP member: {name}")
            if not info.is_dir():
                # ZipFile.read verifies each member's CRC before returning bytes.
                payload[name] = zipped.read(info)
        if CHECKPOINT_MEMBER not in payload:
            raise ValueError("embedded checkpoint manifest is missing")
        checkpoint = parse_json(payload[CHECKPOINT_MEMBER], CHECKPOINT_MEMBER)
        entries = checkpoint.get("entries")
        if not isinstance(entries, dict):
            raise ValueError("embedded checkpoint entries must be a dictionary")
        if set(entries) != set(payload) - {CHECKPOINT_MEMBER}:
            raise ValueError("ZIP payload and embedded manifest members differ")
        for name, spec in entries.items():
            if (not isinstance(spec, dict) or spec.get("size_bytes") != len(payload[name])
                    or spec.get("sha256") != sha256(payload[name])):
                raise ValueError(f"ZIP member fails embedded size/SHA256 check: {name}")
        if checkpoint.get("payload_file_count", len(entries)) != len(entries):
            raise ValueError("embedded payload file count differs")
        if checkpoint.get("uncompressed_payload_bytes", sum(map(len, (payload[x] for x in entries)))) != sum(len(payload[x]) for x in entries):
            raise ValueError("embedded payload byte count differs")
    for member in HANDOFF_MEMBERS:
        if member not in payload:
            raise ValueError(f"selected handoff member missing: {member}")
    return digest, checkpoint, payload


def unique_index(rows: list[dict], key: str, source: str) -> dict:
    result = {}
    for row in rows:
        identity = row[key]
        if identity in result:
            raise ValueError(f"duplicate {key} in {source}: {identity}")
        result[identity] = row
    return result


def verify_source(ref: dict, expected: dict, registry: dict, location: str):
    if not isinstance(ref, dict) or not isinstance(expected, dict):
        raise ValueError(f"missing source reference: {location}")
    for key in ("raw_file", "sha256"):
        if ref.get(key) != expected.get(key) or not ref.get(key):
            raise ValueError(f"source {key} binding differs: {location}")
    if registry.get(ref["raw_file"]) != ref["sha256"]:
        raise ValueError(f"source not bound to generic input manifest: {location}")


def validate_condition(condition: dict, axis: dict, row: dict, registry: dict, location: str):
    for upstream_key, input_key in (("axis_label", "axis_label"), ("selected_value", "value"),
                                    ("family_values", "family_values"), ("value_span", "value_span")):
        if condition.get(upstream_key) != axis.get(input_key):
            raise ValueError(f"whole-axis {upstream_key} differs: {location}")
    if "axis_label_span" in condition and condition["axis_label_span"] != axis.get("axis_label_span"):
        raise ValueError(f"whole-axis label span differs: {location}")
    for field in ("axis_label_span", "value_span"):
        if field == "axis_label_span" and axis.get(field) is None:
            continue
        verify_source(axis.get(field), axis.get(field), registry, location)
    if "au_value" in condition:
        name = condition.get("axis", {}).get("au_axis")
        option = condition.get("mapping", {}).get("option", {})
        if option and (option.get("axis_name") != name or option.get("value") != condition["au_value"]):
            raise ValueError(f"upstream AU mapping fields disagree: {location}")
        candidates = [a for a in row["axes"] if a["axis_name"] == name and a["value"] == condition["au_value"]]
        if len(candidates) != 1 or condition.get("au_value_span") != candidates[0].get("value_span"):
            raise ValueError(f"mapped AU value/span is not on the fixed row: {location}")
        verify_source(condition["au_value_span"], candidates[0]["value_span"], registry, location)


def fixed_au_condition(condition: dict, row: dict, registry: dict, location: str) -> dict:
    """Resolve a producer's complete AU-only value to this exact fixed AU row."""
    if not isinstance(condition, dict) or not isinstance(condition.get("axis_name"), str) or not isinstance(condition.get("value"), str):
        raise ValueError(f"AU-only condition lacks axis_name/value: {location}")
    candidates = [axis for axis in row["axes"]
                  if axis["axis_name"] == condition["axis_name"] and axis["value"] == condition["value"]]
    if len(candidates) != 1 or condition.get("value_span") != candidates[0].get("value_span"):
        raise ValueError(f"AU-only axis/value/span is not on the fixed row: {location}")
    axis = candidates[0]
    verify_source(condition["value_span"], axis["value_span"], registry, location)
    if axis.get("axis_name_span") is not None:
        verify_source(axis["axis_name_span"], axis["axis_name_span"], registry, location)
    return axis


def prepare(archive: Path, input_dir: Path, output_dir: Path,
            expected_sha256: str | None = None, validate_only: bool = False) -> dict:
    if output_dir.exists():
        raise FileExistsError(output_dir)
    archive_sha, checkpoint, payload = read_archive(archive, expected_sha256)
    input_bytes = {name: (input_dir / name).read_bytes()
                   for name in ("cases.jsonl", "products.jsonl", "manifest.json")}
    cases = unique_index([r for _, r, _ in jsonl(input_bytes["cases.jsonl"], "cases.jsonl")], "case_id", "cases.jsonl")
    products = unique_index([r for _, r, _ in jsonl(input_bytes["products.jsonl"], "products.jsonl")], "dossier_id", "products.jsonl")
    input_manifest = parse_json(input_bytes["manifest.json"], "manifest.json")
    registry = input_manifest["source_raw_sha256"]
    for name, digest in input_manifest.get("output_sha256", {}).items():
        if name in input_bytes and sha256(input_bytes[name]) != digest:
            raise ValueError(f"generic input differs from its frozen output SHA: {name}")
    cards, contexts, reverse, original_lines = [], [], [], []
    seen_rows, represented_cases, counts = set(), set(), Counter()
    member_sources, omitted_groups = [], {}
    for member in HANDOFF_MEMBERS:
        cohort = PurePosixPath(member).parts[-3]
        summary_name = str(PurePosixPath(member).with_name("summary.json"))
        summary = parse_json(payload[summary_name], summary_name) if summary_name in payload else None
        if summary and summary.get("outputs_sha256", {}).get("handoff.jsonl") != sha256(payload[member]):
            raise ValueError(f"upstream summary handoff SHA differs: {member}")
        decisions_name = str(PurePosixPath(member).with_name("decisions.jsonl"))
        if decisions_name in payload:
            if summary and summary.get("outputs_sha256", {}).get("decisions.jsonl") != sha256(payload[decisions_name]):
                raise ValueError(f"upstream summary decisions SHA differs: {member}")
            omitted = [r for _, r, _ in jsonl(payload[decisions_name], decisions_name)
                       if r.get("reason") == "symmetric_condition_unresolved"]
            for decision in omitted:
                if decision.get("case_id") not in cases:
                    raise ValueError(f"omitted upstream case is not in generic inputs: {decisions_name}")
            omitted_groups[cohort] = {"case_count": len(omitted), "processed_by_this_importer": False,
                                      "source_member": decisions_name, "sha256": sha256(payload[decisions_name])}
        member_sources.append({"member": member, "sha256": sha256(payload[member]),
                               "size_bytes": len(payload[member]), "producer_summary": summary})
        for line_number, handoff, original_line in jsonl(payload[member], member):
            location = f"{member}:{line_number}"
            case = cases.get(handoff.get("case_id"))
            if case is None:
                raise ValueError(f"handoff case is not in generic inputs: {location}")
            for key in ("dossier_id", "au_product_id"):
                if handoff.get(key) != case.get(key):
                    raise ValueError(f"fixed {key} differs: {location}")
            if case.get("cohort", cohort) != cohort:
                raise ValueError(f"handoff cohort differs: {location}")
            product = products.get(case["dossier_id"])
            if product is None or product["au"]["product_id"] != case["au_product_id"]:
                raise ValueError(f"fixed AU product not bound to dossier: {location}")
            rows = unique_index(product["au"]["rows"], "row_key", location)
            row = rows.get(handoff.get("au_row_key"))
            if row is None:
                raise ValueError(f"AU row is not on the fixed product: {location}")
            identity = (case["case_id"], row["row_key"])
            if identity in seen_rows:
                raise ValueError(f"duplicate handoff case/row: {location}")
            seen_rows.add(identity)
            represented_cases.add(case["case_id"])
            sku = handoff.get("rakuten_sku", {})
            for key in ("source_sku_key", "sku_record_key", "variant_id", "url"):
                if sku.get(key) != case["rakuten"].get(key) or not sku.get(key):
                    raise ValueError(f"Rakuten SKU {key} differs: {location}")
            verify_source(handoff.get("au_product_source"), product["au"].get("title_source"), registry, location)
            rakuten_source = product["rakuten"].get("title_source")
            if rakuten_source is None:
                rakuten_source = next((a.get("value_span") for a in case["rakuten"]["axes"] if a.get("value_span")), None)
            verify_source(sku, rakuten_source, registry, location)
            axes = unique_index(case["rakuten"]["axes"], "axis_key", location)
            if len({a["axis_index"] for a in axes.values()}) != len(axes):
                raise ValueError(f"duplicate input axis index: {location}")
            processed = set()
            source = {"archive": str(archive.resolve()), "archive_sha256": archive_sha,
                      "member": member, "member_sha256": sha256(payload[member]),
                      "line": line_number, "line_sha256": sha256(original_line)}
            context_line = len(contexts) + 1
            context = {"case_id": case["case_id"], "au_row_key": row["row_key"],
                       "au_product_id": case["au_product_id"], "source_sku_key": sku["source_sku_key"],
                       "source_handoff": source, "aligned_conditions": [],
                       "upstream_alignment_is_proof": False, "final_sku_adoption": "not_decided"}
            for group in ALL_GROUPS:
                conditions = handoff.get(group, [])
                if not isinstance(conditions, list):
                    raise ValueError(f"condition group must be a list: {location}:{group}")
                counts[group] += len(conditions)
                for condition in conditions:
                    axis_key = condition.get("axis_key")
                    if axis_key not in axes or axis_key in processed:
                        raise ValueError(f"unknown or repeated Rakuten axis: {location}:{axis_key}")
                    processed.add(axis_key)
                    axis = axes[axis_key]
                    validate_condition(condition, axis, row, registry, location)
                    if group in ("aligned_conditions", "extra_in_value_conditions") and "au_value" not in condition:
                        raise ValueError(f"mapped condition lacks its complete AU value: {location}")
                    if group == "aligned_conditions":
                        context[group].append({"producer_condition": condition, "raw_condition": axis,
                                               "status": "upstream_alignment_not_independent_proof"})
                        continue
                    card = {"case_id": case["case_id"], "au_row_key": row["row_key"],
                                  "condition_id": f"claude-whole-axis:{axis['axis_index']}",
                                  "axis_name": axis["axis_label"], "selected_value": axis["value"],
                                  "option_values": axis["family_values"], "raw_condition": axis,
                                  "source_refs": [axis.get("axis_label_span"), axis["value_span"]],
                                  "source_sku_key": sku["source_sku_key"], "au_product_id": case["au_product_id"],
                                  "direction": "rakuten_to_au", "source_handoff": source,
                                  "producer_group": group, "producer_condition": condition,
                                  "upstream_context_ref": {"file": "upstream_context.jsonl", "line": context_line}}
                    if group == "extra_in_value_conditions":
                        # Every incomplete correspondence needs both whole values,
                        # regardless of which value happens to be longer or contains
                        # a familiar word. No direction is inferred from text length.
                        au_axis_name = condition["axis"]["au_axis"]
                        au_axis = next(a for a in row["axes"] if a["axis_name"] == au_axis_name and a["value"] == condition["au_value"])
                        au_options = list(dict.fromkeys(a["value"] for candidate in product["au"]["rows"]
                                                        for a in candidate["axes"] if a["axis_name"] == au_axis_name))
                        reverse.append({"case_id": case["case_id"], "au_row_key": row["row_key"],
                                        "au_product_id": case["au_product_id"], "source_sku_key": sku["source_sku_key"],
                                        "direction": "au_to_rakuten", "origin": "extra_in_value_conditions",
                                        "axis_name": au_axis_name, "selected_value": au_axis["value"],
                                        "option_values": au_options, "raw_condition": au_axis,
                                        "source_refs": [au_axis.get("axis_name_span"), au_axis["value_span"]],
                                        "parent_condition_ref": {"case_id": case["case_id"], "au_row_key": row["row_key"],
                                                                 "condition_id": card["condition_id"]},
                                        "producer_condition": condition, "source_handoff": source,
                                        "status": "not_evaluated", "verification_required": True,
                                        "upstream_context_ref": card["upstream_context_ref"]})
                        counts["extra_in_value_reverse_conditions"] += 1
                        card["reverse_condition_ref"] = {"file": "reverse_conditions.jsonl", "line": len(reverse)}
                    cards.append(card)
            if processed != set(axes):
                raise ValueError(f"Rakuten axis coverage incomplete: {location}; missing={sorted(set(axes)-processed)}")
            counts["covered_rakuten_axes"] += len(processed)
            only_au = handoff.get("au_only_varying_conditions", [])
            if not isinstance(only_au, list):
                raise ValueError(f"AU-only conditions must be a list: {location}")
            counts["au_only_varying_conditions"] += len(only_au)
            seen_au_only = set()
            for condition in only_au:
                au_axis = fixed_au_condition(condition, row, registry, location)
                au_axis_name = au_axis["axis_name"]
                if au_axis_name in seen_au_only:
                    raise ValueError(f"repeated AU-only axis: {location}:{au_axis_name}")
                seen_au_only.add(au_axis_name)
                au_options = list(dict.fromkeys(a["value"] for candidate in product["au"]["rows"]
                                                for a in candidate["axes"] if a["axis_name"] == au_axis_name))
                reverse.append({"case_id": case["case_id"], "au_row_key": row["row_key"],
                                "au_product_id": case["au_product_id"], "source_sku_key": sku["source_sku_key"],
                                "direction": "au_to_rakuten", "producer_condition": condition,
                                "condition_id": f"claude-au-only-whole-axis:{row['axes'].index(au_axis)}",
                                "axis_name": au_axis_name, "selected_value": au_axis["value"],
                                "option_values": au_options, "raw_condition": au_axis,
                                "source_refs": [au_axis.get("axis_name_span"), au_axis["value_span"]],
                                "origin": "au_only_varying_conditions", "source_handoff": source,
                                "status": "not_evaluated", "verification_required": True,
                                "upstream_context_ref": {"file": "upstream_context.jsonl", "line": context_line}})
            contexts.append(context)
            original_lines.append(original_line)
    # Check frozen inputs again before creating any output. Raw file bytes are not
    # re-read here; their refs are checked against the separately audited registry.
    if sha256(archive.read_bytes()) != archive_sha or any((input_dir / n).read_bytes() != b for n, b in input_bytes.items()):
        raise ValueError("archive or generic inputs changed during validation")
    encode = lambda rows: b"".join((json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n").encode() for r in rows)
    outputs = {"cards.jsonl": encode(cards), "handoff_rows.jsonl": b"\n".join(original_lines) + (b"\n" if original_lines else b""),
               "upstream_context.jsonl": encode(contexts), "reverse_conditions.jsonl": encode(reverse)}
    manifest = {"schema_version": "claude_alignment_whole_axis_cards_v1", "labels_read": False,
                "model_inference_run": False, "legacy_rule_mask_used": False, "no_type_filter": True,
                "whole_axis_values_preserved": True, "upstream_alignment_is_proof": False,
                "final_sku_adoption": "not_decided", "raw_source_bytes_reverified": False,
                "raw_source_bindings_verified_against_input_manifest": True,
                "upstream_pr": UPSTREAM_PR, "upstream_head": UPSTREAM_HEAD,
                "selected_arm": SELECTED_ARM, "archive_sha256": archive_sha,
                "external_expected_sha256": expected_sha256, "embedded_checkpoint_sha256": sha256(payload[CHECKPOINT_MEMBER]),
                "upstream_label_derived_content_disclosure": checkpoint.get("label_derived_content"),
                "unprocessed_upstream_groups": {"symmetric_condition_unresolved": omitted_groups},
                "unprocessed_upstream_case_count": sum(x["case_count"] for x in omitted_groups.values()),
                "warnings": (["Upstream symmetric_condition_unresolved cases are omitted from handoff; separate recovery is required."]
                             if any(x["case_count"] for x in omitted_groups.values()) else []),
                "archive_member_count_verified": len(payload), "archive_members_verified": checkpoint["entries"],
                "selected_handoff_sources": member_sources, "code_sha256": sha256(Path(__file__).read_bytes()),
                "input_sha256": {n: sha256(b) for n, b in input_bytes.items()},
                "input_case_count": len(cases), "input_product_count": len(products),
                "handoff_row_count": len(original_lines), "handoff_case_count": len(represented_cases),
                "input_cases_without_handoff": len(set(cases)-represented_cases), "card_count": len(cards),
                "reverse_condition_count": len(reverse), "condition_counts": dict(counts),
                "reverse_verification_required": bool(reverse),
                "output_sha256": {n: sha256(b) for n, b in outputs.items()}, "validation_only": validate_only}
    if not validate_only:
        output_dir.mkdir(parents=True, exist_ok=False)
        for name, data in outputs.items():
            with (output_dir / name).open("xb") as stream:
                stream.write(data)
        with (output_dir / "manifest.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, default=ROOT / ".lab-output/sku-generic-model-inputs-20261010-v2")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--validate-only", action="store_true")
    try:
        manifest = prepare(**vars(parser.parse_args()))
    except (ValueError, KeyError, BadZipFile, FileExistsError) as error:
        parser.exit(2, f"{type(error).__name__}: {error}\n")
    print(json.dumps({key: manifest[key] for key in ("archive_sha256", "input_case_count", "handoff_row_count", "card_count", "reverse_condition_count", "unprocessed_upstream_case_count", "condition_counts", "validation_only")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
