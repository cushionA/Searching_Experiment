#!/usr/bin/env python3
"""Bounded short-claim ablation for the frozen Japanese SKU NLI evidence set."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
V1_DIR = ROOT / ".lab-output/sku-nonllm-nli-trial-20261010-v1"
DEFAULT_INPUT = ROOT / ".lab-output/sku-gpu-condition-tasks-20261010-v2/model/packets.jsonl"
MODEL_DIR = ROOT / ".deps/sku-nli-model"
OUTPUT = ROOT / ".lab-output/sku-nonllm-nli-trial-20261010-v2"
CONFIDENCE_THRESHOLD = 0.90

spec = importlib.util.spec_from_file_location("trial_nonllm_nli_v1", HERE / "trial_nonllm_nli_v1.py")
if not spec or not spec.loader:
    raise RuntimeError("could not load frozen v1 runner")
v1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v1)


def _fmt_number(value: int | float) -> str:
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def plain_hypothesis(condition: dict) -> str:
    """Short Japanese claim derived only from the condition in this input packet."""
    kind = condition.get("atom_kind")
    atom = condition.get("atom_value")
    value = condition.get("selected_value_raw")
    if kind == "color":
        return f"色は{atom}。"
    if kind == "dimension":
        if not isinstance(atom, dict):
            raise ValueError("dimension condition has no typed atom")
        role = atom.get("role")
        values = atom.get("value")
        labels = atom.get("labels") or []
        if role == "labeled":
            if not isinstance(values, list) or len(values) != len(labels) or not labels:
                raise ValueError("labeled dimension must retain matching labels and values")
            rendered = "、".join(f"{label}は{_fmt_number(number)}cm" for label, number in zip(labels, values, strict=True))
            return f"{rendered}。"
        if role == "generic":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("generic dimension has no source selected_value_raw")
            return f"サイズは{value}。"
        raise ValueError(f"unsupported dimension role: {role!r}")
    if kind == "component_presence":
        if not isinstance(atom, dict) or not isinstance(atom.get("value"), bool):
            raise ValueError("component presence value is invalid")
        name = {"lace": "レースカーテン", "blanket": "毛布"}.get(atom.get("component"))
        if not name:
            axis = condition.get("axis_name_raw")
            if not isinstance(axis, str) or not axis:
                raise ValueError("component has no source name")
            name = axis
        return f"{name}が付属する。" if atom["value"] else f"{name}は付属しない。"
    if kind == "piece_total":
        if not isinstance(atom, int) or atom < 0:
            raise ValueError("piece_total value is invalid")
        return f"枚数は{atom}枚。"
    if kind == "named_size":
        if not isinstance(atom, str) or not atom:
            raise ValueError("named_size value is invalid")
        return f"サイズは{atom}。"
    if kind == "fabric":
        if not isinstance(atom, str) or not atom:
            raise ValueError("fabric value is invalid")
        return f"生地は{atom}。"
    if kind == "variant":
        if not isinstance(value, str) or not value:
            raise ValueError("variant has no source selected_value_raw")
        return f"タイプは{value}。"
    raise ValueError(f"unsupported condition type: {kind!r}")


def _sha(path: Path) -> str:
    return v1.sha256_file(path)


def _read_jsonl(path: Path) -> list[dict]:
    return v1.read_jsonl(path)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {OUTPUT}")
    if CONFIDENCE_THRESHOLD != 0.90 or v1.CONFIDENCE_THRESHOLD != CONFIDENCE_THRESHOLD:
        raise RuntimeError("confidence threshold must remain frozen at 0.90")
    previous_manifest_path = V1_DIR / "frozen_manifest.json"
    previous_predictions_path = V1_DIR / "fp32_predictions.jsonl"
    previous_summary_path = V1_DIR / "summary.json"
    for required in (previous_manifest_path, previous_predictions_path, previous_summary_path, DEFAULT_INPUT):
        if not required.is_file():
            raise FileNotFoundError(required)

    previous_manifest = json.loads(previous_manifest_path.read_text(encoding="utf-8"))
    previous_summary = json.loads(previous_summary_path.read_text(encoding="utf-8"))
    input_sha = _sha(DEFAULT_INPUT)
    if input_sha != previous_manifest["input_sha256"]:
        raise RuntimeError("input SHA differs from v1; refusing to compare runs")
    if previous_manifest["revision"] != v1.REVISION or previous_manifest["model_id"] != v1.MODEL_ID:
        raise RuntimeError("v1 pinned model identity differs from the current runner")
    if previous_manifest["thresholds_frozen_before_inference"]["confidence_threshold"] != CONFIDENCE_THRESHOLD:
        raise RuntimeError("v1 threshold differs from required 0.90")
    if previous_summary.get("fp32", {}).get("inferred_count") != 97:
        raise RuntimeError("v1 FP32 run did not infer all 97 pairs")

    # Verify every downloaded file against the previous immutable snapshot.
    model_files = sorted(p for p in MODEL_DIR.rglob("*") if p.is_file() and ".cache" not in p.parts)
    current_model_sha = {p.relative_to(MODEL_DIR).as_posix(): _sha(p) for p in model_files}
    if current_model_sha != previous_manifest["snapshot_sha256"]:
        raise RuntimeError("model/tokenizer snapshot differs from the frozen v1 run")

    packets = _read_jsonl(DEFAULT_INPUT)
    if len(packets) != 27 or sum(len(row.get("evidence") or []) for row in packets) != 97:
        raise RuntimeError("expected the same 27 conditions / 97 evidence quotes")
    pairs = v1.collect_pairs(packets)
    for pair in pairs:
        pair["hypothesis"] = plain_hypothesis(pair["condition"])
    if len(pairs) != 97:
        raise RuntimeError("expected 97 paired claims")

    previous_predictions = _read_jsonl(previous_predictions_path)
    if len(previous_predictions) != 97:
        raise RuntimeError("v1 prediction file must contain all 97 pairs")

    import platform
    import importlib.metadata as metadata
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    torch.set_num_threads(min(4, torch.get_num_threads()))
    torch.use_deterministic_algorithms(True, warn_only=True)
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "run": "short Japanese claim ablation; FP32 CPU only",
        "model_id": v1.MODEL_ID,
        "revision": v1.REVISION,
        "model_snapshot_sha256": current_model_sha,
        "model_card_sha256": previous_manifest["card_sha256"],
        "model_config_sha256": previous_manifest["config_sha256"],
        "tokenizer_config_sha256": previous_manifest["tokenizer_config_sha256"],
        "label_mapping_provenance": previous_manifest["label_mapping_provenance"],
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "max_input_tokens": v1.MAX_INPUT_TOKENS,
        "device": "cpu",
        "precision": "float32",
        "quantization": None,
        "input_path": str(DEFAULT_INPUT),
        "input_sha256": input_sha,
        "input_pair_count": len(pairs),
        "condition_count": len(packets),
        "evidence_binding_sha256": v1.sha256_bytes(json.dumps(
            [x["evidence_binding"] for x in pairs], ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode("utf-8")),
        "previous_v1_manifest_sha256": _sha(previous_manifest_path),
        "previous_v1_predictions_sha256": _sha(previous_predictions_path),
        "script_path": str(Path(__file__).resolve()),
        "script_sha256": _sha(Path(__file__).resolve()),
        "v1_runner_path": str(HERE / "trial_nonllm_nli_v1.py"),
        "v1_runner_sha256": _sha(HERE / "trial_nonllm_nli_v1.py"),
        "versions": {
            "python": platform.python_version(),
            "torch": metadata.version("torch"),
            "transformers": metadata.version("transformers"),
            "tokenizers": metadata.version("tokenizers"),
            "fugashi": metadata.version("fugashi"),
            "unidic-lite": metadata.version("unidic-lite"),
        },
        "claim_templates": {
            "color": "色は{condition.atom_value}。",
            "dimension_generic": "サイズは{condition.selected_value_raw}。",
            "dimension_labeled": "only labelled dimensions are stated, using condition labels and values",
            "component_presence": "レースカーテンが付属する。 / レースカーテンは付属しない。",
            "piece_total": "枚数は{condition.atom_value}枚。",
            "named_size": "サイズは{condition.atom_value}。",
            "fabric": "生地は{condition.atom_value}。",
            "variant": "タイプは{condition.selected_value_raw}。",
        },
        "claim_source": "Every claim is an input-derived template over the selected condition in the frozen packet, not synthetic sample data.",
        "ablation": "Remove the selected-product subject and avoid repeating long axis wording where the atom gives the semantic value. Evidence quote, source bindings, condition, and offsets remain unchanged.",
        "interpretation": "NLI relation proposals only; a partial supported condition is not SKU acceptance.",
    }
    if manifest["script_sha256"] == "":
        raise RuntimeError("script hash unavailable")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir()
    (OUTPUT / "frozen_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR), local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        str(MODEL_DIR), local_files_only=True, torch_dtype=torch.float32)
    model.to("cpu").eval()
    if model.config.num_labels != 3:
        raise RuntimeError("unexpected number of NLI labels")
    predictions, excluded, timing = v1.run_model("fp32_cpu_plain_claim_v2", model, tokenizer, pairs)

    # Preserve one-to-one identity and quote binding before making transitions.
    key = lambda row: (row["packet_id"], row["evidence_index"])
    new_by_key = {key(row): row for row in predictions}
    old_by_key = {key(row): row for row in previous_predictions}
    if set(new_by_key) != set(old_by_key) or len(predictions) + len(excluded) != 97:
        raise RuntimeError("v1/v2 pair identities differ")
    for pair_key in new_by_key:
        if new_by_key[pair_key]["evidence_binding"] != old_by_key[pair_key]["evidence_binding"]:
            raise RuntimeError("v2 changed an original evidence binding")

    transitions = Counter(
        (old_by_key[pair_key]["proposal"], new_by_key[pair_key]["proposal"])
        for pair_key in sorted(old_by_key)
    )
    label_transitions = Counter(
        (old_by_key[pair_key]["predicted_label"], new_by_key[pair_key]["predicted_label"])
        for pair_key in sorted(old_by_key)
    )
    _write_jsonl(OUTPUT / "fp32_plain_predictions.jsonl", predictions)
    _write_jsonl(OUTPUT / "fp32_plain_excluded.jsonl", excluded)
    transition_rows = [
        {"v1_fp32_proposal": old, "v2_plain_proposal": new, "count": count}
        for (old, new), count in sorted(transitions.items())
    ]
    label_transition_rows = [
        {"v1_fp32_label": old, "v2_plain_label": new, "count": count}
        for (old, new), count in sorted(label_transitions.items())
    ]
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "same_input_sha256_as_v1": input_sha,
        "pair_count": 97,
        "inferred_count": len(predictions),
        "excluded_count_unknown": len(excluded),
        "threshold": CONFIDENCE_THRESHOLD,
        "v1_fp32": previous_summary["fp32"],
        "v2_fp32_plain": timing,
        "proposal_transition_counts": transition_rows,
        "predicted_label_transition_counts": label_transition_rows,
        "plain_claims": sorted({row["hypothesis"] for row in predictions}),
        "interpretation": "Shorter claims change only the hypothesis wording. These relation proposals do not establish SKU correctness; partial support never means the SKU is accepted. The downstream gate still needs every required condition verified.",
    }
    (OUTPUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUTPUT / "summary.txt").write_text(
        "Japanese NLI short-claim ablation — FP32 CPU only\n"
        f"Model: {v1.MODEL_ID}@{v1.REVISION}\nInput SHA matches v1: {input_sha}\n"
        "Pairs: 97; confidence threshold: 0.90; maximum input length: 512; truncation: disabled\n"
        f"V1 FP32 proposals: {previous_summary['fp32']['proposal_counts']}\n"
        f"V2 plain-claim proposals: {timing['proposal_counts']}; inferred {timing['inferred_count']}/97; excluded {len(excluded)}\n"
        f"V2 inference time: {timing['total_inference_seconds']:.2f}s\n"
        "Proposal transitions (v1 → v2):\n"
        + "".join(f"  {row['v1_fp32_proposal']} → {row['v2_plain_proposal']}: {row['count']}\n" for row in transition_rows)
        + "Claim templates are generated from each frozen packet condition, while every quote and source binding is held constant. This is a relation ablation, not an SKU accuracy estimate. Partial support does not accept a SKU.\n",
        encoding="utf-8")


if __name__ == "__main__":
    main()
