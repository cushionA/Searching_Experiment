#!/usr/bin/env python3
"""Score every whole raw composition option with the pinned CPU NLI model.

This is a score-only diagnostic. It reads only the frozen classification
artifacts named by its CLI, performs no reference/gate/label evaluation, and
leaves aggregation to a later evaluator after the existing JEV run completes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import composition_evidence_v1 as CONTENT
import evaluate_mixed_gpu_sku_v1 as E
import trial_full_scoped_condition_choices_v1 as FULL
import trial_generic_model_nli_v1 as NLI

DEFAULT_INPUT = ROOT / ".lab-output/sku-composition-evidence-20261013-v1"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-composition-nli-20261013-v1"
THREADS, BATCH_SIZE = 4, 16
EXPECTED_NLI_REVISION = "21df45469d02809dc430e3ac5f5d6f67b63aabf3"
INPUT_NAMES = (
    "model-requests.jsonl", "classification-units.jsonl", "source-blocks.jsonl",
    "classification-input-freeze.json", "execution-freeze.json",
)
CODE_NAMES = (
    Path(__file__).name,
    "composition_evidence_v1.py",
    "evaluate_mixed_gpu_sku_v1.py",
    "evaluate_scoped_html_trial_v1.py",
    "integrate_claude_description_v1.py",
    "import_claude_alignment_handoff_v2.py",
    "evaluate_product_condition_share_v1.py",
    "trial_product_condition_models_v1.py",
    "product_condition_sharing_v1.py",
    "trial_scoped_html_evidence_v1.py",
    "trial_full_scoped_condition_choices_v1.py",
    "trial_scoped_condition_gliner_v1.py",
    "trial_generic_gliner_choice_v1.py",
    "trial_scoped_condition_nli_choice_v1.py",
    "trial_scoped_condition_nli_v1.py",
    "trial_generic_model_nli_v1.py",
    "trial_cpu_requirement_relations_v1.py",
    "trial_generic_model_jev_v1.py",
)
POLICY_DECLARATIONS = {
    "eligible_scope": "baseline-unknown only; shared and paired units",
    "nli_variant": "each original raw option is a support candidate only when raw support probability is strictly greater than both conflict and unknown; one unique supported option per block; require the same option on at least two of top-three blocks",
    "joint_variant": "on the same two blocks, JEV and raw NLI must each uniquely select the same original raw option",
    "unknown_policy": "ties, zero or multiple supported options, missing/error/overlong scores remain unknown",
    "threshold_policy": "no threshold fitting; raw three-class probabilities are preserved",
    "evaluation_policy": "declared before NLI inference; aggregation and evaluation are deferred to a separate evaluator",
}


def sha(path: Path) -> str:
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object: " + str(path))
    return value


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("expected JSON object row: " + str(path))
                rows.append(row)
    return rows


def write_json(path: Path, value: Any) -> None:
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def write_rows(path: Path, rows) -> None:
    with Path(path).open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(FULL.canonical(row) + "\n")


def _snapshot_entry(snapshot: dict, path: Path) -> dict | None:
    expected = Path(path).resolve()
    return next((item for item in snapshot.values()
                 if isinstance(item, dict) and item.get("path")
                 and Path(item["path"]).resolve() == expected), None)


def verify_input_freezes(input_dir: Path, paths: dict[str, Path],
                         classification_freeze: dict, execution_freeze: dict) -> None:
    if classification_freeze.get("labels_read") is not False:
        raise ValueError("classification input freeze must state labels_read=false")
    request_entry = _snapshot_entry(classification_freeze.get("input_snapshot", {}), paths["model-requests.jsonl"])
    if not isinstance(request_entry, dict) or request_entry.get("sha256") != sha(paths["model-requests.jsonl"]):
        raise ValueError("classification request snapshot SHA differs")
    if classification_freeze.get("request_count") != len(read_rows(paths["model-requests.jsonl"])):
        raise ValueError("classification request count differs from its freeze")

    if (execution_freeze.get("schema_version") != "composition-evidence-execution-v1"
            or execution_freeze.get("labels_read") is not False
            or execution_freeze.get("independent_test") is not False
            or execution_freeze.get("content_version") != CONTENT.CONTENT_VERSION):
        raise ValueError("unsupported composition execution freeze")
    source_snapshot = execution_freeze.get("source_snapshot")
    if not isinstance(source_snapshot, dict):
        raise ValueError("execution freeze lacks source snapshot")
    for filename in ("classification-units.jsonl", "source-blocks.jsonl"):
        entry = _snapshot_entry(source_snapshot, paths[filename])
        if not isinstance(entry, dict) or entry.get("sha256") != sha(paths[filename]):
            raise ValueError("execution source snapshot SHA differs: " + filename)
    frozen_code_dir = input_dir / "code"
    for name in CODE_NAMES:
        frozen_path = frozen_code_dir / name
        if frozen_path.is_file():
            entry = _snapshot_entry(source_snapshot, frozen_path)
            if not isinstance(entry, dict) or entry.get("sha256") != sha(frozen_path):
                raise ValueError("execution source code snapshot SHA differs: " + name)
            if sha(HERE / name) != sha(frozen_path):
                raise ValueError("current source code differs from composition input snapshot: " + name)


def _source_choice_hash(choices: list[dict[str, Any]]) -> str:
    return FULL.digest(choices)


def pairs_for(units: list[dict[str, Any]], requests: list[dict[str, Any]],
              blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build one title-free NLI pair per original option, preserving provenance."""
    unit_by_id = {unit.get("group_id"): unit for unit in units}
    block_by_id = {block.get("block_id"): block for block in blocks}
    if None in unit_by_id or len(unit_by_id) != len(units):
        raise ValueError("classification unit IDs must be present and unique")
    if None in block_by_id or len(block_by_id) != len(blocks):
        raise ValueError("source block IDs must be present and unique")

    pairs, seen_ids = [], set()
    for request in requests:
        group_id = request.get("group_id")
        unit = unit_by_id.get(group_id)
        if unit is None:
            raise ValueError("model request has no classification unit")
        block = block_by_id.get(request.get("block_id"))
        if block is None:
            raise ValueError("model request has no source block")
        options = unit.get("option_values")
        if (request.get("group_sha256") != unit.get("group_sha256")
                or request.get("unit_kind") != unit.get("kind")
                or request.get("all_original_option_values") != options
                or request.get("block_rank", 0) not in (1, 2, 3)
                or block.get("target_document_id") != unit.get("target_document_id")):
            raise ValueError("model request and classification/source binding differs")
        if not isinstance(options, list) or not options or any(not isinstance(v, str) or v == "" for v in options):
            raise ValueError("classification unit requires nonempty raw options")
        unknown = request.get("unknown_control_label")
        choices = request.get("choices")
        if (not isinstance(unknown, str) or not unknown or unknown in options
                or not isinstance(choices, list) or len(choices) != len(options) + 1
                or [choice.get("label") for choice in choices] != [*options, unknown]):
            raise ValueError("full source choices must preserve every raw option and one unknown")
        if request.get("question") != (
                "購入対象の仕様・セット内容について、〈" + unit["axis_name"] + "〉はどの選択肢に該当しますか。"):
            raise ValueError("composition question differs from the frozen axis")
        state = request.get("state")
        block_passage = block.get("model_passage")
        if not isinstance(state, str) or not isinstance(block_passage, str):
            raise ValueError("model state must be exact title-free composition passage")
        if unit.get("kind") == "paired":
            selected = unit.get("target_selected_axes_text")
            if not isinstance(selected, str) or not selected:
                raise ValueError("paired unit requires its original selected axes")
            state_prefix = "対象SKUの選択軸：\n" + selected + "\n"
        else:
            selected = None
            state_prefix = ""
        expected_state = state_prefix + "この商品の記載された仕様・セット内容：\n" + block_passage
        if state != expected_state:
            raise ValueError("model state differs from the frozen title-free composition rendering")
        source_refs = request.get("quote_refs")
        if source_refs != block.get("quote_refs") or not isinstance(source_refs, list) or not source_refs:
            raise ValueError("model request quote refs must preserve the complete source block refs")
        refs = json.loads(json.dumps(source_refs, ensure_ascii=False))

        if unit.get("kind") == "paired":
            selected_ref = {"document_id": unit["target_document_id"], "field": "selected_axes",
                            "start": 0, "end": len(selected), "quote": selected}
            quote_request = {"target_document_id": unit["target_document_id"],
                             "target_selected_axes_text": selected}
            if not E.GATE.verify_quote(selected_ref, quote_request, {}):
                raise ValueError("paired selected axes do not verify as an exact literal quote")
            refs.append(selected_ref)
        elif unit.get("kind") == "shared":
            if any(ref.get("field") == "selected_axes" for ref in refs if isinstance(ref, dict)):
                raise ValueError("shared unit must not contain selected axes")
        else:
            raise ValueError("unsupported classification unit kind")

        choice_hash = _source_choice_hash(choices)
        for option_index, value in enumerate(options):
            pair_id = str(request["id"]) + ":nli-option:" + str(option_index)
            if pair_id in seen_ids:
                raise ValueError("duplicate composition NLI pair ID")
            seen_ids.add(pair_id)
            pairs.append({
                "id": pair_id,
                "premise": state,
                "hypothesis": "購入対象の〈" + unit["axis_name"] + "〉は〈" + value + "〉である。",
                "group_id": group_id,
                "group_sha256": unit["group_sha256"],
                "unit_sha256": unit["group_sha256"],
                "unit_kind": unit["kind"],
                "block_id": block["block_id"],
                "block_rank": request["block_rank"],
                "target_document_id": unit["target_document_id"],
                "option_index": option_index,
                "option_value": value,
                "all_original_option_values": list(options),
                "unknown_control_label": unknown,
                "source_choice_sha256": choice_hash,
                "quote_refs": refs,
                "scope_status": "unproved",
                "production_adoption_allowed": False,
            })
    return pairs


def model_pin() -> dict[str, Any]:
    if NLI.MODEL_REVISION != EXPECTED_NLI_REVISION:
        raise ValueError("NLI source revision differs from the frozen FP32 pin")
    return {"model_id": NLI.MODEL_ID, "revision": NLI.MODEL_REVISION,
            "parameter_count": NLI.FP32_PARAMETER_COUNT, "precision": "fp32", "device": "cpu",
            "files_sha256": NLI.NLI_FILE_SHA256}


def _input_paths(input_dir: Path) -> dict[str, Path]:
    return {name: input_dir / name for name in INPUT_NAMES}


def run(input_dir: Path = DEFAULT_INPUT, output_dir: Path = DEFAULT_OUTPUT,
        model_dir: Path = NLI.DEFAULT_MODEL, threads: int = THREADS,
        batch_size: int = BATCH_SIZE, *, nli_runner=None) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if threads != THREADS or batch_size != BATCH_SIZE:
        raise ValueError("frozen CPU settings are four threads and batch size 16")
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    paths = _input_paths(input_dir)
    before = {name: sha(path) for name, path in paths.items()}
    classification_freeze = read_json(paths["classification-input-freeze.json"])
    execution_freeze = read_json(paths["execution-freeze.json"])
    requests = read_rows(paths["model-requests.jsonl"])
    units = read_rows(paths["classification-units.jsonl"])
    blocks = read_rows(paths["source-blocks.jsonl"])
    verify_input_freezes(input_dir, paths, classification_freeze, execution_freeze)
    pairs = pairs_for(units, requests, blocks)
    if len(pairs) != sum(len(request["all_original_option_values"]) for request in requests):
        raise ValueError("NLI pair coverage differs from raw-option coverage")

    output_dir.mkdir(parents=True, exist_ok=False)
    copied_inputs = {}
    for name, path in paths.items():
        destination_name = "source-" + name
        shutil.copyfile(path, output_dir / destination_name)
        copied_inputs[destination_name] = sha(output_dir / destination_name)
    code_dir = output_dir / "code"
    code_dir.mkdir()
    code_hashes = {}
    for name in CODE_NAMES:
        source = HERE / name
        if not source.is_file():
            raise FileNotFoundError(source)
        target = code_dir / name
        shutil.copyfile(source, target)
        code_hashes[name] = sha(target)
    write_rows(output_dir / "model-pairs.jsonl", pairs)

    unique_pairs = FULL.unique_inputs(pairs, lambda pair: NLI.pair_key(pair["premise"], pair["hypothesis"]))
    pinned_model = model_pin()
    frozen_nli = execution_freeze.get("model_identity", {}).get("nli", {})
    if any(frozen_nli.get(key) != pinned_model[key]
           for key in ("model_id", "revision", "parameter_count", "files_sha256")):
        raise ValueError("composition source and NLI model pins differ")
    freeze = {
        "schema_version": "composition-nli-score-only-v1",
        "labels_read": False,
        "references_read": False,
        "gates_read": False,
        "development_only": True,
        "independent_test": False,
        "production_adoption_allowed": False,
        "scope_status": "unproved",
        "content_version": CONTENT.CONTENT_VERSION,
        "input_sha256": before,
        "copied_input_sha256": copied_inputs,
        "classification_request_count": len(requests),
        "classification_unit_count": len(units),
        "source_block_count": len(blocks),
        "actual_option_pair_count": len(pairs),
        "actual_unique_pair_count": len(unique_pairs),
        "input_contract": {
            "title_free_state": True,
            "shared_selected_axes_in_model_input": False,
            "paired_selected_axes_in_model_input": True,
            "paired_selected_axes_quote_refs_added": True,
            "wanted_selected_value_injected": False,
            "all_original_options_scored_in_order": True,
            "unknown_control_retained_in_choice_hash_but_not_as_nli_hypothesis": True,
            "source_choice_sha256": "canonical SHA256 of the full ordered JEV choice list, including the unknown control",
            "truncation": "none; NLI inputs over 512 tokens remain unknown in raw records",
        },
        "predeclared_comparison_policies": POLICY_DECLARATIONS,
        "nli_threshold": NLI.THRESHOLD,
        "model_pin": pinned_model,
        "threads": threads,
        "batch_size": batch_size,
        "device": "cpu",
        "precision": "fp32",
        "code_sha256": code_hashes,
        "comparison_output_policy": "scores only; no group winners or evaluation are computed here",
    }
    write_json(output_dir / "freeze.json", freeze)

    started = time.perf_counter()
    call = nli_runner or NLI.run_pairs
    cache: dict[str, dict[str, Any]] = {}
    runtime: dict[str, Any] = {}
    pins = None
    with (FULL.reuse_nli_factories() if nli_runner is None and unique_pairs else _nullcontext()):
        unique_path = output_dir / "nli-unique-scores.jsonl"
        with unique_path.open("x", encoding="utf-8") as handle:
            for offset in range(0, len(unique_pairs), 128):
                chunk = unique_pairs[offset:offset + 128]
                result = FULL.score_nli_chunk(call, chunk, Path(model_dir), threads, batch_size)
                records = result["records"]
                if len(records) != len(chunk) or [row.get("id") for row in records] != [row["id"] for row in chunk]:
                    raise ValueError("NLI unique output identity differs")
                for record in records:
                    handle.write(FULL.canonical(record) + "\n")
                handle.flush()
                cache.update({record["cache_key"]: record for record in records})
                FULL._accumulate(runtime, result["runtime"])
                pins = result["pins"]
        full_result = call(pairs, model_dir=Path(model_dir), threads=threads,
                           batch_size=batch_size, cache=cache) if pairs else {"records": [], "pins": pins, "runtime": {}}

    records = full_result["records"]
    if len(records) != len(pairs) or [record.get("id") for record in records] != [pair["id"] for pair in pairs]:
        raise ValueError("full NLI records do not preserve exact option inputs")
    write_rows(output_dir / "classification-options.jsonl", records)
    for name, expected in before.items():
        if sha(paths[name]) != expected:
            raise ValueError("source input changed during NLI inference: " + name)
    for name, expected in code_hashes.items():
        if sha(HERE / name) != expected:
            raise ValueError("source code changed during NLI inference: " + name)
    for name, expected in copied_inputs.items():
        if sha(output_dir / name) != expected:
            raise ValueError("copied source input changed: " + name)
    if any(sha(code_dir / name) != expected for name, expected in code_hashes.items()):
        raise ValueError("copied source code changed during NLI inference")

    runtime = dict(runtime)
    FULL._accumulate(runtime, full_result.get("runtime", {}))
    pins = full_result.get("pins") or pins
    summary = {
        **freeze,
        "verified_model_pin": pins,
        "runtime": runtime,
        "inference_seconds": runtime.get("inference_seconds", 0.0),
        "record_count": len(records),
        "raw_relation_counts": dict(Counter(record.get("relation", "unknown") for record in records)),
        "overlong_count": sum(NLI.exceeds_token_limit(record["token_count_untruncated"])
                               for record in records if isinstance(record.get("token_count_untruncated"), int)),
        "input_and_code_sha256_verified_after_inference": True,
        "output_sha256": {path.name: sha(path) for path in output_dir.iterdir() if path.is_file()},
    }
    summary["total_seconds"] = time.perf_counter() - started
    write_json(output_dir / "summary.json", summary)
    return summary


class _nullcontext:
    def __enter__(self):
        return None

    def __exit__(self, *_args):
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model-dir", type=Path, default=NLI.DEFAULT_MODEL)
    parser.add_argument("--threads", type=int, default=THREADS)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    run(**vars(parser.parse_args()))
