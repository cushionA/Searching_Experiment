#!/usr/bin/env python3
"""Bind Claude's pinned flat v2/v3/v4 handoff to frozen fixed-page SKU inputs.

No labels, inference, source code execution, routing or final SKU adoption. Whole
values survive partial correspondence; AU obligations stay in a separate direction.
Original JSONL bytes, including the producer's missing-value NaN, are preserved.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
import math
from pathlib import Path, PurePosixPath
import stat
import unicodedata
from zipfile import BadZipFile, ZipFile

from import_claude_alignment_handoff_v1 import parse_json, sha256, unique_index, verify_source

ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT_MEMBER = "SKU_GATE_CHECKPOINT.json"
RUN = ".lab-output/sku-align-20261011-v2"
COHORTS = ("legacy", "family")
HANDOFF_MEMBERS = tuple(f"{RUN}/{cohort}/align/handoff.jsonl" for cohort in COHORTS)
UPSTREAM_PR = "https://github.com/cushionA/Searching_Experiment/pull/26"
UPSTREAM_HEAD = "c4c68342a9e23ada310c40761cae6c5cf84cfc8c"
ARCHIVE_SHA = "81c0a3b6e72c907068183e2f70bd604d0a8c6932d203e42a6436a80928573efc"
UPSTREAM_V3_HEAD = "86b5e3b7612f88185be639b86a88233a7c06f431"
ARCHIVE_V3_SHA = "46388080b7ab0abfd56629760cb21871e376a6853b6b1e8de99009a70ae5d6d4"
UPSTREAM_V4_HEAD = "2c2db5c52c328405e2f4249f424f2970b4ceca5a"
ARCHIVE_V4_SHA = "7dd4a51cbd45fbe93502b69c06204a92cb66b6394c3cb32fca98e23c1c64e2d9"
PINNED_RUNS = {
    UPSTREAM_HEAD: (RUN, ARCHIVE_SHA),
    UPSTREAM_V3_HEAD: (".lab-output/sku-align-20261011-v3", ARCHIVE_V3_SHA),
    UPSTREAM_V4_HEAD: (".lab-output/sku-align-20261011-v4", ARCHIVE_V4_SHA),
}
STATES = {"aligned", "one_sided", "extra_in_value"}
VERSION_STATES = {
    UPSTREAM_HEAD: STATES,
    UPSTREAM_V3_HEAD: STATES,
    UPSTREAM_V4_HEAD: {"aligned", "one_sided", "model_candidate"},
}


def jsonl(data: bytes, source: str):
    for number, line in enumerate(data.splitlines(), 1):
        value = parse_json(line, f"{source}:{number}")
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row must be an object: {source}:{number}")
        yield number, value, line


def strict_json(value):
    """Serialization validation also rejects NaN/Infinity in nested payloads."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def read_archive(path: Path, expected_sha256: str, handoff_members=HANDOFF_MEMBERS):
    body = path.read_bytes()
    digest = sha256(body)
    if digest != expected_sha256.lower():
        raise ValueError("archive SHA256 differs from the external expected digest")
    payload, seen = {}, set()
    with ZipFile(path) as zipped:
        for info in zipped.infolist():
            name, member = info.filename, PurePosixPath(info.filename)
            if (not member.parts or name in seen or member.is_absolute() or ".." in member.parts
                    or "\\" in name or ":" in member.parts[0] or str(member) != name.rstrip("/")):
                raise ValueError(f"unsafe or duplicate ZIP member: {name}")
            seen.add(name)
            if info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"encrypted or symlink ZIP member: {name}")
            if not info.is_dir():
                payload[name] = zipped.read(info)  # verifies CRC before returning
    checkpoint = parse_json(payload[CHECKPOINT_MEMBER], CHECKPOINT_MEMBER)
    entries = checkpoint.get("entries")
    if not isinstance(entries, dict) or set(entries) != set(payload) - {CHECKPOINT_MEMBER}:
        raise ValueError("ZIP payload and embedded manifest members differ")
    for name, spec in entries.items():
        if (not isinstance(spec, dict) or spec.get("size_bytes") != len(payload[name])
                or spec.get("sha256") != sha256(payload[name])):
            raise ValueError(f"ZIP member fails embedded size/SHA256 check: {name}")
    if (checkpoint.get("payload_file_count") != len(entries)
            or checkpoint.get("uncompressed_payload_bytes") != sum(len(payload[n]) for n in entries)):
        raise ValueError("embedded payload totals differ")
    for member in handoff_members:
        if member not in payload:
            raise ValueError(f"pinned handoff member missing: {member}")
        if payload[member] and not payload[member].endswith(b"\n"):
            raise ValueError(f"original JSONL lacks terminal newline: {member}")
    return digest, checkpoint, payload


def normalize_condition(condition: dict, location: str) -> dict:
    if not isinstance(condition, dict):
        raise ValueError(f"flat condition must be an object: {location}")
    result = copy.deepcopy(condition)
    value = result.get("au_value")
    if isinstance(value, float) and not math.isfinite(value):
        if (not math.isnan(value) or result.get("kind") != "rakuten"
                or result.get("status") != "one_sided" or result.get("au_axis") is not None):
            raise ValueError(f"nonfinite AU value is not a one-sided missing value: {location}")
        result["au_value"] = None
        result["producer_missing_au_value_was_nan"] = True
    strict_json(result)
    return result


def mapped_au_axis(condition: dict, row: dict, registry: dict, location: str) -> dict:
    if not isinstance(condition.get("au_axis"), str) or not isinstance(condition.get("au_value"), str):
        raise ValueError(f"mapped AU condition lacks whole axis/value: {location}")
    axes = [axis for axis in row["axes"] if axis["axis_name"] == condition["au_axis"]
            and axis["value"] == condition["au_value"]]
    if len(axes) != 1:
        raise ValueError(f"mapped AU value is not on the fixed row: {location}")
    axis = axes[0]
    for field in ("axis_name_span", "value_span"):
        if axis.get(field) is not None:
            verify_source(axis[field], axis[field], registry, location)
    if "au_value_span" in condition and condition["au_value_span"] != axis["value_span"]:
        raise ValueError(f"mapped AU span differs from fixed row: {location}")
    return axis


def reverse_condition(condition: dict, au_axis: dict, row: dict, product: dict,
                      common: dict, origin: str, parent: dict | None) -> dict:
    options = list(dict.fromkeys(axis["value"] for candidate in product["au"]["rows"]
                               for axis in candidate["axes"] if axis["axis_name"] == au_axis["axis_name"]))
    return {**common, "condition_id": f"claude-v2-au-whole-axis:{row['axes'].index(au_axis)}",
            "direction": "au_to_rakuten", "origin": origin, "axis_name": au_axis["axis_name"],
            "selected_value": au_axis["value"], "option_values": options,
            "raw_condition": au_axis, "source_refs": [au_axis.get("axis_name_span"), au_axis["value_span"]],
            "producer_condition": condition, "parent_condition_ref": parent,
            "status": "not_evaluated", "verification_required": True,
            "automatic_adoption_allowed": False}


def prepare(archive: Path, input_dir: Path, output_dir: Path,
            expected_sha256: str | None = None, expected_head: str = UPSTREAM_HEAD,
            validate_only: bool = False) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    if expected_head not in PINNED_RUNS:
        raise ValueError("upstream head is not a known pinned flat handoff version")
    run, default_sha = PINNED_RUNS[expected_head]
    is_v4 = expected_head == UPSTREAM_V4_HEAD
    allowed_states = VERSION_STATES[expected_head]
    expected_sha256 = expected_sha256 or default_sha
    handoff_members = tuple(f"{run}/{cohort}/align/handoff.jsonl" for cohort in COHORTS)
    archive_sha, checkpoint, payload = read_archive(archive, expected_sha256, handoff_members)
    inputs = {name: (input_dir / name).read_bytes() for name in ("cases.jsonl", "products.jsonl", "manifest.json")}
    cases = unique_index([row for _, row, _ in jsonl(inputs["cases.jsonl"], "cases.jsonl")], "case_id", "cases.jsonl")
    products = unique_index([row for _, row, _ in jsonl(inputs["products.jsonl"], "products.jsonl")], "dossier_id", "products.jsonl")
    input_manifest = parse_json(inputs["manifest.json"], "manifest.json")
    registry = input_manifest["source_raw_sha256"]
    for name in ("cases.jsonl", "products.jsonl"):
        if input_manifest.get("output_sha256", {}).get(name) != sha256(inputs[name]):
            raise ValueError(f"generic input differs from its frozen output SHA: {name}")
    cards, contexts, reverse = [], [], []
    counts, seen_rows, represented = Counter(), set(), set()
    sources, omissions, cohort_counts = [], {}, {}
    uncovered_au = Counter()
    for cohort, member in zip(COHORTS, handoff_members):
        member_sha256 = sha256(payload[member])
        parent = PurePosixPath(member).parent
        summary_name, decisions_name = str(parent / "summary.json"), str(parent / "decisions.jsonl")
        metadata_name = f"{run}/{cohort}/similarities.json"
        summary = parse_json(payload[summary_name], summary_name)
        metadata = parse_json(payload[metadata_name], metadata_name)
        for name, digest in summary["outputs_sha256"].items():
            target = str(parent / name)
            if target not in payload or sha256(payload[target]) != digest:
                raise ValueError(f"upstream summary output SHA differs: {target}")
        similarity_member = f"{run}/{cohort}/similarities.jsonl"
        if (metadata.get("similarities_sha256") != sha256(payload[similarity_member])
                or summary.get("similarities_sha256") != metadata["similarities_sha256"]):
            raise ValueError(f"similarity metadata binding differs: {metadata_name}")
        declared_inputs = metadata.get("inputs_sha256", {})
        if set(declared_inputs) != {"cases.jsonl", "products.jsonl"} or any(
                not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
                for value in declared_inputs.values()):
            raise ValueError(f"producer input SHA declaration invalid: {metadata_name}")
        decisions = unique_index([row for _, row, _ in jsonl(payload[decisions_name], decisions_name)], "case_id", decisions_name)
        for identity, decision in decisions.items():
            case = cases.get(identity)
            if (case is None or case.get("cohort") != cohort or decision.get("dossier_id") != case["dossier_id"]):
                raise ValueError(f"decision fixed-case binding differs: {decisions_name}:{identity}")
        omitted = [identity for identity, row in decisions.items() if row.get("reason") == "symmetric_condition_unresolved"]
        omissions[cohort] = {"case_count": len(omitted), "case_ids": omitted,
                             "processed_by_this_importer": False, "source_member": decisions_name,
                             "sha256": sha256(payload[decisions_name])}
        sources.append({"member": member, "sha256": member_sha256, "size_bytes": len(payload[member]),
                        "producer_summary": summary, "similarities_metadata": metadata,
                        "producer_input_bytes_reverified": False})
        member_rows = 0
        initial_cards, initial_reverse = len(cards), len(reverse)
        cohort_cases = set()
        for number, handoff, original_line in jsonl(payload[member], member):
            member_rows += 1
            location = f"{member}:{number}"
            case = cases.get(handoff.get("case_id"))
            if case is None or case.get("cohort") != cohort:
                raise ValueError(f"handoff case/cohort differs: {location}")
            if handoff["case_id"] not in decisions or handoff["case_id"] in omitted:
                raise ValueError(f"handoff case missing decision or marked omitted: {location}")
            for key in ("dossier_id", "au_product_id"):
                if handoff.get(key) != case.get(key):
                    raise ValueError(f"fixed {key} differs: {location}")
            product = products.get(case["dossier_id"])
            if product is None or product["au"]["product_id"] != case["au_product_id"]:
                raise ValueError(f"fixed AU product not bound to dossier: {location}")
            row = unique_index(product["au"]["rows"], "row_key", location).get(handoff.get("au_row_key"))
            if row is None:
                raise ValueError(f"AU row is not on the fixed product: {location}")
            identity = (case["case_id"], row["row_key"])
            if identity in seen_rows:
                raise ValueError(f"duplicate handoff case/row: {location}")
            seen_rows.add(identity)
            represented.add(case["case_id"])
            cohort_cases.add(case["case_id"])
            sku = handoff["rakuten_sku"]
            for key in ("source_sku_key", "sku_record_key", "variant_id", "url"):
                if not sku.get(key) or sku.get(key) != case["rakuten"].get(key):
                    raise ValueError(f"Rakuten SKU {key} differs: {location}")
            verify_source(handoff["au_product_source"], product["au"]["title_source"], registry, location)
            axes = unique_index(case["rakuten"]["axes"], "axis_key", location)
            if len({axis["axis_index"] for axis in axes.values()}) != len(axes):
                raise ValueError(f"duplicate input axis index: {location}")
            source = product["rakuten"].get("title_source") or next(a["value_span"] for a in axes.values())
            verify_source(sku, source, registry, location)
            raw_axes = handoff.get("rakuten_axes")
            if not isinstance(raw_axes, dict) or set(raw_axes) != set(axes):
                raise ValueError(f"Rakuten source axis coverage differs: {location}")
            for key, axis in axes.items():
                for field in ("family_values", "value_span"):
                    if raw_axes[key].get(field) != axis.get(field):
                        raise ValueError(f"whole-axis {field} differs: {location}:{key}")
                if "axis_label_span" in raw_axes[key] and raw_axes[key]["axis_label_span"] != axis.get("axis_label_span"):
                    raise ValueError(f"whole-axis label span differs: {location}:{key}")
                for field in ("axis_label_span", "value_span"):
                    if axis.get(field) is not None:
                        verify_source(axis[field], axis[field], registry, location)
            source_ref = {"archive": str(archive.resolve()), "archive_sha256": archive_sha,
                          "member": member, "member_sha256": member_sha256,
                          "line": number, "line_sha256": sha256(original_line)}
            common = {"case_id": case["case_id"], "dossier_id": case["dossier_id"],
                      "au_product_id": case["au_product_id"], "au_row_key": row["row_key"],
                      "source_sku_key": sku["source_sku_key"], "source_handoff": source_ref,
                      "upstream_context_ref": {"file": "upstream_context.jsonl", "line": len(contexts) + 1}}
            context = {**common, "aligned_conditions": [], "upstream_alignment_is_proof": False,
                       "final_sku_adoption": "not_decided"}
            processed, mapped, only_au = set(), set(), set()
            conditions = handoff.get("conditions")
            if not isinstance(conditions, list):
                raise ValueError(f"flat conditions must be a list: {location}")
            conditions = [normalize_condition(condition, location) for condition in conditions]
            strict_json({**handoff, "conditions": conditions})
            for condition in conditions:
                kind, status = condition.get("kind"), condition.get("status")
                if kind not in ("rakuten", "au_only") or status not in allowed_states:
                    raise ValueError(f"unsupported condition kind/status: {location}:{kind}/{status}")
                if is_v4 and "matched_by" in condition:
                    raise ValueError(f"v4 condition uses retired matched_by field: {location}")
                if expected_head == UPSTREAM_V3_HEAD:
                    method = condition.get("matched_by")
                    if ("matched_by" not in condition or
                            (status == "one_sided" and method is not None) or
                            (status != "one_sided" and method not in ("identical", "model"))):
                        raise ValueError(f"v3 condition alignment provenance differs: {location}")
                counts[f"{kind}:{status}"] += 1
                if kind == "au_only":
                    if (status != "one_sided" or condition.get("axis_key") is not None
                            or condition.get("value") is not None or condition.get("axis_label") != condition.get("au_axis")):
                        raise ValueError(f"AU-only condition schema differs: {location}")
                    au_axis = mapped_au_axis(condition, row, registry, location)
                    if au_axis["axis_name"] in only_au:
                        raise ValueError(f"repeated AU-only axis: {location}")
                    only_au.add(au_axis["axis_name"])
                    reverse.append(reverse_condition(condition, au_axis, row, product, common, "au_only", None))
                    continue
                key = condition.get("axis_key")
                if key not in axes or key in processed:
                    raise ValueError(f"unknown or repeated Rakuten axis: {location}:{key}")
                processed.add(key)
                axis = axes[key]
                if condition.get("axis_label") != axis["axis_label"] or condition.get("value") != axis["value"]:
                    raise ValueError(f"whole-axis label/value differs: {location}:{key}")
                for field in ("family_values", "value_span", "axis_label_span"):
                    if field in condition and condition[field] != axis.get(field):
                        raise ValueError(f"condition whole-axis {field} differs: {location}:{key}")
                if status == "one_sided":
                    if condition.get("au_axis") is not None or condition.get("au_value") is not None:
                        raise ValueError(f"one-sided condition has mapped AU value: {location}")
                    au_axis = None
                else:
                    au_axis = mapped_au_axis(condition, row, registry, location)
                    if au_axis["axis_name"] in mapped:
                        raise ValueError(f"multiple Rakuten axes map the same AU axis: {location}")
                    mapped.add(au_axis["axis_name"])
                if status == "aligned":
                    if is_v4 and ("".join(unicodedata.normalize("NFKC", axis["value"]).split()) !=
                                  "".join(unicodedata.normalize("NFKC", au_axis["value"]).split())):
                        raise ValueError(f"v4 aligned condition is not an identical whole-value link: {location}")
                    context["aligned_conditions"].append({"producer_condition": condition, "raw_condition": axis,
                                                          "mapped_au_condition": au_axis,
                                                          "status": "upstream_alignment_not_independent_proof"})
                    continue
                card = {**common, "condition_id": f"claude-v2-whole-axis:{axis['axis_index']}",
                        "direction": "rakuten_to_au", "axis_name": axis["axis_label"],
                        "selected_value": axis["value"], "option_values": axis["family_values"],
                        "raw_condition": axis, "source_refs": [axis.get("axis_label_span"), axis["value_span"]],
                        "producer_condition": condition, "producer_status": status}
                if status in ("extra_in_value", "model_candidate"):
                    ref = {key: card[key] for key in ("case_id", "au_row_key", "condition_id")}
                    reverse.append(reverse_condition(condition, au_axis, row, product, common, status, ref))
                    card["reverse_condition_ref"] = {"file": "reverse_conditions.jsonl", "line": len(reverse)}
                if status == "model_candidate":
                    card.update(model_alignment_is_proof=False, verification_required=True,
                                automatic_adoption_allowed=False)
                cards.append(card)
            if processed != set(axes) or only_au & mapped:
                raise ValueError(f"condition axis coverage/overlap differs: {location}")
            if is_v4:
                # The producer exports AU-only axes only when they have several
                # values. Record any absent whole AU axes, without inventing
                # conditions or interpreting their values.
                missing_axes = {axis["axis_name"] for axis in row["axes"]} - mapped - only_au
                uncovered_au["handoff_rows_with_unrepresented_au_axes"] += bool(missing_axes)
                for name in missing_axes:
                    alternatives = {axis["value"] for candidate in product["au"]["rows"]
                                    for axis in candidate["axes"] if axis["axis_name"] == name}
                    uncovered_au["unrepresented_au_axis_occurrences"] += 1
                    uncovered_au["single_valued_au_axis_occurrences" if len(alternatives) == 1 else
                                 "multi_valued_au_axis_occurrences"] += 1
            counts["covered_rakuten_axes"] += len(processed)
            contexts.append(context)
        if summary.get("handoff_rows") != member_rows:
            raise ValueError(f"producer handoff row count differs: {member}")
        cohort_counts[cohort] = {"handoff_row_count": member_rows, "handoff_case_count": len(cohort_cases),
                                 "card_count": len(cards) - initial_cards,
                                 "reverse_condition_count": len(reverse) - initial_reverse}
    if sha256(archive.read_bytes()) != archive_sha or any((input_dir / name).read_bytes() != body for name, body in inputs.items()):
        raise ValueError("archive or generic inputs changed during validation")
    encode = lambda rows: b"".join((strict_json(row) + "\n").encode("utf-8") for row in rows)
    outputs = {"cards.jsonl": encode(cards), "upstream_context.jsonl": encode(contexts),
               "reverse_conditions.jsonl": encode(reverse),
               "original_handoff.jsonl": b"".join(payload[name] for name in handoff_members)}
    manifest = {"schema_version": "claude-flat-whole-axis-cards-v2", "upstream_pr": UPSTREAM_PR,
                "upstream_head": expected_head, "upstream_run": run,
                "head_binding": "externally pinned; archive does not embed a git head",
                "archive_sha256": archive_sha, "external_expected_sha256": expected_sha256,
                "embedded_checkpoint_sha256": sha256(payload[CHECKPOINT_MEMBER]),
                "archive_member_count_verified": len(payload), "archive_members_verified": checkpoint["entries"],
                "selected_handoff_sources": sources, "producer_input_bytes_reverified": False,
                "producer_input_binding": "every handoff case, SKU, fixed row and complete Rakuten axes compared directly to frozen generic inputs",
                "upstream_label_derived_content_disclosure": checkpoint.get("label_derived_content"),
                "labels_read": False, "model_inference_run": False, "legacy_rule_mask_used": False,
                "no_type_filter": True, "whole_axis_values_preserved": True, "original_handoff_bytes_preserved": True,
                "generated_json_nonfinite_values": False, "raw_source_bytes_reverified": False,
                "raw_source_bindings_verified_against_input_manifest": True, "upstream_alignment_is_proof": False,
                "final_sku_adoption": "not_decided", "reverse_verification_required": bool(reverse),
                "unprocessed_upstream_groups": {"symmetric_condition_unresolved": omissions},
                "unprocessed_upstream_case_count": sum(row["case_count"] for row in omissions.values()),
                "warnings": ["Producer input byte hashes are declarations; their task-format bytes are not available here.",
                             "Symmetric unresolved cases omitted by the upstream handoff are not processed or adopted."],
                "code_sha256": sha256(Path(__file__).read_bytes()),
                "input_sha256": {name: sha256(body) for name, body in inputs.items()},
                "input_case_count": len(cases), "input_product_count": len(products),
                "handoff_row_count": len(contexts), "handoff_case_count": len(represented),
                "input_cases_without_handoff": len(set(cases) - represented), "card_count": len(cards),
                "reverse_condition_count": len(reverse), "condition_counts": dict(counts),
                "output_sha256": {name: sha256(body) for name, body in outputs.items()}, "validation_only": validate_only}
    if is_v4:
        candidate_member = f"{run}/candidate/align/handoff.jsonl"
        candidate_summary_member = f"{run}/candidate/align/summary.json"
        candidate_summary = (parse_json(payload[candidate_summary_member], candidate_summary_member)
                             if candidate_summary_member in payload else None)
        manifest.update(
            upstream_flat_version=4,
            upstream_checkpoint_purpose=checkpoint.get("purpose"),
            allowed_condition_states=sorted(allowed_states),
            model_candidate_is_proof=False,
            all_model_candidate_mapped_au_whole_values_require_reverse_verification=True,
            received_cohorts=list(COHORTS), received_cohort_counts=cohort_counts,
            upstream_label_informed_changes={cohort: source["producer_summary"].get("label_informed_changes")
                                            for cohort, source in zip(COHORTS, sources)},
            upstream_method_label_informed=any(source["producer_summary"].get("label_informed_changes") for source in sources),
            unreceived_cohorts={"candidate": {
                "handoff_member": candidate_member, "handoff_in_archive": candidate_member in payload,
                "processed_by_this_importer": False,
                "summary_member": candidate_summary_member if candidate_summary is not None else None,
                "summary_sha256": sha256(payload[candidate_summary_member]) if candidate_summary is not None else None,
                "declared_handoff_row_count": candidate_summary.get("handoff_rows") if candidate_summary is not None else None,
                "summary_row_count_is_unverified_declaration": True}},
            producer_au_only_export_limitation="AU-only export includes only axes with multiple values; singleton AU axes can be absent.",
            unrepresented_au_axis_counts=dict(uncovered_au))
        manifest["warnings"].extend([
            "V4 is label-informed: the upstream method changed after its author read machine value references.",
            "Model candidates are unresolved whole values in both directions, never correspondence proof.",
            "Only legacy/family handoffs are received; the candidate cohort handoff is not imported.",
            "The upstream AU-only export can omit singleton axes; absence is not proof that no AU obligation remains."])
    if not validate_only:
        output_dir.mkdir(parents=True, exist_ok=False)
        for name, body in outputs.items():
            with (output_dir / name).open("xb") as stream:
                stream.write(body)
        with (output_dir / "manifest.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, default=ROOT / ".lab-output/sku-generic-model-inputs-20261010-v2")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-sha256", help="defaults to the archive pin for --expected-head")
    parser.add_argument("--expected-head", default=UPSTREAM_HEAD)
    parser.add_argument("--validate-only", action="store_true")
    try:
        result = prepare(**vars(parser.parse_args()))
    except (OSError, ValueError, KeyError, TypeError, BadZipFile) as error:
        parser.exit(2, f"{type(error).__name__}: {error}\n")
    fields = ("archive_sha256", "handoff_row_count", "handoff_case_count", "card_count", "reverse_condition_count",
              "unprocessed_upstream_case_count", "condition_counts", "validation_only")
    print(json.dumps({key: result[key] for key in fields}, ensure_ascii=False))


if __name__ == "__main__":
    main()
