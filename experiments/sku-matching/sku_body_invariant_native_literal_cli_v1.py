#!/usr/bin/env python3
"""Prepare R9 native-source packets and persist literal model responses.

This utility performs source/hash/schema transport checks only. It does not run
inference or decide semantic truth, winners, labels, or grades.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".lab-output/sku-next-body-invariant-native-execution-20261013-v1"
SOURCE = ROOT / ".lab-output/sku-next-body-invariant-v3-source-only-20261013-r9-v3"
PARTITION = ROOT / ".lab-output/sku-next-body-invariant-dev10-pair-partition-20261013-v5"
MODULE = ROOT / "experiments/sku-matching/sku_body_invariant_choice_v3.py"
SCHEMA = MODULE.with_suffix(".schema.json")
NATIVE_WRAPPER = ROOT / "experiments/sku-matching/sku_body_invariant_native_rakuten_source_v1.py"
MAX_PACKET_BYTES = 90_000
MAX_PAYLOADS = 2
EXPECTED = {
    "module_sha256": "fee3fd15531560eb66bd301e15434669f347658f1ef7e775cc346b9ea2ff1923",
    "schema_sha256": "26ee56933d7d6867e674007088216dd84aba90dc3a81f61f56c348fd22cf1ea0",
    "native_wrapper_sha256": "5870c8d1fd3ed870dada3715e3e3c831d4b2710d23504d3372606544e6b46a09",
    "native_freeze_sha256": "bfc270ead9dbf248b257589403568f683a6e3129f9c52ebb7c8414db546c9d4d",
    "r9_manifest_sha256": "315732224e81f537c8983ef127470ec57ebdff0db828f5460abc6b38c562c3ce",
    "native_field_audit_sha256": "ee98de160fcc9bd9c4bf01468e33af157a22574505524e5e92e72f330954fc15",
    "r9_pairs_sha256": "10bf09da391446983cc075e0fa7336be69594c698fac1ac479a0d64a9cb2462a",
    "r9_payloads_sha256": "0f9bac10c0c0f53188bb7a174d5267a04a963476c1a5b1ccc6e05268e2eb1262",
    "partition_receipt_sha256": "6a27f280abaab34cdaa4634565be2905d398449a902d9bd99552ca676349dfa1",
    "dispatch_ids_sha256": "e23695520f3a226b31d7e7df1afb3658e25857348b4030edf7b93a43fca91e7c",
    "packet_index_sha256": "e8449d9f48fd9c7ecd875ef162f096a40235c26ec6097cd8bea318fcc99e87d1",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def verify_live_sources() -> None:
    """Reject stale live validator/schema/native wrapper before transport work."""
    paths = (("module_sha256", MODULE), ("schema_sha256", SCHEMA),
             ("native_wrapper_sha256", NATIVE_WRAPPER),
             ("native_freeze_sha256", SOURCE / "freeze.json"),
             ("r9_manifest_sha256", SOURCE / "manifest.json"),
             ("native_field_audit_sha256", SOURCE / "native-rakuten-field-binding-audit.jsonl"),
             ("r9_pairs_sha256", SOURCE / "model-pairs-v3.jsonl"),
             ("r9_payloads_sha256", SOURCE / "model-payloads-v3.jsonl"))
    for key, path in paths:
        if not path.is_file() or not EXPECTED[key] or sha(path.read_bytes()) != EXPECTED[key]:
            raise ValueError(f"live frozen source/hash guard failed: {key}")


def prepare_packets() -> dict[str, Any]:
    verify_live_sources()
    for label, path in (("partition_receipt_sha256", PARTITION / "pair-partition-receipt.json"),
                        ("dispatch_ids_sha256", PARTITION / "dispatch-pair-ids.jsonl")):
        if sha(path.read_bytes()) != EXPECTED[label]:
            raise ValueError(f"frozen {label} mismatch: {path}")
    receipt = json.loads((PARTITION / "pair-partition-receipt.json").read_text(encoding="utf-8"))
    source_inputs = receipt.get("source_inputs", {})
    if (source_inputs.get("native_rakuten_source_adapter_sha256") != EXPECTED["native_wrapper_sha256"]
            or source_inputs.get("prepared_freeze_sha256") != EXPECTED["native_freeze_sha256"]
            or source_inputs.get("prepared_manifest_sha256") != EXPECTED["r9_manifest_sha256"]):
        raise ValueError("partition receipt is not bound to the frozen R9-v3 native source bundle")
    pairs_path, payloads_path = SOURCE / "model-pairs-v3.jsonl", SOURCE / "model-payloads-v3.jsonl"
    pair_rows, payload_rows = read_jsonl(pairs_path), read_jsonl(payloads_path)
    pair_by_id = {row["pair_id"]: row for row in pair_rows}
    payload_by_id = {row["pair_id"]: row for row in payload_rows}
    if len(pair_by_id) != len(pair_rows) or len(payload_by_id) != len(payload_rows):
        raise ValueError("duplicate source pair identity")
    dispatch_rows = read_jsonl(PARTITION / "dispatch-pair-ids.jsonl")
    dispatch_ids = [row.get("pair_id") for row in dispatch_rows]
    if len(dispatch_ids) != 93 or len(set(dispatch_ids)) != 93:
        raise ValueError("frozen dispatch must contain 93 unique pair IDs")
    if any(pid not in pair_by_id or pid not in payload_by_id for pid in dispatch_ids):
        raise ValueError("dispatch ID does not map to both frozen pair and payload")
    if set(dispatch_ids) & {r.get("pair_id") for r in read_jsonl(PARTITION / "literal-protected-pair-ids.jsonl")}:
        raise ValueError("literal-protected pair leaked into dispatch")

    # A worker receives only the pair protocol record and complete public payload.
    # The private validation-bindings file is never opened here.
    packet_dir = OUT / "packets"
    packet_dir.mkdir(parents=True, exist_ok=True)
    groups: list[list[str]] = []
    current: list[str] = []
    for pid in dispatch_ids:
        tentative = current + [pid]
        blob = canon({"packet_schema": "sku-body-invariant-public-packet-v1",
                      "pair_records": [pair_by_id[x] for x in tentative],
                      "model_payloads": [payload_by_id[x] for x in tentative]}) + b"\n"
        if current and (len(tentative) > MAX_PAYLOADS or len(blob) > MAX_PACKET_BYTES):
            groups.append(current)
            current = [pid]
        else:
            current = tentative
    if current:
        groups.append(current)

    packet_manifest = {
        "schema_version": "sku-body-invariant-public-packet-index-v1",
        "source_status": "source_only_model_semantics_unverified",
        "inference_run": False, "labels_read": False, "predictions_read": False,
        "scores_created": False, "grades_created": False,
        "dispatch_pair_count": len(dispatch_ids), "literal_protected_pair_count": 48,
        "dispatch_ids_sha256": sha((PARTITION / "dispatch-pair-ids.jsonl").read_bytes()),
        "partition_receipt_sha256": sha((PARTITION / "pair-partition-receipt.json").read_bytes()),
        "source_pairs_sha256": sha(pairs_path.read_bytes()),
        "source_payloads_sha256": sha(payloads_path.read_bytes()),
        "r9_manifest_sha256": sha((SOURCE / "manifest.json").read_bytes()),
        "native_wrapper_sha256": sha(NATIVE_WRAPPER.read_bytes()),
        "native_field_binding_audit_sha256": sha((SOURCE / "native-rakuten-field-binding-audit.jsonl").read_bytes()),
        "native_freeze_sha256": sha((SOURCE / "freeze.json").read_bytes()),
        "module_sha256": sha(MODULE.read_bytes()), "schema_sha256": sha(SCHEMA.read_bytes()),
        "packet_count": len(groups), "packets": [],
    }
    for n, group in enumerate(groups, 1):
        name = f"packet-{n:03}.json"
        packet = {"packet_schema": "sku-body-invariant-public-packet-v1",
                  "packet_number": n, "pair_records": [pair_by_id[x] for x in group],
                  "model_payloads": [payload_by_id[x] for x in group]}
        raw = canon(packet) + b"\n"
        too_large = len(raw) > MAX_PACKET_BYTES
        if too_large and len(group) != 1:
            raise ValueError("packet size limit exceeded with more than one payload")
        path = packet_dir / name
        if path.exists():
            raise FileExistsError(path)
        path.write_bytes(raw)
        packet_manifest["packets"].append({"file": f"packets/{name}", "sha256": sha(raw),
            "bytes": len(raw), "pair_ids": group, "payload_count": len(group),
            "oversized_single_payload": too_large})
    index_path = OUT / "packet-index.json"
    if index_path.exists():
        raise FileExistsError(index_path)
    index_path.write_bytes(canon(packet_manifest) + b"\n")
    return packet_manifest


def _literal_json_items(raw: str) -> list[str]:
    """Extract original object slices from either a JSON array or JSONL."""
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"duplicate JSON object key: {key}")
            obj[key] = value
        return obj

    decoder = json.JSONDecoder(object_pairs_hook=unique_object)
    text = raw.lstrip()
    offset = len(raw) - len(text)
    if text.startswith("["):
        # Validate the complete document first. This rejects trailing commas,
        # malformed separators, and duplicate keys at every nesting level.
        parsed = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(parsed, list) or any(not isinstance(row, dict) for row in parsed):
            raise ValueError("literal response array entries must be objects")
        offset += 1
        items: list[str] = []
        while True:
            while offset < len(raw) and raw[offset].isspace():
                offset += 1
            if offset < len(raw) and raw[offset] == "]":
                offset += 1
                break
            start = offset
            obj, end = decoder.raw_decode(raw, offset)
            if not isinstance(obj, dict):
                raise ValueError("literal response array entries must be objects")
            items.append(raw[start:end])
            offset = end
            while offset < len(raw) and raw[offset].isspace():
                offset += 1
            if offset < len(raw) and raw[offset] == ",":
                offset += 1
            elif offset < len(raw) and raw[offset] == "]":
                offset += 1
                break
            else:
                raise ValueError("malformed literal response array")
        if raw[offset:].strip():
            raise ValueError("trailing data after literal response array")
        return items
    items = []
    for line in raw.splitlines():
        if line.strip():
            obj = json.loads(line, object_pairs_hook=unique_object)
            if not isinstance(obj, dict):
                raise ValueError("literal JSONL entries must be objects")
            items.append(line.strip())
    return items


def load_packet(packet_path: Path, index_path: Path) -> dict[str, Any]:
    verify_live_sources()
    raw = packet_path.read_bytes()
    index_raw = index_path.read_bytes()
    if sha(index_raw) != EXPECTED["packet_index_sha256"]:
        raise ValueError("frozen packet index SHA mismatch")
    index = json.loads(index_raw)
    match = [item for item in index["packets"] if (index_path.parent / item["file"]).resolve() == packet_path.resolve()]
    if len(match) != 1 or sha(raw) != match[0]["sha256"]:
        raise ValueError("packet is absent from frozen index or packet SHA mismatch")
    packet = json.loads(raw)
    if packet.get("packet_schema") != "sku-body-invariant-public-packet-v1":
        raise ValueError("unsupported packet schema")
    ids = [r.get("pair_id") for r in packet.get("pair_records", [])]
    if ids != match[0]["pair_ids"] or ids != [p.get("pair_id") for p in packet.get("model_payloads", [])]:
        raise ValueError("packet pair/payload IDs or order mismatch")
    return packet


def persist(packet_path: Path, index_path: Path, literal_path: Path, output_path: Path) -> dict[str, Any]:
    # Keep the canonical frozen validator beside this CLI and import its exact implementation.
    for label, path in (("module_sha256", MODULE), ("schema_sha256", SCHEMA)):
        if sha(path.read_bytes()) != EXPECTED[label]:
            raise ValueError(f"frozen live validator {label} mismatch")
    sys.path.insert(0, str(MODULE.parent))
    import sku_body_invariant_choice_v3 as body  # type: ignore
    packet = load_packet(packet_path, index_path)
    raw_draft = literal_path.read_text(encoding="utf-8")
    literal_items = _literal_json_items(raw_draft)
    if any("\n" in item or "\r" in item for item in literal_items):
        raise ValueError("use literal JSONL: one complete response object per physical line to preserve authored bytes")
    pairs = packet["pair_records"]
    payloads = packet["model_payloads"]
    if len(literal_items) != len(pairs):
        raise ValueError("draft response count does not equal packet pair count")
    by_id = {p["pair_id"]: p for p in payloads}
    validators = []
    for pair in pairs:
        payload = by_id[pair["pair_id"]]
        validators.append(lambda response, pair=pair, payload=payload:
                          body.validate_literal_response(response, pair, payload))
    # Existing atomically-persist helper validates the whole set before creating
    # a destination and hard-links the complete literal bytes without overwrite.
    validator_by_id = {p["pair_id"]: validator for p, validator in zip(pairs, validators)}
    verify_live_sources()
    return body.persist_literal_responses_atomically(output_path, literal_items,
        [p["pair_id"] for p in pairs],
        lambda response: validator_by_id[response.get("pair_id")](response))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("packetize", help="build frozen public packets from the specified dispatch file")
    save = sub.add_parser("persist", help="schema-check and atomically save a worker-authored literal draft")
    save.add_argument("--packet", type=Path, required=True)
    save.add_argument("--index", type=Path, default=OUT / "packet-index.json")
    save.add_argument("--literal-json-file", type=Path, required=True)
    save.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare_packets() if args.command == "packetize" else persist(args.packet, args.index, args.literal_json_file, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
