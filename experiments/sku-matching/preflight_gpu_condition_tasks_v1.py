"""Verify source spans, fixed pools, and actual pinned-tokenizer prompt lengths."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def preflight(prepared: Path, source: Path, claude_code: Path, output: Path, tokenizer_cache: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    sys.path.insert(0, str(claude_code))
    import sku_gate_sources
    code = ROOT / "experiments/sku-matching/kaggle_gpu_condition_runner_v1.py"
    spec = importlib.util.spec_from_file_location("condition_runner_preflight", code)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(runner.MODEL["name"], revision=runner.MODEL["revision"],
                                            cache_dir=str(tokenizer_cache), trust_remote_code=False)
    runner.symbol_token_ids(tokenizer)
    pools = read_jsonl(prepared / "host/fullpools.jsonl")
    original = read_jsonl(source / "source-cases.jsonl")
    packets = read_jsonl(prepared / "model/packets.jsonl")
    if [p["case_id"] for p in pools] != [p["case_id"] for p in original]:
        raise ValueError("Host case inventory/order differs from source")
    for host, case in zip(pools, original):
        if host["au_rows"] != case["au_rows"]:
            raise ValueError("Fixed AU pool changed: " + host["case_id"])
    if len({p["packet_id"] for p in packets}) != len(packets):
        raise ValueError("Duplicate packet IDs")
    by_case = {c["case_id"]: c for c in pools}
    store = sku_gate_sources.RawStore(ROOT)
    prompts = []
    count = 0
    for packet in packets:
        runner.validate_packet(packet)
        host = by_case[packet["case_id"]]
        if packet["au_row_key"] != host["focus_row_key"]:
            raise ValueError("Packet escaped fixed source row")
        expected_side = "au" if packet["condition"]["side"] == "rakuten" else "rakuten"
        for block in packet["evidence"]:
            ref, offset = block["source_ref"], block["leaf_offset"]
            if ref["source_side"] != expected_side:
                raise ValueError("Evidence comes from condition side, not comparison side")
            span = {"raw_file": ref["raw_file"], "sha256": block["source_sha256"],
                    "locator": offset["locator"], "start": offset["start"], "end": offset["end"], "quote": block["quote"]}
            if not sku_gate_sources.verify_span(store, span):
                raise ValueError("Unresolved original-source quote: " + packet["packet_id"])
            count += 1
        for index, mapping in enumerate(runner.relation_permutations()):
            prompt = runner.build_prompt(packet, mapping)
            rendered = tokenizer.apply_chat_template([{"role": "user", "content": prompt}], tokenize=False,
                                                       add_generation_prompt=True, enable_thinking=False)
            ids = tokenizer.encode(rendered, add_special_tokens=False)
            if len(ids) > runner.MAX_PREFILL_TOKENS:
                raise ValueError("Over-limit prompt, no truncation permitted: " + packet["packet_id"])
            prompts.append({"packet_id": packet["packet_id"], "permutation_index": index, "tokens": len(ids),
                            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                            "rendered_prompt_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                            "token_ids_sha256": runner.canonical_sha(ids)})
    result = {"schema": "gpu-condition-preflight-v1", "status": "pass", "labels_read": False,
              "case_count": len(pools), "full_pool_row_references": sum(len(c["au_rows"]) for c in pools),
              "packet_count": len(packets), "verified_quote_count": count,
              "packet_sha256": sha(prepared / "model/packets.jsonl"),
              "host_fullpools_sha256": sha(prepared / "host/fullpools.jsonl"),
              "runner_sha256": sha(code), "model": runner.MODEL,
              "preflight_code_sha256": sha(Path(__file__)),
              "tokenizer_class": type(tokenizer).__name__, "symbol_token_ids": runner.symbol_token_ids(tokenizer),
              "max_prompt_tokens": max((p["tokens"] for p in prompts), default=0),
              "prompts": prompts, "source_file_hashes": dict(sorted(store._sha.items()))}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {k: v for k, v in result.items() if k not in {"prompts", "source_file_hashes"}}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prepared", required=True, type=Path)
    ap.add_argument("--source", required=True, type=Path)
    ap.add_argument("--claude-code", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--tokenizer-cache", required=True, type=Path)
    args = ap.parse_args()
    print(json.dumps(preflight(args.prepared, args.source, args.claude_code, args.output, args.tokenizer_cache), indent=2))
