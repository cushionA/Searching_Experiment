"""Audit real SKU/model-input token lengths with local tokenizer.json files only.

No model weights are imported or loaded. The input strings match compare_models.py:
SKU mode is sku_label; title-sku mode is product_title + " / " + sku_label.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MODELS = {
    "minilm": (ROOT / ".deps/sku-matching-model", HERE / "model-manifest.json"),
    "bekko": (ROOT / ".deps/sku-bekko-model", HERE / "manifests/bekko.json"),
    "granite": (ROOT / ".deps/sku-granite-model", HERE / "manifests/granite.json"),
    "ruri": (ROOT / ".deps/sku-ruri-model", HERE / "manifests/ruri.json"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def stats(values: list[int]) -> dict:
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    def percentile(q: float) -> float:
        position = (len(ordered) - 1) * q
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return {"count": len(values), "min": ordered[0], "p50": percentile(0.50),
            "p95": percentile(0.95), "max": ordered[-1]}


def model_text(record: dict, mode: str) -> str:
    if mode == "sku":
        return record["sku_label"]
    return f"{record['product_title']} / {record['sku_label']}"


def get_sku_label_token_indices(offsets: list[tuple[int, int]], start: int, end: int) -> list[int]:
    return [i for i, (left, right) in enumerate(offsets) if right > left and left < end and right > start]


def audit_dataset(tokenizer, model_key: str, max_length: int, name: str, path: Path) -> dict:
    pair = json.loads(path.read_text(encoding="utf-8"))
    summary = {}
    details = []
    for mode in ("sku", "title-sku"):
        lengths = []
        truncated = []
        mode_counts = Counter()
        for site in ("au", "rakuten"):
            for record in pair[site]:
                text = model_text(record, mode)
                encoded = tokenizer.encode(text, add_special_tokens=True)
                length = len(encoded.ids)
                lengths.append(length)
                over = length > max_length
                mode_counts["records"] += 1
                mode_counts["over_limit"] += int(over)
                if mode == "sku":
                    sku_start = 0
                else:
                    sku_start = len(record["product_title"]) + 3  # " / "
                label_end = sku_start + len(record["sku_label"])
                label_indices = get_sku_label_token_indices(encoded.offsets, sku_start, label_end)
                total_label_tokens = len(label_indices)
                content_indices = [i for i,(left,right) in enumerate(encoded.offsets) if right > left]
                capacity = max_length - tokenizer.num_special_tokens_to_add(False)
                retained_indices = set(content_indices[:max(0, capacity)])
                kept_label_tokens = sum(index in retained_indices for index in label_indices)
                dropped_label_tokens = total_label_tokens - kept_label_tokens
                if dropped_label_tokens:
                    mode_counts["sku_label_token_dropped"] += 1
                if over:
                    first_dropped = encoded.offsets[max_length] if max_length < len(encoded.offsets) else None
                    truncated.append({
                        "site": site,
                        "product_id": record["product_id"],
                        "sku_id": record["sku_id"],
                        "source_attributes": record.get("attributes", {}),
                        "mode": mode,
                        "input_text": text,
                        "token_length_with_special_tokens": length,
                        "max_sequence_length": max_length,
                        "sku_label_token_count": total_label_tokens,
                        "sku_label_tokens_retained": kept_label_tokens,
                        "sku_label_tokens_dropped": dropped_label_tokens,
                        "first_dropped_token_char_span": list(first_dropped) if first_dropped else None,
                        "first_dropped_text": text[first_dropped[0]:first_dropped[1]] if first_dropped and first_dropped[1] > first_dropped[0] else None,
                    })
        summary[mode] = {"model_input_definition": "sku_label" if mode == "sku" else "product_title + ' / ' + sku_label",
                         "record_counts": dict(mode_counts), "token_length": stats(lengths),
                         "truncated_examples": truncated}
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    try:
        from tokenizers import Tokenizer
    except ImportError as exc:
        raise SystemExit("Install/use tokenizers in .deps/sku-matching-venv") from exc

    models = {}
    for key, (model_dir, manifest_path) in MODELS.items():
        if not manifest_path.is_file():
            raise FileNotFoundError(manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        max_length = manifest.get("max_sequence_length")
        if max_length is None:
            max_length = manifest.get("maximum_sequence_length")
        if max_length is None:
            raise ValueError(f"Missing max sequence length for {key}")
        tokenizer_path = model_dir / "tokenizer.json"
        expected = manifest.get("files", {}).get("tokenizer.json", {})
        if expected.get("sha256") and sha256(tokenizer_path) != expected["sha256"]:
            raise RuntimeError(f"Local tokenizer SHA256 differs from pinned manifest: {key}")
        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        tokenizer.no_truncation()
        tokenizer.no_padding()
        models[key] = {"repo": manifest["repo"], "revision": manifest["revision"],
                       "max_sequence_length": max_length,
                       "tokenizer_sha256": sha256(tokenizer_path), "tokenizer": tokenizer}

    result = {"created_at_utc": datetime.now(timezone.utc).isoformat(),
              "method": "local tokenizers.Tokenizer from pinned tokenizer.json only; no model weights loaded",
              "query_direction_in_comparator": "Rakuten records are queries; au records are candidates",
              "source_attribute_note": "source_attributes are supplied record attributes; embedding inputs are only the explicitly listed title/SKU strings",
              "datasets": {}, "models": {}}
    paths = {name: HERE / "results" / f"real-{name}-pair.jsonl" for name in ("original", "sibling")}
    result["source_sha256"] = {name: sha256(path) for name, path in paths.items()}
    for model_key, values in models.items():
        tokenizer = values.pop("tokenizer")
        result["models"][model_key] = {k: v for k, v in values.items()}
        result["datasets"][model_key] = {
            name: audit_dataset(tokenizer, model_key, values["max_sequence_length"], name, path)
            for name, path in paths.items()
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        target.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "models": list(models),
                      "source_sha256": result["source_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
