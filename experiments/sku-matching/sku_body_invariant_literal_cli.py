#!/usr/bin/env python3
"""Prepare public whole-option packets and persist literal model responses.

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
OUT = ROOT / ".lab-output/sku-next-body-invariant-execution-20261013-v1"
SOURCE = ROOT / ".lab-output/sku-next-body-invariant-v3-source-only-20261013-r8"
PARTITION = ROOT / ".lab-output/sku-next-body-invariant-dev10-pair-partition-20261013-v3"
MODULE = ROOT / "experiments/sku-matching/sku_body_invariant_choice_v3.py"
SCHEMA = MODULE.with_suffix(".schema.json")
MAX_PACKET_BYTES = 90_000
MAX_PAYLOADS = 2
EXPECTED = {
    "module_sha256": "fee3fd15531560eb66bd301e15434669f347658f1ef7e775cc346b9ea2ff1923",
    "schema_sha256": "26ee56933d7d6867e674007088216dd84aba90dc3a81f61f56c348fd22cf1ea0",
    "partition_receipt_sha256": "80ed44521cc0d37fdb363647ffa95fc05b134666f8247e3f0fdc4824b6ea4da7",
    "dispatch_ids_sha256": "c858bd7b20f8bca6b39be74083085f834b6439201755fbaf37080334dd8ef3a7",
    "r8_pairs_sha256": "af8b3450ee5b63089a2cf64b16ed6343d418d11ddab59a6b70b991887c2222a6",
    "r8_payloads_sha256": "6dbded4f161a9508d1acf436c4ed1fc761c31fe1a4049ecc1e6c3eedba2b25ce",
    "packet_index_sha256": "d54c8a3b6acb55b72fde4d28a3bcc6809e61326041bc87453102da5adc2fa2ae",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def prepare_packets() -> dict[str, Any]:
    for label, path in (("module_sha256", MODULE), ("schema_sha256", SCHEMA),
                        ("partition_receipt_sha256", PARTITION / "pair-partition-receipt.json"),
                        ("dispatch_ids_sha256", PARTITION / "dispatch-pair-ids.jsonl"),
                        ("r8_pairs_sha256", SOURCE / "model-pairs-v3.jsonl"),
                        ("r8_payloads_sha256", SOURCE / "model-payloads-v3.jsonl")):
        if sha(path.read_bytes()) != EXPECTED[label]:
            raise ValueError(f"frozen {label} mismatch: {path}")
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
