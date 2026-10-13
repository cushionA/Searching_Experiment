#!/usr/bin/env python3
"""Freeze complete public packets and persist an immutable indexed first pass.

This utility does not run inference, inspect private evaluation bindings, grade,
or read labels. Raw authored JSONL and expanded v3 responses are separate files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sysroot = str(HERE)
import sys
if sysroot not in sys.path:
    sys.path.insert(0, sysroot)

import run_next_body_indexed_route_dev_v1 as RUNNER  # noqa: E402
import sku_body_invariant_choice_v3 as BODY  # noqa: E402
import sku_body_invariant_indexed_transport_v1 as INDEXED  # noqa: E402
import prepare_body_indexed_packets_v1 as BASE_PACKETS  # noqa: E402

DATA_ROOT = ROOT / ".lab-output/sku-next-body-indexed-route-dev-20261013-v1"
SCOPED = DATA_ROOT / "scoped-native-source-v2"
PREFLIGHT = DATA_ROOT / "pre-model-preflight-v2"
CONFIG = DATA_ROOT / "runner-config.json"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-next-body-indexed-route-execution-20261013-v1"
MAX_PACKET_BYTES = 90_000
MAX_PAIRS = 2
GUIDANCE_PATH = ROOT / "experiments/sku-matching/sku-body-indexed-grounding-guidance-v1.txt"
PINNED_CODE = {
    "experiments/sku-matching/sku_body_invariant_indexed_transport_v1.py": "d45cf1faa5108b778cb10fdd2014e01ca6771c86f47ab9b23957084b3666f4c5",
    "experiments/sku-matching/sku_body_invariant_scoped_rakuten_inventory_route_dev_v1.py": "8d301391a8418b8bad551d5180144dbd347a9b546f4ef076434735335ff31392",
    "experiments/sku-matching/run_next_body_indexed_route_dev_v1.py": "3c76504ce41cc29b9bb9c5c1f87b13eb889aea2e26ee7247e779e3a9244b1184",
    "experiments/sku-matching/sku_body_invariant_choice_v3.py": "fee3fd15531560eb66bd301e15434669f347658f1ef7e775cc346b9ea2ff1923",
    "experiments/sku-matching/sku_body_invariant_choice_v3.schema.json": "26ee56933d7d6867e674007088216dd84aba90dc3a81f61f56c348fd22cf1ea0",
    "experiments/sku-matching/prepare_body_indexed_packets_v1.py": "37bb6b0b30870d5ff861b981aa96162173de170012de6f277e9fb4869250abab",
}
BASE_WORKER_INSTRUCTIONS = BASE_PACKETS.WORKER_INSTRUCTIONS
GROUNDING_GUIDANCE = GUIDANCE_PATH.read_text(encoding="utf-8")
WORKER_INSTRUCTIONS = BASE_WORKER_INSTRUCTIONS + "\n\n" + GROUNDING_GUIDANCE
def root_instructions(output_name: str, pair_count: int, output_dir: Path) -> str:
    return f"""Indexed route DEV packet handoff\n\
Workers author only pair_refs assigned in a packet and return one JSONL row per assigned ref in packet order. Preserve complete first drafts; never repair, retry, fill missing rows, or copy between packets. Each assigned packet is validated independently with the packet's exact pair_ref sequence and full candidate vector.\n\
ROOT can run `python3 -B experiments/sku-matching/prepare_body_indexed_route_dev_packets_v1.py validate-packet --raw-jsonl PATH --index-dir .lab-output/{output_name} --packet-file packet-NN.json` to check exact assignment/order and full vectors. This read-only check marks structurally invalid decisions unknown and preserves their original bytes.\n\
After all {pair_count} dispatched first drafts are collected, ROOT can concatenate original JSONL lines byte-for-byte in global pair_ref order and run `persist --raw-jsonl PATH --index-dir .lab-output/{output_name} --output-dir NEW_DIRECTORY`. Bulk persist requires the exact complete dispatched ref set, creates separate raw and canonical artifacts, and does not repair invalid first drafts. Raw bytes are not claimed equal to rendered v3 bytes.\n\
If a ref is missing or a raw row cannot be parsed, do not fabricate a response. Preserve received bytes and use the route runner's missing-response/failure-ledger path. Do not inspect labels, predictions, private task/case/row/request bindings, prior model responses, or other packets. Do not grade.\n"""


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canon(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def strict_loads(data: str | bytes) -> Any:
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate JSON object key: " + key)
            out[key] = value
        return out
    return json.loads(data, object_pairs_hook=pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError("invalid JSON constant: " + x)))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    data = Path(path).read_bytes()
    rows = []
    for i, line in enumerate(data.splitlines(), 1):
        if not line.strip():
            raise ValueError(f"blank JSONL row {i}: {path}")
        row = strict_loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"JSONL row {i} is not an object: {path}")
        rows.append(row)
    return rows


def file_pins(paths: dict[str, Path]) -> dict[str, dict[str, Any]]:
    return {name: {"path": str(path.resolve().relative_to(ROOT)), "bytes": path.stat().st_size,
                   "sha256": sha_file(path)} for name, path in paths.items()}


def verify_code() -> dict[str, str]:
    got = {}
    for rel, expected in PINNED_CODE.items():
        path = ROOT / rel
        digest = sha_file(path)
        if digest != expected:
            raise ValueError("live code hash differs from approved packet protocol: " + rel)
        got[rel] = digest
    return got


def _public_payload(pair: dict[str, Any], payload: dict[str, Any], pair_ref: int) -> dict[str, Any]:
    if payload.get("pair_id") != pair.get("pair_id") or payload.get("cache_key") != pair.get("cache_key"):
        raise ValueError("scoped pair and payload identity mismatch")
    if payload.get("candidate_vector") != pair.get("candidate_vector"):
        raise ValueError("scoped pair and payload candidate vector mismatch")
    indexed = INDEXED.build_indexed_payload(payload, pair_ref)
    base = dict(indexed)
    base.pop("indexed_transport", None)
    if base != payload:
        raise ValueError("indexed payload changed non-additive top-level public fields")
    if indexed["indexed_transport"].get("protocol") != INDEXED.PROTOCOL:
        raise ValueError("indexed payload protocol mismatch")
    return indexed


def validate_packet_draft(raw_jsonl: Path, index_dir: Path,
                          packet_file: str) -> dict[str, Any]:
    """Check one worker draft against only its assigned packet; never rewrite it."""
    index, source = _verify_packet_index(Path(index_dir))
    packet_record = next((x for x in index["packets"] if x["file"] == packet_file), None)
    if packet_record is None:
        raise ValueError("packet file is not assigned by this execution index")
    packet = strict_loads((Path(index_dir) / packet_file).read_bytes())
    assigned = [x["pair_ref"] for x in packet["pairs"]]
    data = Path(raw_jsonl).read_bytes()
    lines = data.splitlines()
    if not lines or any(not line.strip() for line in lines):
        raise ValueError("packet draft must be nonempty JSONL without blank lines")
    drafts = [strict_loads(line) for line in lines]
    if any(not isinstance(x, dict) for x in drafts):
        raise ValueError("packet draft row must be a JSON object")
    refs = [x.get("pair_ref") for x in drafts]
    if any(type(ref) is not int for ref in refs) or refs != assigned:
        raise ValueError("packet draft must preserve its exact assigned pair_ref list and order")
    report = []
    for draft, ref in zip(drafts, refs):
        pair_id = source["by_number"][ref]["pair_id"]
        try:
            INDEXED.render_indexed_response(draft, source["pairs"][pair_id], source["payloads"][pair_id], ref)
            report.append({"pair_ref": ref, "status": "structurally_valid_firstpass",
                           "semantic_retry_performed": False})
        except (KeyError, TypeError, ValueError) as exc:
            report.append({"pair_ref": ref, "status": "invalid_unknown_firstpass_preserved",
                           "error_type": type(exc).__name__, "error": str(exc),
                           "semantic_retry_performed": False})
    return {"packet_file": packet_file, "assigned_pair_refs": assigned,
            "response_pair_refs": refs, "raw_bytes": len(data), "raw_sha256": sha_bytes(data),
            "raw_bytes_preserved": True, "status": "complete_packet_firstpass_checked",
            "responses": report}


def _source_pair_files(scoped_dir: Path, preflight_dir: Path):
    scoped_manifest = strict_loads((scoped_dir / "manifest.json").read_bytes())
    pairs = read_jsonl(scoped_dir / "model-pairs-v3.jsonl")
    payloads = read_jsonl(scoped_dir / "model-payloads-v3.jsonl")
    pair_count = len(pairs)
    if (pair_count <= 0 or len(payloads) != pair_count
            or scoped_manifest.get("prepared_pair_count") != pair_count
            or scoped_manifest.get("payload_count") != pair_count):
        raise ValueError("scoped pair/payload population differs from its frozen manifest")
    pair_by_id = RUNNER.unique_index(pairs, "pair_id", "scoped pair")
    payload_by_id = RUNNER.unique_index(payloads, "pair_id", "scoped payload")
    if set(pair_by_id) != set(payload_by_id):
        raise ValueError("scoped pair and payload identities differ")
    refs = read_jsonl(preflight_dir / "pair-ref-index.jsonl")
    dispatch_rows = read_jsonl(preflight_dir / "dispatch-pair-ids.jsonl")
    if len(refs) != pair_count:
        raise ValueError("preflight ref population differs from scoped pair count")
    by_id = RUNNER.unique_index(refs, "pair_id", "global pair-ref index")
    by_ref = {}
    for ref in refs:
        n = ref.get("pair_ref")
        if type(n) is not int or n in by_ref:
            raise ValueError("pair_ref must be a unique source-global integer")
        by_ref[n] = ref
    dispatch_ids = [r.get("pair_id") for r in dispatch_rows]
    dispatch_count = len(dispatch_ids)
    if (dispatch_count <= 0 or len(set(dispatch_ids)) != dispatch_count
            or set(dispatch_ids) != {r["pair_id"] for r in refs if r.get("dispatch_status") == "dispatched"}):
        raise ValueError("dispatch refs do not match preflight global index")
    if any(ref.get("dispatch_status") != "literal_protected_not_dispatched" for ref in refs if ref["pair_id"] not in set(dispatch_ids)):
        raise ValueError("preflight index has an unexpected non-dispatch status")
    if set(pair_by_id) != set(by_id):
        raise ValueError("preflight refs do not cover all scoped source pairs")
    partition = strict_loads((preflight_dir / "source-partition-receipt.json").read_bytes())
    protected_rows = read_jsonl(preflight_dir / "literal-protected-pair-ids.jsonl")
    protected_ids = [r.get("pair_id") for r in protected_rows]
    protected_count = len(protected_ids)
    if (partition.get("schema_version") != "indexed-dev-source-partition-v1"
            or partition.get("status") != "complete_source_derived_pre_model_partition"
            or partition.get("prepared_pair_count") != pair_count
            or partition.get("dispatch_pair_count") != dispatch_count
            or partition.get("literal_protected_pair_count") != protected_count
            or partition.get("dispatch_pair_ids_sha256") != RUNNER.digest_canonical(sorted(dispatch_ids))
            or partition.get("literal_protected_pair_ids_sha256") != RUNNER.digest_canonical(sorted(protected_ids))
            or len(set(protected_ids)) != protected_count
            or set(protected_ids) != set(by_id) - set(dispatch_ids)
            or dispatch_count + protected_count != pair_count
            or partition.get("labels_read") is not False or partition.get("inference_run") is not False
            or partition.get("scores_created") is not False):
        raise ValueError("source partition receipt does not prove an exact prepared-pair partition")
    return pair_by_id, payload_by_id, refs, by_id, by_ref, dispatch_ids, protected_ids


def prepare(output_dir: Path = DEFAULT_OUTPUT, scoped_dir: Path = SCOPED,
            preflight_dir: Path = PREFLIGHT) -> dict[str, Any]:
    output_dir, scoped_dir, preflight_dir = map(lambda x: Path(x).resolve(), (output_dir, scoped_dir, preflight_dir))
    if not output_dir.is_relative_to(ROOT / ".lab-output") or output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("packet output must be a new directory under .lab-output")
    code = verify_code()
    scoped_manifest = strict_loads((scoped_dir / "manifest.json").read_bytes())
    preflight_inventory = strict_loads((preflight_dir / "preflight-inventory.json").read_bytes())
    for name, pin in preflight_inventory.get("files", {}).items():
        path = preflight_dir / name
        if not path.is_file() or sha_file(path) != pin.get("sha256") or path.stat().st_size != pin.get("bytes"):
            raise ValueError("preflight artifact pin mismatch: " + name)
    for name, digest in scoped_manifest.get("artifact_sha256", {}).items():
        path = scoped_dir / name
        if not path.is_file() or sha_file(path) != digest:
            raise ValueError("scoped source artifact pin mismatch: " + name)
    if (scoped_manifest.get("schema_version") != "sku-body-invariant-scoped-rakuten-inventory-v1"
            or scoped_manifest.get("prepared_pair_count") != len(read_jsonl(scoped_dir / "model-pairs-v3.jsonl"))
            or scoped_manifest.get("payload_count") != len(read_jsonl(scoped_dir / "model-payloads-v3.jsonl"))
            or scoped_manifest.get("labels_read") is not False or scoped_manifest.get("model_inference_run") is not False
            or preflight_inventory.get("schema_version") != "indexed-dev-preflight-inventory-v1"):
        raise ValueError("scoped source/preflight manifest is not the frozen blind population")
    pair_by_id, payload_by_id, refs, ref_by_id, ref_by_number, dispatch_ids, protected_ids = _source_pair_files(scoped_dir, preflight_dir)
    pairs = []
    for pair_id in dispatch_ids:
        ref = ref_by_id[pair_id]
        pair, payload = pair_by_id[pair_id], payload_by_id[pair_id]
        if (ref.get("cache_key") != pair.get("cache_key")
                or ref.get("option_count") != len(pair.get("option_values", []))):
            raise ValueError("global pair-ref metadata mismatch")
        public = _public_payload(pair, payload, ref["pair_ref"])
        pairs.append({"pair_ref": ref["pair_ref"], "payload": public})
    pairs.sort(key=lambda x: x["pair_ref"])

    pin_paths = {
        "scoped_manifest": scoped_dir / "manifest.json",
        "scoped_freeze": scoped_dir / "freeze.json",
        "scoped_pairs": scoped_dir / "model-pairs-v3.jsonl",
        "scoped_payloads": scoped_dir / "model-payloads-v3.jsonl",
        "preflight_inventory": preflight_dir / "preflight-inventory.json",
        "preflight_partition": preflight_dir / "source-partition-receipt.json",
        "preflight_pair_ref_index": preflight_dir / "pair-ref-index.jsonl",
        "preflight_dispatch_ids": preflight_dir / "dispatch-pair-ids.jsonl",
    }
    inputs = file_pins(pin_paths)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".body-indexed-packets-", dir=output_dir.parent))
    try:
        packet_records = []
        chunks = []
        current = []
        for item in pairs:
            proposed = current + [item]
            proposed_bytes = len(canon({"protocol": INDEXED.PROTOCOL,
                "instructions": WORKER_INSTRUCTIONS, "pairs": proposed}) + b"\n")
            if current and (len(proposed) > MAX_PAIRS or proposed_bytes > MAX_PACKET_BYTES):
                chunks.append(current)
                current = [item]
            else:
                current = proposed
        if current:
            chunks.append(current)
        for packet_number, chunk in enumerate(chunks, 1):
            packet = {"protocol": INDEXED.PROTOCOL, "instructions": WORKER_INSTRUCTIONS,
                      "pairs": chunk}
            encoded = canon(packet) + b"\n"
            if len(chunk) > 1 and len(encoded) > MAX_PACKET_BYTES:
                raise ValueError("multi-pair packet exceeds target byte size")
            oversized = len(chunk) == 1 and len(encoded) > MAX_PACKET_BYTES
            name = f"packet-{packet_number:02d}.json"
            (stage / name).write_bytes(encoded)
            packet_records.append({"file": name, "bytes": len(encoded), "sha256": sha_bytes(encoded),
                                  "pair_refs": [x["pair_ref"] for x in chunk],
                                  "oversized_single_payload": oversized})
        preparation_history = [{"attempt": "route_dev_packet_set_v1", "status": "complete",
            "rule": "up to two complete contexts when packet is at most 90 KB; otherwise singleton context, including over-target singleton",
            "context_truncated": False, "source_files_modified": False,
            "model_inference_run": False, "labels_read": False, "scores_created": False}]
        root_text = root_instructions(output_dir.name, len(pairs), output_dir)
        index = {"schema_version": "sku-body-indexed-execution-packet-index-v1",
            "status": "complete_public_dispatch_packets_ready", "dataset": scoped_manifest["dataset"],
            "protocol": INDEXED.PROTOCOL, "prepared_pair_count": len(refs), "dispatch_pair_count": len(pairs),
            "literal_protected_pair_count": len(protected_ids), "dispatch_pair_refs": [x["pair_ref"] for x in pairs],
            "dispatch_option_count": sum(len(pair_by_id[ref_by_number[x["pair_ref"]]["pair_id"]]["option_values"])
                                         for x in pairs),
            "option_counts_by_pair_ref": [{"pair_ref": x["pair_ref"],
                "option_count": len(pair_by_id[ref_by_number[x["pair_ref"]]["pair_id"]]["option_values"])}
                for x in pairs],
            "source_pair_ref_index_sha256": inputs["preflight_pair_ref_index"]["sha256"],
            "source_dispatch_ids_sha256": inputs["preflight_dispatch_ids"]["sha256"],
            "source_inputs": inputs, "live_code_sha256": code,
            "pair_vector_sha256": sha_bytes(canon([{ "pair_ref": x["pair_ref"],
                "option_values": pair_by_id[ref_by_number[x["pair_ref"]]["pair_id"]]["option_values"],
                "candidate_vector": pair_by_id[ref_by_number[x["pair_ref"]]["pair_id"]]["candidate_vector"]}
                for x in pairs])),
            "packet_count": len(packet_records), "max_pairs_per_packet": MAX_PAIRS,
            "target_max_packet_bytes": MAX_PACKET_BYTES,
            "largest_packet_bytes": max(x["bytes"] for x in packet_records),
            "oversized_single_packet_count": sum(x["oversized_single_payload"] for x in packet_records),
            "packets": packet_records, "worker_instructions": WORKER_INSTRUCTIONS,
            "root_instructions": root_text,
            "preparation_history": preparation_history,
            "packetizer_sha256": sha_file(Path(__file__)),
            "supersedes_packet_indexes": [],
            "model_inference_run": False, "labels_read": False, "scores_created": False,
            "rendered_v3_responses_created": False}
        index_bytes = canon(index) + b"\n"
        (stage / "packet-index.json").write_bytes(index_bytes)
        (stage / "worker-instructions.txt").write_text(WORKER_INSTRUCTIONS, encoding="utf-8")
        (stage / "ROOT-INSTRUCTIONS.txt").write_text(root_text, encoding="utf-8")
        (stage / "prepare-history.jsonl").write_bytes(b"".join(canon(x) + b"\n" for x in preparation_history))
        lock = output_dir.with_name(output_dir.name + ".lock")
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            if output_dir.exists() or output_dir.is_symlink():
                raise FileExistsError("packet output appeared during preparation")
            os.rename(stage, output_dir)
        finally:
            lock.unlink(missing_ok=True)
        return index
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _verify_packet_index(index_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    index_dir = Path(index_dir).resolve()
    if not index_dir.is_relative_to(ROOT / ".lab-output") or index_dir.is_symlink():
        raise ValueError("packet index directory must be regular .lab-output data")
    index_path = index_dir / "packet-index.json"
    index = strict_loads(index_path.read_bytes())
    if index.get("schema_version") != "sku-body-indexed-execution-packet-index-v1" or index.get("status") != "complete_public_dispatch_packets_ready":
        raise ValueError("packet index schema/status mismatch")
    if verify_code() != index.get("live_code_sha256"):
        raise ValueError("live code hashes differ from frozen packet index")
    if sha_file(Path(__file__)) != index.get("packetizer_sha256"):
        raise ValueError("live packetizer code differs from frozen packet index")
    for item in index.get("source_inputs", {}).values():
        path = ROOT / item["path"]
        if (not path.is_file() or path.stat().st_size != item["bytes"] or sha_file(path) != item["sha256"]):
            raise ValueError("packet source input pin mismatch: " + item["path"])
    for packet in index.get("packets", []):
        path = index_dir / packet["file"]
        if not path.is_file() or path.stat().st_size != packet["bytes"] or sha_file(path) != packet["sha256"]:
            raise ValueError("public packet bytes/hash mismatch: " + packet["file"])
    if ((index_dir / "worker-instructions.txt").read_text(encoding="utf-8") != index.get("worker_instructions")
            or (index_dir / "ROOT-INSTRUCTIONS.txt").read_text(encoding="utf-8") != index.get("root_instructions")
            or [strict_loads(x) for x in (index_dir / "prepare-history.jsonl").read_bytes().splitlines()]
                != index.get("preparation_history")):
        raise ValueError("durable packet instructions/history differ from indexed contract")
    source_inputs = index.get("source_inputs", {})
    scoped_dir = ROOT / Path(source_inputs["scoped_manifest"]["path"]).parent
    preflight_dir = ROOT / Path(source_inputs["preflight_inventory"]["path"]).parent
    pair_by_id, payload_by_id, refs, ref_by_id, ref_by_number, dispatch_ids, protected_ids = _source_pair_files(scoped_dir, preflight_dir)
    if index.get("dispatch_pair_count") != len(dispatch_ids) or index.get("dispatch_pair_refs") != sorted(ref_by_id[x]["pair_ref"] for x in dispatch_ids):
        raise ValueError("packet index dispatch population differs from frozen source refs")
    seen_packet_refs = []
    for packet_record in index["packets"]:
        packet = strict_loads((index_dir / packet_record["file"]).read_bytes())
        packet_refs = [x.get("pair_ref") for x in packet.get("pairs", [])]
        if (packet.get("protocol") != INDEXED.PROTOCOL or len(packet_refs) > MAX_PAIRS
                or packet_refs != packet_record.get("pair_refs")
                or (len(packet_refs) > 1 and packet_record["bytes"] > MAX_PACKET_BYTES)
                or (len(packet_refs) == 1 and packet_record.get("oversized_single_payload") !=
                    (packet_record["bytes"] > MAX_PACKET_BYTES))):
            raise ValueError("packet contents do not match the frozen indexed packet record")
        for packet_item in packet.get("pairs", []):
            ref = packet_item["pair_ref"]
            pair_id = ref_by_number[ref]["pair_id"]
            pair, payload = pair_by_id[pair_id], payload_by_id[pair_id]
            public = packet_item.get("payload")
            expected_public = _public_payload(pair, payload, ref)
            if public != expected_public:
                raise ValueError("packet source context/candidate vector differs from frozen public payload")
        seen_packet_refs.extend(packet_refs)
    if sorted(seen_packet_refs) != index.get("dispatch_pair_refs") or len(seen_packet_refs) != len(set(seen_packet_refs)):
        raise ValueError("packet files do not cover dispatched global refs exactly once")
    return index, {"pairs": pair_by_id, "payloads": payload_by_id, "refs": ref_by_id,
                   "dispatch_ids": dispatch_ids, "by_number": ref_by_number}


def persist(raw_jsonl: Path, index_dir: Path, output_dir: Path) -> dict[str, Any]:
    raw_jsonl, index_dir, output_dir = map(lambda x: Path(x).resolve(), (raw_jsonl, index_dir, output_dir))
    if not output_dir.is_relative_to(ROOT / ".lab-output") or output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("persisted output must be a new directory under .lab-output")
    if (not raw_jsonl.is_relative_to(ROOT / ".lab-output") or raw_jsonl.is_symlink()
            or not raw_jsonl.is_file()):
        raise ValueError("raw first pass must be a regular file under .lab-output")
    index, source = _verify_packet_index(index_dir)
    raw_bytes = raw_jsonl.read_bytes()
    raw_rows = []
    raw_lines = raw_bytes.splitlines(keepends=True)
    if not raw_lines or any(not line.strip() for line in raw_lines):
        raise ValueError("raw first pass must be nonempty JSONL without blank lines")
    for i, line in enumerate(raw_lines, 1):
        row = strict_loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"raw first-pass line {i} is not an object")
        raw_rows.append(row)
    by_ref = {r["pair_ref"]: r for r in source["refs"].values()}
    refs = []
    for row in raw_rows:
        ref = row.get("pair_ref")
        if type(ref) is not int or ref not in by_ref or by_ref[ref]["pair_id"] not in source["dispatch_ids"]:
            raise ValueError("raw first pass contains foreign, boolean, or protected pair_ref")
        refs.append(ref)
    expected = sorted(source["refs"][pid]["pair_ref"] for pid in source["dispatch_ids"])
    if sorted(refs) != expected or len(refs) != len(set(refs)):
        raise ValueError("raw first pass must cover every dispatched global pair_ref exactly once")

    rendered_rows, ledger = [], []
    for row, ref in zip(raw_rows, refs):
        pair_id = by_ref[ref]["pair_id"]
        pair, payload = source["pairs"][pair_id], source["payloads"][pair_id]
        try:
            rendered = INDEXED.render_indexed_response(row, pair, payload, ref)
            rendered_rows.append(rendered)
            ledger.append({"pair_ref": ref, "status": "rendered_structurally_valid",
                           "semantic_retry_performed": False})
        except (KeyError, TypeError, ValueError) as exc:
            ledger.append({"pair_ref": ref, "status": "invalid_unknown_firstpass_preserved",
                           "error_type": type(exc).__name__, "error": str(exc),
                           "semantic_retry_performed": False})

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".body-indexed-persist-", dir=output_dir.parent))
    try:
        # Exact authored bytes, including original whitespace and final newline.
        (stage / "raw-indexed-responses.jsonl").write_bytes(raw_bytes)
        (stage / "rendered-v3-responses.jsonl").write_bytes(b"".join(canon(x) + b"\n" for x in rendered_rows))
        (stage / "firstpass-status.jsonl").write_bytes(b"".join(canon(x) + b"\n" for x in ledger))
        receipt = {"schema_version": "sku-body-indexed-firstpass-persist-v1",
            "status": "complete_firstpass_raw_preserved", "raw_sha256": sha_bytes(raw_bytes),
            "raw_bytes": len(raw_bytes), "raw_response_count": len(raw_rows),
            "structurally_rendered_count": len(rendered_rows),
            "invalid_unknown_count": len(raw_rows) - len(rendered_rows),
            "pair_refs": refs, "packet_index_sha256": sha_file(index_dir / "packet-index.json"),
            "raw_and_rendered_are_distinct_artifacts": True, "raw_bytes_equal_rendered": False,
            "semantic_retry_performed": False, "native_source_revalidation_performed": False,
            "labels_read": False, "scores_created": False}
        (stage / "persist-receipt.json").write_bytes(canon(receipt) + b"\n")
        lock = output_dir.with_name(output_dir.name + ".lock")
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            if output_dir.exists() or output_dir.is_symlink():
                raise FileExistsError("persisted output appeared during write")
            os.rename(stage, output_dir)
        finally:
            lock.unlink(missing_ok=True)
        return receipt
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--scoped-dir", type=Path, default=SCOPED)
    p.add_argument("--preflight-dir", type=Path, default=PREFLIGHT)
    q = sub.add_parser("persist")
    q.add_argument("--raw-jsonl", type=Path, required=True)
    q.add_argument("--index-dir", type=Path, default=DEFAULT_OUTPUT)
    q.add_argument("--output-dir", type=Path, required=True)
    v = sub.add_parser("validate-packet")
    v.add_argument("--raw-jsonl", type=Path, required=True)
    v.add_argument("--index-dir", type=Path, default=DEFAULT_OUTPUT)
    v.add_argument("--packet-file", required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.output_dir, args.scoped_dir, args.preflight_dir)
    elif args.command == "persist":
        result = persist(args.raw_jsonl, args.index_dir, args.output_dir)
    else:
        result = validate_packet_draft(args.raw_jsonl, args.index_dir, args.packet_file)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
