#!/usr/bin/env python3
"""Run a frozen, label-free Japanese NLI trial on real SKU evidence packets."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import json
import math
from pathlib import Path
import platform
import time
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_INPUT = ROOT / ".lab-output/sku-gpu-condition-tasks-20261010-v2/model/packets.jsonl"
DEFAULT_MODEL = ROOT / ".deps/sku-nli-model"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-nonllm-nli-trial-20261010-v1"
MODEL_ID = "0x3/bert-base-japanese-v3_nli-jsnli-jnli-jsick"
REVISION = "21df45469d02809dc430e3ac5f5d6f67b63aabf3"
MAX_MODEL_BYTES = 500_000_000
MAX_INPUT_TOKENS = 512
CONFIDENCE_THRESHOLD = 0.90
LABELS = ("entailment", "neutral", "contradiction")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            rows.append(item)
    return rows


def hypothesis_for(condition: dict[str, Any]) -> str:
    """Create one Japanese statement directly from a selected condition."""
    kind = condition.get("atom_kind")
    value = condition.get("selected_value_raw")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("condition has no selected_value_raw")
    atom = condition.get("atom_value")
    axis = condition.get("axis_name_raw")
    if kind == "color":
        return f"選択された商品のカラーは「{value}」である。"
    if kind == "dimension":
        return f"選択された商品のサイズは「{value}」である。"
    if kind == "piece_total":
        if not isinstance(atom, int) or atom < 0:
            raise ValueError("piece_total condition has invalid atom_value")
        return f"選択された商品に含まれる枚数は{atom}枚である。"
    if kind == "component_presence":
        if not isinstance(atom, dict) or not isinstance(atom.get("value"), bool):
            raise ValueError("component_presence condition has invalid atom_value")
        component = atom.get("component")
        component_name = {"lace": "レースカーテン", "blanket": "毛布"}.get(component)
        if not component_name:
            if not isinstance(axis, str) or not axis:
                raise ValueError("component_presence condition has no component name")
            component_name = axis
        verb = "含まれる" if atom["value"] else "含まれない"
        return f"選択された商品に{component_name}は{verb}。"
    if kind in {"named_size", "variant", "fabric"}:
        if not isinstance(axis, str) or not axis:
            axis = "商品仕様"
        return f"選択された商品の{axis}は「{value}」である。"
    raise ValueError(f"unsupported condition atom_kind: {kind!r}")


def collect_pairs(packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs = []
    for packet in packets:
        condition = packet.get("condition")
        if not isinstance(condition, dict):
            raise ValueError(f"packet {packet.get('packet_id')} has no condition")
        hypothesis = hypothesis_for(condition)
        for evidence_index, evidence in enumerate(packet.get("evidence") or []):
            if not isinstance(evidence, dict):
                raise ValueError(f"packet {packet.get('packet_id')} evidence is not an object")
            premise = evidence.get("quote")
            if not isinstance(premise, str) or not premise.strip():
                raise ValueError(f"packet {packet.get('packet_id')} evidence quote is empty")
            pairs.append({
                "packet_id": packet.get("packet_id"),
                "case_id": packet.get("case_id"),
                "au_row_key": packet.get("au_row_key"),
                "condition": condition,
                "evidence_index": evidence_index,
                "evidence_binding": evidence,
                "premise": premise,
                "hypothesis": hypothesis,
            })
    return pairs


def resolve_mapping(card_text: str, config: dict[str, Any]) -> tuple[str, ...]:
    """Use the model's own README declaration; reject unverified config labels."""
    normalized = "".join(card_text.split()).replace(" ", "")
    expected = '{0:"entailment",1:"neutral",2:"contradiction}'
    if expected not in normalized:
        raise RuntimeError("pinned model card no longer declares expected label order")
    expected_generic = {"0": "LABEL_0", "1": "LABEL_1", "2": "LABEL_2"}
    if config.get("id2label") != expected_generic:
        raise RuntimeError(f"config label mapping changed: {config.get('id2label')!r}")
    return LABELS


def make_frozen_manifest(input_path: Path, model_dir: Path) -> dict[str, Any]:
    files = sorted(p for p in model_dir.rglob("*") if p.is_file() and ".cache" not in p.parts)
    model_bytes = sum(p.stat().st_size for p in files)
    if model_bytes > MAX_MODEL_BYTES:
        raise RuntimeError(f"model/tokenizer snapshot exceeds 500MB: {model_bytes} bytes")
    for name in ("README.md", "config.json", "tokenizer_config.json", "model.safetensors", "vocab.txt"):
        if not (model_dir / name).is_file():
            raise FileNotFoundError(f"required pinned snapshot file missing: {name}")
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_id": MODEL_ID,
        "revision": REVISION,
        "model_tokenizer_snapshot_bytes": model_bytes,
        "snapshot_sha256": {p.relative_to(model_dir).as_posix(): sha256_file(p) for p in files},
        "card_sha256": sha256_file(model_dir / "README.md"),
        "config_sha256": sha256_file(model_dir / "config.json"),
        "tokenizer_config_sha256": sha256_file(model_dir / "tokenizer_config.json"),
        "input_path": str(input_path),
        "input_sha256": sha256_file(input_path),
        "script_path": str(Path(__file__).resolve()),
        "script_sha256": sha256_file(Path(__file__).resolve()),
        "thresholds_frozen_before_inference": {
            "max_input_tokens": MAX_INPUT_TOKENS,
            "confidence_threshold": CONFIDENCE_THRESHOLD,
            "labels": list(LABELS),
            "fp32": True,
            "dynamic_int8_linear": True
        },
        "versions": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "torch": metadata.version("torch"),
            "transformers": metadata.version("transformers"),
            "huggingface_hub": metadata.version("huggingface-hub"),
            "tokenizers": metadata.version("tokenizers"),
            "fugashi": metadata.version("fugashi"),
            "unidic-lite": metadata.version("unidic-lite"),
        },
        "device": "cpu",
        "input_policy": "27 condition packets / 97 quoted evidence blocks, no answer labels, no truncation; inputs over 512 tokens excluded as unknown.",
        "label_mapping_provenance": {
            "card_declares": {"0": "entailment", "1": "neutral", "2": "contradiction"},
            "config_id2label": json.loads((model_dir / "config.json").read_text(encoding="utf-8")).get("id2label"),
            "mismatch_note": "config exposes generic LABEL_0..2; README's explicit mapping is used. A later README snippet for another DeBERTa model gives a different order and is not used."
        },
        "outputs_are": "model probability proposals, not SKU correctness or production decisions"
    }


def probability_record(logits: list[float]) -> dict[str, Any]:
    maximum = max(logits)
    exponentials = [math.exp(value - maximum) for value in logits]
    total = sum(exponentials)
    probs = [value / total for value in exponentials]
    index = max(range(len(probs)), key=probs.__getitem__)
    confidence = float(probs[index])
    return {
        "probabilities": {label: float(prob) for label, prob in zip(LABELS, probs, strict=True)},
        "predicted_label": LABELS[index],
        "predicted_confidence": confidence,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "proposal": LABELS[index] if confidence >= CONFIDENCE_THRESHOLD else "unknown",
    }


def run_model(name: str, model: Any, tokenizer: Any, pairs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    import torch
    model.eval()
    prepared, excluded = [], []
    lengths = []
    for pair in pairs:
        encoded = tokenizer(pair["premise"], pair["hypothesis"], add_special_tokens=True,
                            truncation=False, return_tensors=None)
        token_count = len(encoded["input_ids"])
        lengths.append(token_count)
        if token_count > MAX_INPUT_TOKENS:
            excluded.append({
                **pair,
                "token_count_untruncated": token_count,
                "error_type": "InputTooLong",
                "error": f"{token_count} tokens exceeds {MAX_INPUT_TOKENS}; excluded without truncation (unknown)",
            })
        else:
            prepared.append((pair, token_count))
    records, total_seconds = [], 0.0
    for pair, token_count in prepared:
        features = tokenizer(pair["premise"], pair["hypothesis"], add_special_tokens=True,
                             truncation=False, return_tensors="pt")
        features = {key: value.to("cpu") for key, value in features.items()}
        start = time.perf_counter()
        with torch.inference_mode():
            logits = model(**features).logits[0].to(torch.float64).tolist()
        elapsed = time.perf_counter() - start
        total_seconds += elapsed
        records.append({
            **pair,
            "token_count_untruncated": token_count,
            "latency_seconds": elapsed,
            **probability_record(logits),
        })
    summary = {
        "implementation": name,
        "pair_count": len(pairs),
        "inferred_count": len(records),
        "excluded_long_count": len(excluded),
        "total_inference_seconds": total_seconds,
        "mean_latency_seconds": total_seconds / len(records) if records else None,
        "max_token_count": max(lengths, default=0),
        "max_input_tokens": MAX_INPUT_TOKENS,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "proposal_counts": {label: sum(r["proposal"] == label for r in records) for label in (*LABELS, "unknown")},
        "not_an_accuracy_measure": True,
    }
    return records, excluded, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {args.output}")
    if not args.input.is_file():
        raise FileNotFoundError(args.input)
    if not args.model_dir.is_dir():
        raise FileNotFoundError(f"download pinned snapshot first: {args.model_dir}")

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    torch.set_num_threads(min(4, torch.get_num_threads()))
    torch.use_deterministic_algorithms(True, warn_only=True)
    card_text = (args.model_dir / "README.md").read_text(encoding="utf-8")
    config = json.loads((args.model_dir / "config.json").read_text(encoding="utf-8"))
    resolve_mapping(card_text, config)
    manifest = make_frozen_manifest(args.input, args.model_dir)
    packets = read_jsonl(args.input)
    if len(packets) != 27 or sum(len(row.get("evidence") or []) for row in packets) != 97:
        raise RuntimeError("input must be 27 packets with exactly 97 evidence blocks")
    pairs = collect_pairs(packets)
    if len(pairs) != 97:
        raise RuntimeError(f"expected 97 pairs, found {len(pairs)}")
    manifest["packet_count"] = len(packets)
    manifest["condition_count"] = len(packets)
    manifest["evidence_pair_count"] = len(pairs)
    manifest["evidence_binding_sha256"] = sha256_bytes(json.dumps(
        [pair["evidence_binding"] for pair in pairs], ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8"))
    # Freeze source, model, versions, mappings and thresholds before predictions.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.mkdir()
    (args.output / "frozen_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    tokenizer = AutoTokenizer.from_pretrained(str(args.model_dir), local_files_only=True)
    if tokenizer.__class__.__name__ != "BertJapaneseTokenizer":
        raise RuntimeError(f"unexpected tokenizer class: {tokenizer.__class__.__name__}")
    load_start = time.perf_counter()
    model = AutoModelForSequenceClassification.from_pretrained(
        str(args.model_dir), local_files_only=True, torch_dtype=torch.float32)
    model.to("cpu").eval()
    loaded_id2label = {str(key): value for key, value in model.config.id2label.items()}
    if model.config.num_labels != 3 or loaded_id2label != config["id2label"]:
        raise RuntimeError("loaded model config differs from pinned config")
    load_seconds = time.perf_counter() - load_start

    fp32, fp32_excluded, fp32_summary = run_model("fp32_cpu", model, tokenizer, pairs)
    int8_model = torch.ao.quantization.quantize_dynamic(
        deepcopy(model).cpu(), {torch.nn.Linear}, dtype=torch.qint8)
    int8, int8_excluded, int8_summary = run_model("dynamic_int8_linear_cpu", int8_model, tokenizer, pairs)
    binding_key = lambda x: (x["packet_id"], x["evidence_index"], x["evidence_binding"])
    if [binding_key(x) for x in fp32] != [binding_key(x) for x in int8]:
        raise RuntimeError("FP32 and INT8 inference bindings differ")
    if [x["evidence_binding"] for x in fp32] != [x["evidence_binding"] for x in pairs]:
        raise RuntimeError("FP32 evidence binding changed")

    def write_jsonl(filename: str, rows: list[dict[str, Any]]) -> None:
        with (args.output / filename).open("x", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    write_jsonl("fp32_predictions.jsonl", fp32)
    write_jsonl("dynamic_int8_predictions.jsonl", int8)
    write_jsonl("fp32_excluded.jsonl", fp32_excluded)
    write_jsonl("dynamic_int8_excluded.jsonl", int8_excluded)
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(args.input),
        "input_sha256": manifest["input_sha256"],
        "model_id": MODEL_ID,
        "revision": REVISION,
        "model_card_label_order": list(LABELS),
        "config_id2label": config["id2label"],
        "card_config_mismatch": "Card declares indices 0/1/2 as entailment/neutral/contradiction; config uses generic LABEL_0..2. A separate README example for another DeBERTa has a different order and was not used.",
        "model_load_seconds": load_seconds,
        "threshold": CONFIDENCE_THRESHOLD,
        "fp32": fp32_summary,
        "dynamic_int8_linear": int8_summary,
        "excluded_unknown_count": {"fp32": len(fp32_excluded), "dynamic_int8_linear": len(int8_excluded)},
        "interpretation": "Probabilities are proposals, not SKU correctness. Downstream accepts only if every required condition has verified evidence; unknown is internal and excluded externally, with no human review.",
    }
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.output / "summary.txt").write_text(
        "Japanese NLI CPU trial — model proposals only\n"
        f"Model: {MODEL_ID}@{REVISION}\nPackets / conditions: {len(packets)}\nEvidence pairs: {len(pairs)}\n"
        f"Label order (pinned card): {', '.join(LABELS)}\nThreshold: {CONFIDENCE_THRESHOLD:.2f} max-class probability\n"
        f"FP32: inferred {fp32_summary['inferred_count']}/97; elapsed {fp32_summary['total_inference_seconds']:.2f}s; proposals {fp32_summary['proposal_counts']}\n"
        f"Dynamic INT8 Linear: inferred {int8_summary['inferred_count']}/97; elapsed {int8_summary['total_inference_seconds']:.2f}s; proposals {int8_summary['proposal_counts']}\n"
        f"Long-input exclusions (unknown): FP32 {len(fp32_excluded)}, INT8 {len(int8_excluded)}\n"
        f"Model load: {load_seconds:.2f}s\n"
        "No labels were loaded or compared. Probabilities are not SKU accuracy. Final acceptance remains downstream: every required condition must have verified evidence; unknown remains internal and is excluded externally, with no human review.\n",
        encoding="utf-8")


if __name__ == "__main__":
    main()
