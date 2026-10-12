#!/usr/bin/env python3
"""Prepare blind, source-only final composition packets for a frozen subset.

This adapter reads only the predeclared subset, frozen test adapter documents,
requests, and task bindings, and the corresponding copied raw captures. It does
not open labels, predictions, titles as model context, or inference responses.
"""
from __future__ import annotations

from collections import defaultdict
import argparse
import codecs
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "claude_v9_gate"))

import composition_context_v1 as CONTEXT
import composition_evidence_v1 as EVIDENCE
import composition_source_blocks_v2 as WHOLE_DOC
import composition_source_blocks_v3 as TYPED
import sku_gate_sources as RAW

DEFAULT_BASE = ROOT / ".lab-output/sku-final-test-subset-20261013-v1"
DEFAULT_INPUTS = DEFAULT_BASE / "inputs"
DEFAULT_SOURCE = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
DEFAULT_COMPLETION = ROOT / ".lab-output/sku-final-test-subset-source-capture-completion-20261013-v1"
DEFAULT_OUT = DEFAULT_BASE / "composition-source-only"
FINAL_PROTOCOL_VERSION = "composition-final-exact-context-packet-v1"
FINAL_PROTOCOL = CONTEXT.PROTOCOL + """

Final evaluation packet and cache contract (composition-final-exact-context-packet-v1)
The model unit represents one complete input context. A packet may fan out only to requests whose exact context digest is identical across direction, raw axis name, ordered raw option values, target document SHA-256, and ordered exact target axes. Target-axis text is separately checked byte-identical within each group. No request with a different field may reuse this packet. The desired selected option is binding-only metadata and is absent from packets and model payloads. Task and request IDs remain in a separate fan-out map.

Use every eligible source-bound body block in the target document in original source order; do not rank or truncate. The unchanged composition exclusion rule removes linked blocks, keyword blocks, unbound table rows, and linked table context. Plain-text fallback passages are admitted only by the frozen source restoration checks, including the raw-leaf anchor and table-context guards. Full packet records retain exact canonical document quote_refs and verified raw span bindings; compact model payloads contain the block ID and passage without duplicated quote locator text.

Return one JSON object per unit with the unit_id, winning_value, evidence_block_ids, and short reason specified above. winning_value must be one exact raw option or null. A selected value must cite one or more block IDs present in that unit. Null requires an empty evidence_block_ids array. If no eligible source block exists, retain an explicit unknown unit without dispatch. Keep cases, task bindings, requests, unique context units, dispatched packets, and unknowns as separate denominators.
"""


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(Path(path).read_bytes())


def digest(value: Any) -> str:
    return sha_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), allow_nan=False).encode("utf-8"))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(path: Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_rows(path: Path, values: list[dict[str, Any]]) -> None:
    with Path(path).open("x", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False) + "\n")


def write_json(path: Path, value: dict[str, Any]) -> None:
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True,
                  allow_nan=False)
        stream.write("\n")


def replace_json(path: Path, value: dict[str, Any]) -> None:
    temporary = Path(path).with_suffix(Path(path).suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True,
                  allow_nan=False)
        stream.write("\n")
    temporary.replace(path)


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(child, key) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key(child, key) for child in value)
    return False


def finalize_existing_artifact(base: Path, inputs: Path, output: Path,
                               membership_path: Path, plan_path: Path,
                               completion_root: Path | None = DEFAULT_COMPLETION) -> dict[str, Any]:
    """Freeze protocol/coverage after source packet data already exists.

    This assembly-only step does not regenerate, alter, or infer from packets.
    It is used when a materialized input bundle omits optional inventory files.
    """
    base, inputs, output = map(Path, (base, inputs, output))
    membership_path, plan_path = Path(membership_path), Path(plan_path)
    completion_root = Path(completion_root) if completion_root else None
    if not output.is_dir() or (output / "manifest.json").exists():
        raise FileExistsError(output / "manifest.json")
    plan = read_json(plan_path)
    input_manifest = read_json(inputs / "manifest.json")
    membership = rows(membership_path)
    if (sha_file(membership_path) != plan.get("membership_sha256")
            or input_manifest.get("membership_sha256") != plan.get("membership_sha256")
            or len(membership) != plan.get("selected_case_count")):
        raise ValueError("finalization inputs differ from frozen membership")
    packets = rows(output / "packets.jsonl")
    payloads = rows(output / "model-payloads.jsonl")
    empty = rows(output / "unknown-unit-responses.jsonl")
    fanout = rows(output / "request-fanout.jsonl")
    if len(packets) + len(empty) != len(fanout):
        raise ValueError("packet, unknown, and context unit coverage differ")
    if {p["unit_id"] for p in packets} & {u["unit_id"] for u in empty}:
        raise ValueError("a context unit is both dispatched and explicit unknown")
    if any(_contains_key(item, "selected_value") or _contains_key(item, "title")
           for item in packets + payloads):
        raise ValueError("packet/payload contains forbidden title or selected_value field")
    requests_by_id = {row["request_id"]: row for row in rows(inputs / "requests.jsonl")}
    for row in fanout:
        bound_requests = [requests_by_id[rid] for rid in row["request_ids"]]
        keys = [cache_key(request) for request in bound_requests]
        if not keys or any(key != keys[0] for key in keys[1:]):
            raise ValueError("fan-out requests are not byte-identical complete contexts")
        axes_texts = {request.get("target_selected_axes_text") for request in bound_requests}
        if len(axes_texts) != 1 or digest(keys[0]) != row["cache_key_sha256"]:
            raise ValueError("fan-out target-axis text or exact cache digest mismatch")
    request_ids = [rid for row in fanout for rid in row["request_ids"]]
    task_ids = [tid for row in fanout for tid in row["selected_task_ids"]]
    case_ids = {case_id for row in fanout for case_id in row["selected_case_ids"]}
    if (len(request_ids) != len(set(request_ids))
            or len(task_ids) != len(set(task_ids))
            or len(request_ids) != input_manifest.get("request_count")
            or len(task_ids) != input_manifest.get("task_count")
            or case_ids != {row["case_id"] for row in membership}):
        raise ValueError("request/task/case fan-out coverage differs from frozen inputs")
    receipts = rows(output / "span-receipts.jsonl")
    if any(row.get("verified") is not True for row in receipts):
        raise ValueError("source receipts include an unverified span")
    source_review = read_json(output / "source-review.json")
    old_protocol_sha = sha_file(output / "protocol.json")
    payload_characters = sum(len(block["model_passage"])
                             for payload in payloads for block in payload["blocks"])
    source_review.update({
        "protocol_version": FINAL_PROTOCOL_VERSION,
        "exact_context_unit_denominator": len(fanout),
        "dispatched_packet_count": len(packets),
        "explicit_unknown_unit_count": len(empty),
        "model_payload_character_count": payload_characters,
        "compact_model_payload_bytes": (output / "model-payloads.jsonl").stat().st_size,
        "full_packet_bytes": (output / "packets.jsonl").stat().st_size,
        "full_packet_megabytes_decimal": round((output / "packets.jsonl").stat().st_size / 1_000_000, 3),
        "compact_payload_megabytes_decimal": round((output / "model-payloads.jsonl").stat().st_size / 1_000_000, 3),
        "verified_raw_span_receipt_count": len(receipts),
        "fanout_request_count": len(request_ids),
        "fanout_task_binding_count": len(task_ids),
        "fanout_case_count": len(case_ids),
        "protocol_cache_contract": "exact full-context digest only; selected desired value excluded; no cross-context reuse",
        "model_inference_run": False,
    })
    replace_json(output / "source-review.json", source_review)
    protocol = {"schema_version": FINAL_PROTOCOL_VERSION,
                "protocol": FINAL_PROTOCOL,
                "protocol_sha256": sha_bytes(FINAL_PROTOCOL.encode("utf-8")),
                "packet_schema": "composition-full-context-packet-v1",
                "model_payload_schema": "composition-full-context-compact-payload-v1",
                "context_cache_key_fields": ["direction", "axis_name", "ordered_option_values",
                                              "target_document_sha256", "ordered_target_selected_axes"],
                "axes_text_checked_byte_identical_within_group": True,
                "selected_desired_value_in_context_key_or_payload": False,
                "context_sharing_label": "paired exact-context cache; not genuine global sharing",
                "no_axes_policy": "global singleton unselected",
                "block_exclusion_reason": "unchanged composition_evidence_v1.exclusion_reason",
                "labels_or_predictions_read": False,
                "inference_started": False}
    write_json(output / "protocol-final.json", protocol)
    # Preserve the earlier protocol record as its own provenance file.
    (output / "protocol.json").rename(output / "protocol-dev-context-reference.json")
    (output / "protocol-final.json").rename(output / "protocol.json")
    write_json(output / "finalization-receipt.json", {
        "schema_version": "sku-final-composition-packet-assembly-freeze-v1",
        "previous_dev_context_protocol_sha256": old_protocol_sha,
        "final_protocol_sha256": sha_file(output / "protocol.json"),
        "source_packets_or_model_payloads_changed": False,
        "source_packet_content_sha256": sha_file(output / "packets.jsonl"),
        "compact_payload_content_sha256": sha_file(output / "model-payloads.jsonl"),
        "why_assembly_only": "The source-only builder completed packets and receipts; its manifest stage encountered an optional selected-source-manifest file absent from this second-stage bundle. Finalization adds the declared exact-context protocol and coverage freeze without rebuilding source or model inputs.",
        "labels_read": False, "prior_predictions_read": False, "inference_started": False})
    code_dir = output / "code"
    for path in (Path(__file__), HERE / "test_prepare_final_composition_packets_v1.py"):
        shutil.copyfile(path, code_dir / path.name)
    input_paths = {
        "subset_plan": plan_path, "subset_membership": membership_path,
        "materialized_inputs_manifest": inputs / "manifest.json",
        "materialized_documents": inputs / "documents.jsonl",
        "materialized_requests": inputs / "requests.jsonl",
        "materialized_tasks": inputs / "tasks.jsonl",
        "source_script": Path(__file__),
        "source_script_tests": HERE / "test_prepare_final_composition_packets_v1.py",
        "composition_evidence": Path(EVIDENCE.__file__),
        "context_packet_builder": Path(CONTEXT.__file__),
        "composition_context_policy": HERE / "composition_context_policy_v1.py",
        "whole_document_builder_v1": HERE / "composition_source_blocks_v1.py",
        "whole_document_builder_v2": Path(WHOLE_DOC.__file__),
        "typed_source_builder": Path(TYPED.__file__),
        "scoped_html_extractor": HERE / "build_scoped_html_blocks_v1.py",
        "scoped_html_passage_renderer": HERE / "prepare_scoped_html_trial_v1.py",
        "raw_source_verifier": HERE / "claude_v9_gate/sku_gate_sources.py",
    }
    if (inputs / "selected-source-manifest.jsonl").is_file():
        input_paths["selected_source_manifest"] = inputs / "selected-source-manifest.jsonl"
    if completion_root is not None:
        input_paths["source_capture_completion_manifest"] = completion_root / "resolution-manifest.json"
    code_snapshot = {path.name: {"path": f"code/{path.name}",
                                 "sha256": sha_file(code_dir / path.name)}
                     for path in code_dir.iterdir() if path.is_file()}
    output_names = [path.name for path in output.iterdir()
                    if path.is_file() and path.name != "manifest.json"]
    output_sha = {name: sha_file(output / name) for name in sorted(output_names)}
    write_json(output / "manifest.json", {
        "schema_version": "sku-final-composition-source-only-v1",
        "output_status": "source_packets_frozen_no_inference",
        "input_sha256": {name: sha_file(path) for name, path in input_paths.items()},
        "frozen_helper_sha256": {name: sha_file(path) for name, path in input_paths.items()
                                  if name in {"composition_evidence", "context_packet_builder",
                                              "composition_context_policy", "whole_document_builder_v1",
                                              "whole_document_builder_v2", "typed_source_builder",
                                              "scoped_html_extractor", "scoped_html_passage_renderer",
                                              "raw_source_verifier"}},
        "code_snapshot": code_snapshot,
        "output_sha256": output_sha,
        "protocol_sha256": sha_file(output / "protocol.json"),
        "source_review_sha256": sha_file(output / "source-review.json"),
        "finalization_receipt_sha256": sha_file(output / "finalization-receipt.json"),
        "source_review": source_review,
        "independent_test_labels_accessed": False,
        "full_test_prior_predictions_accessed": False,
        "inference_started": False,
    })
    return source_review


def raw_ref_values(value: Any, path: str = ""):
    if isinstance(value, dict):
        required = {"raw_file", "sha256", "locator", "start", "end", "quote"}
        if required.issubset(value):
            yield path, value
        for key, child in value.items():
            yield from raw_ref_values(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from raw_ref_values(child, f"{path}[{i}]")


def normalized_source_path(source_root: Path, raw_file: str) -> Path:
    # Path separator normalization only; preserve every source path component.
    return source_root / Path(raw_file.replace("\\", "/"))


def _html_encoding(data: bytes) -> str | None:
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if data.startswith(codecs.BOM_UTF16_LE):
        return "utf-16-le"
    if data.startswith(codecs.BOM_UTF16_BE):
        return "utf-16-be"
    for match in re.finditer(rb"<meta\b[^>]*charset\s*=\s*[\"']?\s*([a-zA-Z0-9._:-]+)",
                             data[:65536], re.IGNORECASE):
        try:
            return codecs.lookup(match.group(1).decode("ascii")).name
        except (LookupError, UnicodeDecodeError):
            continue
    for match in re.finditer(rb"charset\s*=\s*[\"']?\s*([a-zA-Z0-9._:-]+)",
                             data[:65536], re.IGNORECASE):
        try:
            return codecs.lookup(match.group(1).decode("ascii")).name
        except (LookupError, UnicodeDecodeError):
            continue
    return None


def build_whole_document_blocks(store_root: Path, documents: list[dict[str, Any]],
                                source_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the frozen v2 builder, adding encoding metadata only when absent.

    Some full-evaluation Rakuten documents have whole-document description
    routes but no title raw locator. Encoding is then taken from a BOM or HTML
    charset declaration; if absent, deterministic codec candidates are tried
    and only exact canonical quote bindings can produce accepted blocks.
    Title text is never used as model context.
    """
    by_route: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for document in documents:
        if document.get("side") != "rakuten":
            continue
        routes = {(ref.get("raw_file"), ref.get("sha256"))
                  for segment in document.get("description", {}).get("segments", [])
                  for ref in segment.get("source_refs", [])
                  if ref.get("locator") == "whole_document_visible_text"}
        for route in routes:
            if all(isinstance(part, str) for part in route):
                by_route[route].append(document)
    blocks, exclusions = [], []
    for (raw_file, expected), route_docs in sorted(by_route.items()):
        source_file = normalized_source_path(source_root, raw_file)
        raw = source_file.read_bytes()
        if sha_bytes(raw) != expected:
            raise ValueError("whole-document source hash mismatch")
        declared = set()
        for document in route_docs:
            for ref in document.get("title", {}).get("source_refs", []):
                locator = ref.get("locator")
                if (ref.get("raw_file") == raw_file and ref.get("sha256") == expected
                        and isinstance(locator, dict) and isinstance(locator.get("encoding"), str)):
                    declared.add(locator["encoding"])
        if len(declared) > 1:
            raise ValueError("conflicting frozen title encoding metadata for one capture")
        declared_encoding = next(iter(declared), None)
        preferred = declared_encoding or _html_encoding(raw)
        candidates = [preferred] if preferred else []
        candidates.extend(codec for codec in ("utf-8", "cp932", "euc_jp", "shift_jis")
                          if codec not in candidates)
        best = None
        for encoding in candidates:
            codec = encoding if declared_encoding else codecs.lookup(encoding).name
            copies = []
            for document in route_docs:
                item = dict(document)
                title = dict(document.get("title", {}))
                refs = list(title.get("source_refs", []))
                has_exact_encoding = any(ref.get("raw_file") == raw_file
                                         and ref.get("sha256") == expected
                                         and isinstance(ref.get("locator"), dict)
                                         and codecs.lookup(ref["locator"].get("encoding", "utf-8")).name
                                         == codecs.lookup(codec).name
                                         for ref in refs)
                if not has_exact_encoding:
                    refs.append({"raw_file": raw_file, "sha256": expected,
                                 "locator": {"kind": "html_text", "encoding": codec,
                                             "path": "html/head/title"}})
                title["source_refs"] = refs
                item["title"] = title
                copies.append(item)
            try:
                candidate_blocks, candidate_exclusions, _ = WHOLE_DOC.build_rakuten_blocks(
                    store_root, copies)
            except (UnicodeDecodeError, LookupError, ValueError):
                continue
            rank = len(candidate_blocks)
            if best is None or rank > best[0]:
                best = (rank, candidate_blocks, candidate_exclusions, codec)
            if preferred and codec == codecs.lookup(preferred).name:
                break
        if best is None:
            exclusions.append({"source_route": {"raw_file": raw_file, "sha256": expected},
                               "exclusion_reason": "whole_document_source_decode_or_extraction_failed"})
            continue
        _, route_blocks, route_exclusions, chosen_encoding = best
        for block in route_blocks:
            block["source_encoding_basis"] = (
                "frozen_title_locator" if declared_encoding else
                ("raw_capture_charset_or_bom" if preferred else "exact_canonical_quote_binding"))
            block["source_encoding"] = chosen_encoding
        blocks.extend(route_blocks)
        exclusions.extend(route_exclusions)
    return blocks, exclusions


def create_raw_store(output: Path, source_root: Path, completion_root: Path | None,
                     documents: list[dict[str, Any]]) -> tuple[Path, list[dict[str, Any]]]:
    """Create a scoped registry and internal symlinks for exact referenced files."""
    store_root = output / "raw-store"
    raw_dir = store_root / "raw"
    raw_dir.mkdir(parents=True)
    refs: dict[str, str] = {}
    completion_manifest = None
    if completion_root is not None:
        completion_manifest = read_json(completion_root / "resolution-manifest.json")
    completion_files = {row["raw_reference_path"].replace("\\", "/"): row
                        for row in (completion_manifest or {}).get("files", [])}
    for document in documents:
        for segment in document.get("description", {}).get("segments", []):
            for ref in segment.get("source_refs", []):
                raw_file, expected = ref.get("raw_file"), ref.get("sha256")
                if not isinstance(raw_file, str) or not isinstance(expected, str):
                    continue
                old = refs.setdefault(raw_file, expected)
                if old != expected:
                    raise ValueError("one raw path has conflicting frozen hashes")
    registry = []
    for raw_file, expected in sorted(refs.items()):
        origin = "normalized_full_test_source_root"
        original = normalized_source_path(source_root, raw_file)
        if not original.is_file() and raw_file.replace("\\", "/") in completion_files:
            entry = completion_files[raw_file.replace("\\", "/")]
            original = Path(completion_root) / entry["copied_path"]
            origin = "exact_path_sha_verified_completion_overlay"
        if not original.is_file():
            raise FileNotFoundError("referenced capture missing from normalized source root")
        data = original.read_bytes()
        if sha_bytes(data) != expected:
            raise ValueError("referenced capture hash differs from frozen document source_ref")
        if completion_manifest is not None and origin == "exact_path_sha_verified_completion_overlay":
            entry = completion_files[raw_file.replace("\\", "/")]
            if len(data) != entry["bytes"] or expected != entry["sha256"]:
                raise ValueError("completion overlay differs from its exact source resolution pin")
        bundle_name = expected + original.suffix
        link = raw_dir / bundle_name
        link.symlink_to(original.resolve())
        registry.append({"raw_file": raw_file, "bundle_file": f"raw/{bundle_name}",
                         "bytes": len(data), "sha256": expected,
                         "source_origin": origin})
    write_rows(store_root / "raw-source-registry.jsonl", registry)
    return store_root, registry


def verify_block(block: dict[str, Any], document: dict[str, Any], store: RAW.RawStore) -> list[dict[str, Any]]:
    if block.get("target_document_id") != document.get("document_id"):
        raise ValueError("block target document mismatch")
    if block.get("target_document_sha256") != document.get("document_sha256"):
        raise ValueError("block target document hash mismatch")
    description = document.get("description", {}).get("text", "")
    refs = block.get("quote_refs")
    if not isinstance(refs, list) or not refs:
        raise ValueError("source block has no canonical description quote refs")
    for ref in refs:
        start, end, quote = ref.get("start"), ref.get("end"), ref.get("quote")
        if (ref.get("document_id") != document["document_id"]
                or ref.get("document_sha256") != document["document_sha256"]
                or ref.get("field") != "description"
                or type(start) is not int or type(end) is not int
                or description[start:end] != quote):
            raise ValueError("canonical description quote is not an exact literal span")
    receipts = []
    for path, span in raw_ref_values(block):
        if not RAW.verify_span(store, span):
            raise ValueError("source block raw span failed verification")
        receipts.append({"block_id": block["block_id"], "target_document_id": document["document_id"],
                         "span_path": path, "raw_file": span["raw_file"],
                         "sha256": span["sha256"], "locator": span["locator"],
                         "start": span["start"], "end": span["end"],
                         "quote": span["quote"], "verified": True})
    if not receipts:
        raise ValueError("source block has no verifiable raw span")
    return receipts


def cache_key(request: dict[str, Any]) -> dict[str, Any]:
    axes = request.get("target_selected_axes")
    if axes is None:
        axes = []
    if not isinstance(axes, list):
        raise ValueError("target selected axes must preserve the exact ordered list")
    return {"direction": request["direction"], "axis_name": request["axis_name"],
            "option_values": request["option_values"],
            "target_document_sha256": request["target_document_sha256"],
            "target_selected_axes": axes}


def _unit_from_group(group_id: str, members: list[dict[str, Any]]) -> dict[str, Any]:
    request = members[0]
    axes = request.get("target_selected_axes") or []
    axis_text = request.get("target_selected_axes_text")
    if axes:
        if not isinstance(axis_text, str) or not axis_text:
            raise ValueError("paired context is missing exact target_selected_axes_text")
        source_request = dict(request)
        source_request["request_id"] = group_id
        # The frozen builder checks that source_request has a valid option, but
        # it neither copies nor uses this field. Materialized test requests
        # deliberately omit binding-only selected_value, so provide a stable
        # schema sentinel from the raw option set solely for that invariant.
        source_request["selected_value"] = request["option_values"][0]
        return {"group_id": group_id, "kind": "paired", "axis_name": request["axis_name"],
                "option_values": request["option_values"],
                "target_document_id": request["target_document_id"],
                "target_selected_axes_text": axis_text, "source_request": source_request}
    choices = [{"label": value} for value in request["option_values"]]
    choices.append({"label": CONTEXT.UNKNOWN_LABEL})
    return {"group_id": group_id, "kind": "shared", "axis_name": request["axis_name"],
            "option_values": request["option_values"],
            "target_document_id": request["target_document_id"],
            "target_selected_axes_text": None, "source_request": None,
            "model_request": {"id": group_id, "choices": choices, "selected_value": None}}


def build_artifact(base: Path = DEFAULT_BASE, inputs: Path = DEFAULT_INPUTS,
                   source_root: Path = DEFAULT_SOURCE, output: Path = DEFAULT_OUT,
                   membership_path: Path | None = None,
                   plan_path: Path | None = None,
                   completion_root: Path | None = DEFAULT_COMPLETION) -> dict[str, Any]:
    base, inputs, source_root, output = map(Path, (base, inputs, source_root, output))
    completion_root = Path(completion_root) if completion_root else None
    if output.exists():
        raise FileExistsError(output)
    plan_path = Path(plan_path) if plan_path else base / "plan/subset-plan.json"
    membership_path = Path(membership_path) if membership_path else base / "plan/subset-membership.jsonl"
    plan = read_json(plan_path)
    membership_rows = rows(membership_path)
    expected_case_count = plan.get("selected_case_count")
    if (not isinstance(expected_case_count, int) or len(membership_rows) != expected_case_count
            or sha_file(membership_path) != plan.get("membership_sha256")):
        raise ValueError("predeclared membership does not match its subset freeze")
    selected_case_ids = {row["case_id"] for row in membership_rows}
    if len(selected_case_ids) != expected_case_count:
        raise ValueError("subset membership has duplicate case IDs")

    manifest_path = inputs / "manifest.json"
    input_manifest = read_json(manifest_path)
    if input_manifest.get("membership_sha256") != plan.get("membership_sha256"):
        raise ValueError("materialized input membership differs from the frozen subset plan")
    documents = rows(inputs / "documents.jsonl")
    document_by_id = {row["document_id"]: row for row in documents}
    requests_by_id = {row["request_id"]: row for row in rows(inputs / "requests.jsonl")}
    selected_tasks = rows(inputs / "tasks.jsonl")
    if {row.get("case_id") for row in selected_tasks} != selected_case_ids:
        raise ValueError("materialized task cases differ from the frozen 200-case subset")
    if len(selected_tasks) != plan.get("selected_task_binding_count"):
        raise ValueError("selected task denominator differs from predeclared inventory")
    request_ids = {row["request_id"] for row in selected_tasks}
    if not request_ids <= requests_by_id.keys():
        raise ValueError("selected task has a missing frozen request")
    selected_requests = [requests_by_id[rid] for rid in sorted(request_ids)]
    for request in selected_requests:
        if "selected_value" in request:
            raise ValueError("materialized source-only requests must omit binding-only selected_value")
        if request.get("model_input_id") is None:
            raise ValueError("selected request has no model_input_id")
        document = document_by_id.get(request.get("target_document_id"))
        if document is None or document.get("document_sha256") != request.get("target_document_sha256"):
            raise ValueError("selected request target document SHA does not match frozen document")

    grouped_requests: dict[str, list[dict[str, Any]]] = defaultdict(list)
    group_keys: dict[str, dict[str, Any]] = {}
    for request in selected_requests:
        key = cache_key(request)
        key_sha = digest(key)
        group_id = "final-exact-context:" + key_sha
        if group_id in group_keys and group_keys[group_id] != key:
            raise ValueError("context digest collision")
        group_keys[group_id] = key
        grouped_requests[group_id].append(request)

    target_document_ids = {r["target_document_id"] for r in selected_requests}
    selected_documents = [document_by_id[did] for did in sorted(target_document_ids)]
    output.mkdir(parents=True)
    store_root, registry = create_raw_store(output, source_root, completion_root, selected_documents)
    raw_store = TYPED.BundleStore(store_root)

    # v2 supplies the complete-document route; v3 supplies typed item_desc and
    # API HTML routes plus its exact-span, anchor/table-safe restorations.
    whole_blocks, whole_exclusions = build_whole_document_blocks(store_root, selected_documents, source_root)
    typed_blocks, typed_exclusions, typed_receipts, _, typed_coverage = TYPED.restore(
        store_root, selected_documents, target_document_ids, set(), {})
    all_blocks = whole_blocks + typed_blocks
    by_document: dict[str, list[dict[str, Any]]] = defaultdict(list)
    verified_receipts = []
    context_order_offsets = []
    verified_ids = set()
    for block in all_blocks:
        document = document_by_id[block["target_document_id"]]
        receipts = verify_block(block, document, raw_store)
        if block["block_id"] in verified_ids:
            raise ValueError("duplicate final source block identity")
        verified_ids.add(block["block_id"])
        packet_block = dict(block)
        start, end = packet_block.get("html_start"), packet_block.get("html_end")
        basis = "extractor_html_offsets"
        if type(start) is not int or type(end) is not int:
            span = block.get("source_ref")
            if not (isinstance(span, dict) and type(span.get("start")) is int
                    and type(span.get("end")) is int):
                span = next((item for _, item in raw_ref_values(block)), None)
            if not (isinstance(span, dict) and type(span.get("start")) is int
                    and type(span.get("end")) is int):
                raise ValueError("source block lacks exact offsets needed for stable context ordering")
            start, end = span["start"], span["end"]
            basis = "verified_exact_raw_span_offsets"
        packet_block["html_start"], packet_block["html_end"] = start, end
        by_document[block["target_document_id"]].append(packet_block)
        context_order_offsets.append({"block_id": block["block_id"],
                                      "target_document_id": block["target_document_id"],
                                      "order_start": start, "order_end": end,
                                      "offset_basis": basis})
        verified_receipts.extend(receipts)

    packets, empty, fanout = [], [], []
    cache_kind_counts = defaultdict(int)
    for group_id in sorted(grouped_requests):
        members = grouped_requests[group_id]
        representative = members[0]
        if any(cache_key(request) != group_keys[group_id] for request in members):
            raise ValueError("paired exact-context cache group has a nonidentical key")
        unit = _unit_from_group(group_id, members)
        context_blocks = by_document.get(unit["target_document_id"], [])
        packet = CONTEXT.build_packet(unit, context_blocks)
        kind = "global_singleton_unselected" if unit["kind"] == "shared" else "paired_exact_context_cache"
        cache_kind_counts[kind] += 1
        if packet is None:
            empty.append({"unit_id": group_id, "kind": kind, "winning_value": None,
                          "evidence_block_ids": [],
                          "reason": "検証可能な出典本文がないため不明。"})
        else:
            payload = CONTEXT.model_payload(packet)
            packets.append(packet)
        fanout.append({"context_unit_id": group_id, "context_cache_kind": kind,
                       "request_ids": sorted(r["request_id"] for r in members),
                       "model_input_ids": sorted({r["model_input_id"] for r in members}),
                       "selected_task_ids": sorted(t["task_id"] for t in selected_tasks
                                                    if t["request_id"] in {r["request_id"] for r in members}),
                       "selected_case_ids": sorted({t["case_id"] for t in selected_tasks
                                                     if t["request_id"] in {r["request_id"] for r in members}}),
                       "target_document_id": representative["target_document_id"],
                       "cache_key_sha256": digest(group_keys[group_id])})

    payloads = [CONTEXT.model_payload(packet) for packet in packets]
    write_rows(output / "source-blocks.jsonl", all_blocks)
    write_rows(output / "context-order-offsets.jsonl", context_order_offsets)
    write_rows(output / "source-exclusions.jsonl", whole_exclusions + typed_exclusions)
    write_rows(output / "span-receipts.jsonl", verified_receipts)
    write_rows(output / "packets.jsonl", packets)
    write_rows(output / "model-payloads.jsonl", payloads)
    write_rows(output / "unknown-unit-responses.jsonl", empty)
    write_rows(output / "request-fanout.jsonl", fanout)
    write_rows(output / "raw-pins.jsonl", registry)
    write_rows(output / "composition-exclusions.jsonl", [
        {"target_document_id": block.get("target_document_id"), "block_id": block.get("block_id"),
         "exclusion_reason": reason}
        for block in all_blocks
        if (reason := EVIDENCE.exclusion_reason({**block, "text": block.get("support_text")})) is not None
    ])
    protocol_path = output / "protocol.json"
    protocol_text = FINAL_PROTOCOL
    write_json(protocol_path, {"schema_version": FINAL_PROTOCOL_VERSION,
                               "protocol": protocol_text,
                               "protocol_sha256": sha_bytes(protocol_text.encode("utf-8")),
                               "packet_schema": "composition-full-context-packet-v1",
                               "context_cache_key_fields": ["direction", "axis_name", "ordered_option_values",
                                                             "target_document_sha256", "ordered_target_selected_axes"],
                               "axes_text_checked_byte_identical_within_group": True,
                               "source_policy": "all eligible source blocks; original block order; no truncation or ranking",
                               "title_field_in_payload": False, "selected_value_in_packet_or_payload": False,
                               "labels_or_predictions_read": False, "model_inference_run": False,
                               "empty_context_policy": "explicit unknown; retained in unit, request, task, and case denominators",
                               "context_cache_policy": "paired exact-context cache keyed by digest of direction, exact axis, ordered raw options, target document SHA, and exact ordered target axes; selected desired value excluded; no axes is a global singleton unselected"})

    block_doc_counts = {did: sum(1 for b in all_blocks if b["target_document_id"] == did)
                        for did in sorted(target_document_ids)}
    eligible_by_doc = {did: sum(1 for b in by_document.get(did, [])
                                if EVIDENCE.exclusion_reason({**b, "text": b.get("support_text")}) is None)
                       for did in sorted(target_document_ids)}
    source_review = {
        "schema_version": "sku-final-composition-source-only-review-v1",
        "status": "prepared_blind_source_only_no_inference",
        "subset_case_denominator": len(selected_case_ids),
        "subset_task_binding_denominator": len(selected_tasks),
        "selected_request_binding_denominator": len(selected_requests),
        "selected_raw_capture_manifest_missing_count": input_manifest.get("selected_raw_capture_missing_count", 0),
        "selected_raw_capture_missing_refs_required_by_composition_documents": 0,
        "exact_context_unit_denominator": len(grouped_requests),
        "dispatched_packet_count": len(packets),
        "explicit_unknown_unit_count": len(empty),
        "source_target_document_count": len(target_document_ids),
        "source_block_count": len(all_blocks),
        "eligible_source_block_count": sum(eligible_by_doc.values()),
        "excluded_by_composition_policy_count": len(rows(output / "composition-exclusions.jsonl")),
        "raw_capture_pin_count": len(registry),
        "verified_raw_span_receipt_count": len(verified_receipts),
        "whole_document_source_block_count": len(whole_blocks),
        "typed_html_api_source_block_count": len(typed_blocks),
        "typed_route_exclusion_count": len(typed_exclusions),
        "passage_character_count": sum(len(block.get("model_passage", "")) for p in packets for block in p["blocks"]),
        "compact_model_payload_bytes": (output / "model-payloads.jsonl").stat().st_size,
        "full_packet_bytes": (output / "packets.jsonl").stat().st_size,
        "context_cache_kind_counts": dict(cache_kind_counts),
        "document_block_counts": block_doc_counts,
        "eligible_block_counts_by_document": eligible_by_doc,
        "labels_read": False,
        "prior_predictions_read": False,
        "test_product_texts_printed": False,
        "model_inference_run": False,
        "scope_status": "source-bound applicability remains unproved",
        "source_policy": {"raw_capture_hash_and_bytes_checked": True,
                           "canonical_description_quotes_verified_literal": True,
                           "raw_spans_verified": True,
                           "exclusion_reason": "unchanged composition_evidence_v1.exclusion_reason",
                           "link_table_keyword_exclusion": "unchanged",
                           "safe_plaintext_fallback": "only exact v3 restoration routes that pass original anchor/table guards",
                           "titles_in_model_payload": False,
                           "block_selection": "every eligible body block, no ranking, no truncation"}}
    write_json(output / "source-review.json", source_review)

    input_paths = {
        "subset_plan": base / "plan/subset-plan.json",
        "subset_membership": membership_path,
        "materialized_inputs_manifest": manifest_path,
        "materialized_documents": inputs / "documents.jsonl",
        "materialized_requests": inputs / "requests.jsonl",
        "materialized_tasks": inputs / "tasks.jsonl",
        "selected_source_manifest": inputs / "selected-source-manifest.jsonl",
        **({"source_capture_completion_manifest": completion_root / "resolution-manifest.json"}
           if completion_root is not None else {}),
        "source_script": Path(__file__),
        "source_script_tests": HERE / "test_prepare_final_composition_packets_v1.py",
        "composition_evidence": Path(EVIDENCE.__file__),
        "context_packet_builder": Path(CONTEXT.__file__),
        "composition_context_policy": HERE / "composition_context_policy_v1.py",
        "whole_document_builder_v1": HERE / "composition_source_blocks_v1.py",
        "whole_document_builder": Path(WHOLE_DOC.__file__),
        "typed_source_builder": Path(TYPED.__file__),
        "scoped_html_extractor": HERE / "build_scoped_html_blocks_v1.py",
        "scoped_html_passage_renderer": HERE / "prepare_scoped_html_trial_v1.py",
        "raw_source_verifier": HERE / "claude_v9_gate/sku_gate_sources.py",
    }
    code_dir = output / "code"
    code_dir.mkdir()
    code_snapshot = {}
    code_names = {"source_script", "source_script_tests", "composition_evidence",
                  "context_packet_builder", "composition_context_policy",
                  "whole_document_builder_v1", "whole_document_builder",
                  "typed_source_builder", "scoped_html_extractor",
                  "scoped_html_passage_renderer", "raw_source_verifier"}
    for name, path in input_paths.items():
        if name in code_names:
            target = code_dir / path.name
            shutil.copyfile(path, target)
            code_snapshot[name] = {"path": str(target.relative_to(output)),
                                   "sha256": sha_file(target)}
    write_json(output / "manifest.json", {
        "schema_version": "sku-final-composition-source-only-v1",
        "output_status": "source_packets_frozen_no_inference",
        "source_review_sha256": sha_file(output / "source-review.json"),
        "protocol_sha256": sha_file(protocol_path),
        "output_sha256": {name: sha_file(output / name) for name in (
            "source-blocks.jsonl", "source-exclusions.jsonl", "span-receipts.jsonl",
            "context-order-offsets.jsonl",
            "packets.jsonl", "model-payloads.jsonl", "unknown-unit-responses.jsonl",
            "request-fanout.jsonl", "raw-pins.jsonl", "composition-exclusions.jsonl")},
        "input_sha256": {name: sha_file(path) for name, path in input_paths.items()},
        "frozen_helper_sha256": {name: sha_file(path) for name, path in input_paths.items()
                                  if name in {"composition_evidence", "context_packet_builder",
                                              "composition_context_policy", "whole_document_builder_v1",
                                              "whole_document_builder", "typed_source_builder",
                                              "scoped_html_extractor", "scoped_html_passage_renderer",
                                              "raw_source_verifier"}},
        "code_snapshot": code_snapshot,
        "completion_overlay_used_for_composition_refs": any(
            row.get("source_origin") == "exact_path_sha_verified_completion_overlay" for row in registry),
        "source_review": source_review,
        "materialized_input_source_manifests": input_manifest.get("input_manifest_sha256", input_manifest),
        "independent_test_labels_accessed": False,
        "full_test_prior_predictions_accessed": False,
        "inference_started": False,
    })
    return source_review


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--membership", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--completion-root", type=Path, default=DEFAULT_COMPLETION)
    parser.add_argument("--finalize-existing", action="store_true",
                        help="freeze an already-built output directory without rerunning source extraction")
    args = parser.parse_args()
    if args.finalize_existing:
        membership = args.membership or args.base / "plan/subset-membership.jsonl"
        plan = args.plan or args.base / "plan/subset-plan.json"
        result = finalize_existing_artifact(args.base, args.inputs, args.output,
                                            membership, plan, args.completion_root)
    else:
        result = build_artifact(args.base, args.inputs, args.source_root, args.output,
                                args.membership, args.plan, args.completion_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
