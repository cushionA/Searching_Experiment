#!/usr/bin/env python3
"""CPU-only, label-free relation proposals for one requirement and one quote."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import time
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_NLI = ROOT / ".deps/sku-nli-model"
DEFAULT_GLINER = ROOT / ".deps/sku-gliner-model"
LABELS = ("support", "conflict", "unknown")
GLINER_CHOICES = (
    "requirement is supported by the evidence quote",
    "evidence quote contradicts the requirement",
    "evidence quote does not state the requirement",
)
GLINER_LABEL_TO_RELATION = dict(zip(GLINER_CHOICES, LABELS, strict=True))
COMPONENT_CHOICES = ("商品に含まれる", "商品に含まれない", "記載がない")
NLI_ORDER = ("entailment", "neutral", "contradiction")
NLI_TO_RELATION = {"entailment": "support", "contradiction": "conflict", "neutral": "unknown"}
THRESHOLD = 0.90
MAX_TOKENS = 512
MODEL_ID = "0x3/bert-base-japanese-v3_nli-jsnli-jnli-jsick"
MODEL_REVISION = "21df45469d02809dc430e3ac5f5d6f67b63aabf3"
NLI_PROVENANCE_MANIFEST_SHA256 = "09e5c8dfef1ce53178ff37ad68e22434428420c097e02ab474435eae75806310"
NLI_FILE_SHA256 = {
    "README.md": "b90f5d55b990871fade305646669ee3f3a28149eeab04286ad9455423942b1fa",
    "config.json": "005f4981bff45802904ee4511c7b6cc204cbaea349ea21029181bff3c16f4dd0",
    "model.safetensors": "5e0913cfefd5437ac45bd197728036e386b740b58676499d9698548378cde621",
    "special_tokens_map.json": "5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a",
    "tokenizer_config.json": "c1fc860b700646459228f05252ce5c080001d1a84337a031ee6166aeb618ff2b",
    "vocab.txt": "5e9a696b0191b833cfdf8eefada01f41f23ccbd7e7746946864260b1cdd0a784",
}
GLINER_ID = "fastino/GLiNER2.5-multi-Decide"
GLINER_REVISION = "e173bca1f0c4217d7a55b3b0c705d5ece8a0218d"


def exceeds_token_limit(token_count: int) -> bool:
    """Return whether a pair must be excluded without truncation."""
    return token_count > MAX_TOKENS


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_no}: expected object")
            rows.append(row)
    return rows


def validate_task(row: dict[str, Any]) -> None:
    for key in ("task_id", "case_id", "au_row_key"):
        if not isinstance(row.get(key), str) or not row[key]:
            raise ValueError(f"task missing {key}")
    req, evidence = row.get("requirement"), row.get("evidence")
    if not isinstance(req, dict) or not isinstance(evidence, dict):
        raise ValueError("task requires requirement and evidence objects")
    for key in ("type", "value", "component", "axis_label", "axis_value_quote"):
        if key not in req:
            raise ValueError(f"requirement missing {key}")
    for key in ("evidence_id", "quote", "span"):
        if key not in evidence:
            raise ValueError(f"evidence missing {key}")
    if not isinstance(evidence["span"], dict):
        raise ValueError("evidence span must be an object")
    if not isinstance(evidence["quote"], str) or not evidence["quote"]:
        raise ValueError("evidence quote must be non-empty text")
    source_scope = row.get("source_scope", evidence.get("source_scope"))
    if not isinstance(source_scope, dict) or not source_scope:
        raise ValueError("task requires verified source_scope metadata")
    if not isinstance(row.get("hypothesis"), str) or not row["hypothesis"].strip():
        raise ValueError("hypothesis must be non-empty templated Japanese")
    # Literal binding is required; do not normalize or reconstruct source text.
    span = evidence["span"]
    if span.get("quote") != evidence["quote"]:
        raise ValueError("evidence span quote does not exactly equal quote")


def relation_probabilities(logits: list[float]) -> tuple[dict[str, float], str, float]:
    if len(logits) != len(NLI_ORDER) or any(not math.isfinite(float(value)) for value in logits):
        raise ValueError("NLI logits must contain exactly three finite values")
    peak = max(logits)
    exp = [math.exp(x - peak) for x in logits]
    total = sum(exp)
    probs = [x / total for x in exp]
    source = {name: float(prob) for name, prob in zip(NLI_ORDER, probs, strict=True)}
    mapped = {"support": source["entailment"], "conflict": source["contradiction"], "unknown": source["neutral"]}
    winner = max(mapped, key=mapped.get)
    return mapped, winner, mapped[winner]


def normalize_gliner_probabilities(probabilities: dict[str, Any]) -> tuple[dict[str, float], str, float]:
    if set(probabilities) != set(GLINER_CHOICES):
        raise ValueError("GLiNER probability labels differ from the frozen three-class schema")
    values = {GLINER_LABEL_TO_RELATION[label]: float(probabilities[label]) for label in GLINER_CHOICES}
    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in values.values()):
        raise ValueError("GLiNER probabilities must be finite values in [0, 1]")
    total = sum(values.values())
    if not math.isclose(total, 1.0, rel_tol=1e-5, abs_tol=1e-5):
        raise ValueError(f"GLiNER exclusive probabilities must sum to 1 (got {total})")
    winner = max(values, key=values.get)
    return values, winner, values[winner]


def component_noun_from_hypothesis(hypothesis: str) -> str:
    """Extract the Japanese component noun from the generated hypothesis template."""
    pattern = r"この商品には(?P<noun>[^。]+?)が付いて(?:いる|いない)。"
    match = re.fullmatch(pattern, hypothesis)
    if match:
        noun = match.group("noun").strip()
        if noun and len(noun) <= 40:
            return noun
    raise ValueError(f"cannot extract a Japanese component noun from hypothesis: {hypothesis!r}")


def component_schema_instruction(hypothesis: str, noun: str) -> str:
    """Stable instruction template used by the frozen component schema."""
    return f"引用文だけを根拠に、仮説「{hypothesis}」について「{noun}」の状態を選んでください。"


def component_relation_map(requirement_value: Any) -> dict[str, str]:
    if not isinstance(requirement_value, bool):
        raise ValueError("component presence requirement value must be boolean")
    if requirement_value:
        return {COMPONENT_CHOICES[0]: "support", COMPONENT_CHOICES[1]: "conflict", COMPONENT_CHOICES[2]: "unknown"}
    return {COMPONENT_CHOICES[0]: "conflict", COMPONENT_CHOICES[1]: "support", COMPONENT_CHOICES[2]: "unknown"}


def normalize_component_probabilities(probabilities: dict[str, Any], requirement_value: bool):
    mapping = component_relation_map(requirement_value)
    if set(probabilities) != set(mapping):
        raise ValueError("GLiNER component probabilities differ from the frozen three-class schema")
    values = {mapping[label]: float(probabilities[label]) for label in COMPONENT_CHOICES}
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values.values()):
        raise ValueError("GLiNER component probabilities must be finite values in [0, 1]")
    total = sum(values.values())
    if not math.isclose(total, 1.0, rel_tol=1e-5, abs_tol=1e-5):
        raise ValueError(f"GLiNER component probabilities must sum to 1 (got {total})")
    winner = max(values, key=values.get)
    return values, winner, values[winner]


def make_record(task: dict[str, Any], probabilities: dict[str, float] | None,
                predicted: str, confidence: float | None, latency: float,
                token_count: int | None, backend: str, note: str | None = None) -> dict[str, Any]:
    proposal = predicted if confidence is not None and confidence >= THRESHOLD else "unknown"
    if predicted == "unknown":
        proposal = "unknown"
    return {
        "task_id": task["task_id"], "case_id": task["case_id"], "au_row_key": task["au_row_key"],
        "requirement": task["requirement"], "hypothesis": task["hypothesis"],
        "evidence_binding": {"evidence_id": task["evidence"]["evidence_id"],
                             "quote": task["evidence"]["quote"], "span": task["evidence"]["span"],
                             "source_scope": task.get("source_scope", task["evidence"].get("source_scope"))},
        "backend": backend, "probabilities": probabilities,
        "predicted_class": predicted, "predicted_confidence": confidence,
        "confidence_threshold": THRESHOLD, "proposal": proposal,
        "token_count_untruncated": token_count, "latency_seconds": latency,
        "note": note,
    }


def run_nli(tasks: list[dict[str, Any]], model_dir: Path, threads: int, batch_size: int,
            precision: str, on_ready: Any) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    model_files = tuple(NLI_FILE_SHA256)
    for name in model_files:
        if not (model_dir / name).is_file():
            raise FileNotFoundError(f"pinned NLI file missing: {model_dir / name}")
        if sha256(model_dir / name) != NLI_FILE_SHA256[name]:
            raise RuntimeError(f"pinned NLI file SHA256 mismatch: {name}")
    card = (model_dir / "README.md").read_text(encoding="utf-8")
    compact = "".join(card.split())
    if '{0:"entailment",1:"neutral",2:"contradiction}' not in compact:
        raise RuntimeError("NLI model card label order is not the pinned mapping")
    config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    if config.get("id2label") != {"0": "LABEL_0", "1": "LABEL_1", "2": "LABEL_2"}:
        raise RuntimeError("NLI model config labels changed")
    torch.set_num_threads(threads)
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True)
    started = time.perf_counter()
    model = AutoModelForSequenceClassification.from_pretrained(str(model_dir), local_files_only=True,
                                                                torch_dtype=torch.float32).to("cpu").eval()
    if model.config.num_labels != 3:
        raise RuntimeError("expected three NLI logits")
    if any(parameter.device.type != "cpu" for parameter in model.parameters()):
        raise RuntimeError("NLI model parameters are not entirely on CPU")
    parameter_count = sum(parameter.numel() for parameter in model.parameters())
    if precision == "int8":
        model = torch.ao.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8).eval()
    load_seconds = time.perf_counter() - started
    pin = {"model_id": MODEL_ID, "revision": MODEL_REVISION, "parameter_count": parameter_count,
           "files": {n: {"sha256": sha256(model_dir / n), "size_bytes": (model_dir / n).stat().st_size} for n in model_files},
           "provenance_manifest_sha256": NLI_PROVENANCE_MANIFEST_SHA256,
           "label_order_provenance": "README declares index 0 entailment, 1 neutral, 2 contradiction; config uses generic LABEL_0..2",
           "config_id2label": config["id2label"]}
    runtime_info = {"model_load_seconds": load_seconds, "precision": precision}
    on_ready(pin, runtime_info)
    prepared: list[tuple[dict[str, Any], int]] = []
    records: list[dict[str, Any]] = []
    long_count = 0
    for task in tasks:
        enc = tokenizer(task["evidence"]["quote"], task["hypothesis"], truncation=False, add_special_tokens=True)
        count = len(enc["input_ids"])
        if exceeds_token_limit(count):
            long_count += 1
            records.append(make_record(task, None, "unknown", None, 0.0, count, "nli",
                                       f"input exceeds {MAX_TOKENS} tokens; no truncation; unknown"))
        else:
            prepared.append((task, count))
    infer_seconds = 0.0
    inference_errors = 0
    for offset in range(0, len(prepared), batch_size):
        batch = prepared[offset:offset + batch_size]
        features = tokenizer([x[0]["evidence"]["quote"] for x in batch],
                             [x[0]["hypothesis"] for x in batch], padding=True,
                             truncation=False, return_tensors="pt")
        started = time.perf_counter()
        try:
            with torch.inference_mode():
                logits_rows = model(**{k: v.cpu() for k, v in features.items()}).logits.to(torch.float64).tolist()
            elapsed = time.perf_counter() - started
            infer_seconds += elapsed
            each = elapsed / len(batch)
            for (task, count), logits in zip(batch, logits_rows, strict=True):
                probs, pred, confidence = relation_probabilities(logits)
                records.append(make_record(task, probs, pred, confidence, each, count, "nli"))
        except Exception as exc:
            infer_seconds += time.perf_counter() - started
            inference_errors += len(batch)
            for task, count in batch:
                record = make_record(task, None, "unknown", None, 0.0, count, "nli",
                                     f"inference error: {type(exc).__name__}: {exc}")
                record["error_type"] = type(exc).__name__
                records.append(record)
    by_id = {r["task_id"]: r for r in records}
    records = [by_id[t["task_id"]] for t in tasks]
    return records, pin, {"model_load_seconds": load_seconds, "inference_seconds": infer_seconds,
                          "long_input_unknown_count": long_count, "inference_error_count": inference_errors,
                          "precision": precision}


def run_gliner(tasks: list[dict[str, Any]], model_dir: Path, threads: int, batch_size: int,
               gliner_task: str, on_ready: Any):
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from gliner2 import AutoExtractor
    from gliner2.classification import Classifier, ClassificationConfig, ClassificationSchema
    if torch.cuda.is_available():
        raise RuntimeError("CUDA is available; refusing GLiNER execution outside the CPU-only setup")
    torch.set_num_threads(threads)
    source_manifest_path = model_dir / "source-manifest.json"
    if not source_manifest_path.is_file():
        raise FileNotFoundError(source_manifest_path)
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if source_manifest.get("repo") != GLINER_ID or source_manifest.get("revision") != GLINER_REVISION:
        raise RuntimeError("GLiNER source manifest repo/revision differs from the frozen model pin")
    files = source_manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise RuntimeError("GLiNER source manifest has no file pins")
    for name, expected in files.items():
        path = model_dir / name
        if not path.is_file() or path.stat().st_size != expected.get("size_bytes") or sha256(path) != expected.get("sha256"):
            raise RuntimeError(f"GLiNER snapshot pin mismatch: {name}")
    model_load_started = time.perf_counter()
    model = AutoExtractor.from_pretrained(str(model_dir), local_files_only=True)
    model.eval()
    if any(parameter.device.type != "cpu" for parameter in model.parameters()):
        raise RuntimeError("GLiNER model parameters are not entirely on CPU")
    model_load_seconds = time.perf_counter() - model_load_started
    classifier = Classifier(model)
    tokenizer = model.processor.tokenizer
    schema_manifest: dict[str, Any]
    task_schemas: list[Any]
    task_names: list[str]
    relation_maps: list[dict[str, str]]
    inference_groups: list[list[int]]
    if gliner_task == "relation":
        texts = [f"根拠引用：{t['evidence']['quote']}\n仮説：{t['hypothesis']}" for t in tasks]
        schema_instruction = "Classify whether the Japanese requirement is supported, contradicted, or unstated by the literal evidence quote."
        shared_schema = ClassificationSchema().single("relation", list(GLINER_CHOICES), instruction=schema_instruction)
        task_schemas = [shared_schema] * len(tasks)
        task_names = ["relation"] * len(tasks)
        relation_maps = [GLINER_LABEL_TO_RELATION] * len(tasks)
        inference_groups = [list(range(len(tasks)))]
        token_counts = [len(tokenizer(" ".join((text, "relation", schema_instruction, *GLINER_CHOICES)),
                                    add_special_tokens=True)["input_ids"]) + 16 for text in texts]
        schema_manifest = {"mode": "relation", "task_name": "relation", "instruction": schema_instruction,
                           "candidate_labels": list(GLINER_CHOICES), "candidate_label_mapping": GLINER_LABEL_TO_RELATION,
                           "model_text_template": "quote plus hypothesis"}
    elif gliner_task == "component":
        texts = [task["evidence"]["quote"] for task in tasks]
        grouped: dict[tuple[str, bool], list[int]] = {}
        per_task: list[dict[str, Any]] = []
        for index, task in enumerate(tasks):
            noun = component_noun_from_hypothesis(task["hypothesis"])
            expected = task["requirement"].get("value")
            relation_map = component_relation_map(expected)
            name = f"{noun}の有無"
            instruction = component_schema_instruction(task["hypothesis"], noun)
            per_task.append({"component_noun": noun, "expected_present": expected, "task_name": name,
                             "hypothesis": task["hypothesis"], "instruction": instruction,
                             "candidate_labels": list(COMPONENT_CHOICES), "candidate_label_mapping": relation_map})
            grouped.setdefault((noun, expected), []).append(index)
        task_schemas = [None] * len(tasks)
        task_names = [x["task_name"] for x in per_task]
        relation_maps = [x["candidate_label_mapping"] for x in per_task]
        for (noun, expected), indices in grouped.items():
            instructions = {per_task[index]["instruction"] for index in indices}
            if len(instructions) != 1:
                raise ValueError(f"tasks grouped by component/value have different hypotheses: {noun!r}")
            instruction = next(iter(instructions))
            schema = ClassificationSchema().single(f"{noun}の有無", list(COMPONENT_CHOICES), instruction=instruction)
            for index in indices:
                task_schemas[index] = schema
        inference_groups = list(grouped.values())
        token_counts = [len(tokenizer(" ".join((text, per_task[index]["task_name"],
                                                per_task[index]["instruction"], *COMPONENT_CHOICES)),
                                    add_special_tokens=True)["input_ids"]) + 16
                        for index, text in enumerate(texts)]
        schema_manifest = {"mode": "component", "candidate_labels": list(COMPONENT_CHOICES),
                           "per_task_schema": [{"task_id": task["task_id"], **spec} for task, spec in zip(tasks, per_task, strict=True)],
                           "model_text_template": "literal quote only; hypothesis appears only in schema instruction"}
    else:
        raise ValueError(f"unsupported GLiNER task mode: {gliner_task}")
    prepared = [i for i, count in enumerate(token_counts) if not exceeds_token_limit(count)]
    records: list[dict[str, Any] | None] = [None] * len(tasks)
    for i, count in enumerate(token_counts):
        if exceeds_token_limit(count):
            records[i] = make_record(tasks[i], None, "unknown", None, 0.0, count, "gliner-decide",
                                     f"estimated complete text/schema input exceeds {MAX_TOKENS} tokens; no truncation; unknown")
    pin = {"model_id": GLINER_ID, "revision": GLINER_REVISION,
           "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
           "source_manifest_sha256": sha256(source_manifest_path),
           "files": {name: {"sha256": value["sha256"], "size_bytes": value["size_bytes"]}
                     for name, value in files.items()},
           "task_mode": gliner_task}
    on_ready(pin, {"model_load_seconds": model_load_seconds, "precision": "model_default_cpu",
                   "task_schema": schema_manifest,
                   "estimated_input_token_count_includes_schema_and_16_token_margin": True})
    config = ClassificationConfig(batch_size=batch_size, max_len=MAX_TOKENS)
    elapsed, error_count = 0.0, 0
    for group in inference_groups:
        active_group = [index for index in group if index in prepared]
        for start_index in range(0, len(active_group), batch_size):
            indices = active_group[start_index:start_index + batch_size]
            start = time.perf_counter()
            try:
                outputs = classifier.batch_classify([texts[j] for j in indices], task_schemas[indices[0]], config=config)
                elapsed += time.perf_counter() - start
                for task_index, out in zip(indices, outputs, strict=True):
                    result = out[task_names[task_index]]
                    if gliner_task == "component":
                        probabilities, probability_winner, probability_confidence = normalize_component_probabilities(
                            dict(result.probabilities), tasks[task_index]["requirement"]["value"])
                    else:
                        probabilities, probability_winner, probability_confidence = normalize_gliner_probabilities(
                            dict(result.probabilities))
                    label = result.label
                    confidence = result.confidence
                    pred = relation_maps[task_index].get(label, "unknown")
                    if pred != probability_winner:
                        raise ValueError("GLiNER selected label differs from the maximum class probability")
                    if confidence is None or not math.isfinite(float(confidence)) or not math.isclose(
                            float(confidence), probability_confidence, rel_tol=1e-5, abs_tol=1e-5):
                        raise ValueError("GLiNER confidence is missing, non-finite, or differs from selected probability")
                    records[task_index] = make_record(tasks[task_index], probabilities, pred,
                        float(confidence), 0.0, token_counts[task_index], "gliner-decide")
            except Exception as exc:
                elapsed += time.perf_counter() - start
                error_count += len(indices)
                for task_index in indices:
                    rec = make_record(tasks[task_index], None, "unknown", None, 0.0, token_counts[task_index], "gliner-decide",
                                      f"inference error: {type(exc).__name__}: {exc}")
                    rec["error_type"] = type(exc).__name__
                    records[task_index] = rec
    return [r for r in records if r is not None], pin, {"model_load_seconds": model_load_seconds,
             "inference_seconds": elapsed,
             "precision": "model_default_cpu", "inference_error_count": error_count,
             "long_input_unknown_count": sum(exceeds_token_limit(x) for x in token_counts)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--backend", choices=("nli", "gliner-decide"), default="nli")
    parser.add_argument("--gliner-task", choices=("relation", "component"), default="relation",
                        help="GLiNER schema mode; component classifies presence using quote-only model text")
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--precision", choices=("fp32", "int8"), default="fp32")
    args = parser.parse_args()
    if args.threads < 1 or args.batch_size < 1:
        parser.error("--threads and --batch-size must be positive")
    if args.backend == "nli" and args.gliner_task != "relation":
        parser.error("--gliner-task component requires --backend gliner-decide")
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite output: {args.output}")
    tasks = read_jsonl(args.input)
    if not tasks:
        raise ValueError("input has no tasks")
    for task in tasks:
        validate_task(task)
    ids = [t["task_id"] for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task_id")
    model_dir = args.model_dir or (DEFAULT_NLI if args.backend == "nli" else DEFAULT_GLINER)
    started = time.perf_counter()

    def freeze_before_inference(pin: dict[str, Any], backend_runtime: dict[str, Any]) -> None:
        args.output.mkdir(parents=True, exist_ok=False)
        runner_path = Path(__file__).resolve()
        runner_bytes = runner_path.read_bytes()
        runner_digest = hashlib.sha256(runner_bytes).hexdigest()
        with (args.output / "runner.py").open("xb") as runner_snapshot:
            runner_snapshot.write(runner_bytes)
        freeze = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(), "backend": args.backend,
            "gliner_task": args.gliner_task if args.backend == "gliner-decide" else None,
            "model": pin, "input_path": str(args.input.resolve()), "input_sha256": sha256(args.input),
            "runner_path": str(runner_path), "runner_sha256": runner_digest,
            "runner_snapshot_path": str((args.output / "runner.py").resolve()),
            "runner_snapshot_sha256": sha256(args.output / "runner.py"),
            "task_count": len(tasks), "threshold_frozen_before_inference": THRESHOLD,
            "relation_classes": list(LABELS), "max_tokens": MAX_TOKENS,
            "runtime_versions": {"python": platform.python_version(), "torch": metadata.version("torch"),
                                 "transformers": metadata.version("transformers"),
                                 "tokenizers": metadata.version("tokenizers"),
                                 **({"gliner2": metadata.version("gliner2")} if args.backend == "gliner-decide" else {})},
            "device": "cpu", "gpu_used": False, "cpu_enforced": True,
            "parameters": {"threads": args.threads, "batch_size": args.batch_size,
                           "precision": args.precision if args.backend == "nli" else "model_default"},
            "source_scope_metadata_present": all(isinstance(t.get("source_scope", t["evidence"].get("source_scope")), dict) for t in tasks),
            "threshold_decision_policy": "support/conflict only when winning class confidence >= 0.90; otherwise unknown",
            "backend_metadata_before_inference": backend_runtime,
            "outputs_are_proposals_only": True,
        }
        with (args.output / "freeze.json").open("x", encoding="utf-8") as stream:
            json.dump(freeze, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")

    if args.backend == "nli":
        predictions, pin, runtime = run_nli(tasks, model_dir, args.threads, args.batch_size, args.precision, freeze_before_inference)
    else:
        predictions, pin, runtime = run_gliner(tasks, model_dir, args.threads, args.batch_size,
                                               args.gliner_task, freeze_before_inference)
    freeze_path = args.output / "freeze.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    input_sha_after = sha256(args.input)
    runner_sha_after = sha256(Path(__file__))
    if input_sha_after != freeze["input_sha256"]:
        raise RuntimeError("input file changed during inference; refusing to finalize artifacts")
    if runner_sha_after != freeze["runner_sha256"]:
        raise RuntimeError("runner source changed during inference; refusing to finalize artifacts")
    with (args.output / "predictions.jsonl").open("x", encoding="utf-8") as f:
        for row in predictions:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    counts = {k: sum(r["proposal"] == k for r in predictions) for k in LABELS}
    summary = {"task_count": len(tasks), "proposal_counts": counts,
               "error_count": int(runtime.get("inference_error_count", 0)),
               "low_confidence_or_unscored_count": sum(r["predicted_confidence"] is None or r["predicted_confidence"] < THRESHOLD for r in predictions),
               "elapsed_seconds": time.perf_counter() - started, **runtime,
               "confidence_threshold": THRESHOLD, "labels_used": False,
               "gliner_task": args.gliner_task if args.backend == "gliner-decide" else None,
               "decisions_emitted": False, "full_probabilities_available": True,
               "probability_distribution_task_count": sum(r["probabilities"] is not None for r in predictions),
               "input_sha256_after_inference": input_sha_after,
               "runner_sha256_after_inference": runner_sha_after}
    with (args.output / "summary.json").open("x", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, sort_keys=True); f.write("\n")
    artifacts = ("freeze.json", "predictions.jsonl", "summary.json", "runner.py")
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_sha256_verified_after_inference": input_sha_after,
        "runner_sha256_verified_after_inference": runner_sha_after,
        "device": "cpu", "gpu_used": False,
        "artifacts": {name: {"sha256": sha256(args.output / name),
                              "size_bytes": (args.output / name).stat().st_size} for name in artifacts},
    }
    with (args.output / "manifest.json").open("x", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True); f.write("\n")


if __name__ == "__main__":
    main()
