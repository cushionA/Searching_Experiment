#!/usr/bin/env python3
"""One-pass, label-blind CUDA NLI worker for frozen source contexts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import time

import kaggle_full_condition_choices_v1 as GPU

NLI = GPU.NLI
HERE = Path(__file__).resolve().parent
CODE_NAMES = tuple(dict.fromkeys((Path(__file__).name, *GPU.CODE_NAMES)))
VIEWS = ("title_row", "body")
HYPOTHESIS_TEMPLATE = "この商品の〈{axis_name}〉は〈{option}〉である。"
MAX_CHUNK_CHARS = 800
MAX_TOKENS = 512
LABELS = ("support", "conflict", "unknown")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_contexts(path: Path) -> list[dict]:
    contexts = []
    refs = set()
    with Path(path).open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"contexts.jsonl:{line_no}: expected object")
            if (not isinstance(row.get("context_ref"), int) or isinstance(row.get("context_ref"), bool)
                    or row["context_ref"] in refs):
                raise ValueError(f"contexts.jsonl:{line_no}: context_ref must be a unique integer")
            if not isinstance(row.get("axis_name"), str) or not isinstance(row.get("prefix"), str):
                raise ValueError(f"contexts.jsonl:{line_no}: axis_name and prefix must be strings")
            if not isinstance(row.get("body_text"), str):
                raise ValueError(f"contexts.jsonl:{line_no}: body_text must be a string")
            options = row.get("option_values")
            if (not isinstance(options, list) or not options
                    or any(not isinstance(v, str) or not v for v in options)
                    or len(set(options)) != len(options)):
                raise ValueError(f"contexts.jsonl:{line_no}: option_values must be nonempty unique original strings")
            source_sha = row.get("document_sha256")
            if (not isinstance(source_sha, str) or len(source_sha) != 64
                    or any(c not in "0123456789abcdef" for c in source_sha)):
                raise ValueError(f"contexts.jsonl:{line_no}: invalid document_sha256")
            refs.add(row["context_ref"])
            contexts.append(row)
    if not contexts:
        raise ValueError("contexts.jsonl must contain at least one context")
    return contexts


def _pair_token_count(tokenizer, premise: str, hypothesis: str) -> int:
    encoded = tokenizer(premise, hypothesis, truncation=False, padding=False, add_special_tokens=True)
    return len(encoded["input_ids"])


def _largest_fitting_chunk(tokenizer, prefix: str, body: str, start: int,
                           hypotheses: list[str], max_chars: int = MAX_CHUNK_CHARS) -> tuple[int, str | None]:
    """Binary-search a character window that fits every original option."""
    hi = min(max_chars, len(body) - start)
    if hi <= 0:
        return 0, None

    def fits(length: int) -> bool:
        text = prefix + "\n引用本文：\n" + body[start:start + length]
        return all(_pair_token_count(tokenizer, text, hypothesis) <= MAX_TOKENS
                   for hypothesis in hypotheses)

    # The metadata alone can exceed the model limit. Keep the failure explicit.
    if not fits(1):
        metadata = prefix + "\n引用本文：\n"
        if any(_pair_token_count(tokenizer, metadata, hypothesis) > MAX_TOKENS
               for hypothesis in hypotheses):
            return 0, "metadata_exceeds_512_tokens"
        return 0, "single_character_exceeds_512_tokens"
    lo = 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo, None


def prepare_context_pairs(context: dict, tokenizer) -> tuple[list[dict], list[dict]]:
    """Create exact title/row and body-view pairs without losing body characters.

    Returns (pairs, issues). This pure preparation seam accepts a fake tokenizer.
    """
    options = context["option_values"]
    hypotheses = [HYPOTHESIS_TEMPLATE.format(axis_name=context["axis_name"], option=value)
                  for value in options]
    pairs, issues = [], []
    ref = context["context_ref"]
    for option_index, hypothesis in enumerate(hypotheses):
        try:
            count = _pair_token_count(tokenizer, context["prefix"], hypothesis)
        except Exception as error:
            issues.append({"context_ref": ref, "view": "title_row", "option_index": option_index,
                           "reason": "tokenization_error", "error_type": type(error).__name__,
                           "detail": str(error)})
            continue
        if count <= MAX_TOKENS:
            pairs.append({"id": f"{ref}:title_row:{option_index}:0", "context_ref": ref,
                          "view": "title_row", "option_index": option_index, "chunk_index": 0,
                          "source_start": None, "source_end": None,
                          "premise": context["prefix"], "hypothesis": hypothesis})
        else:
            issues.append({"context_ref": ref, "view": "title_row", "option_index": option_index,
                           "reason": "metadata_exceeds_512_tokens", "token_count": count})

    body = context["body_text"]
    body_prefix = context["prefix"] + "\n引用本文：\n"
    if not body:
        for option_index, hypothesis in enumerate(hypotheses):
            try:
                count = _pair_token_count(tokenizer, context["prefix"], hypothesis)
            except Exception as error:
                issues.append({"context_ref": ref, "view": "body", "option_index": option_index,
                               "reason": "tokenization_error", "error_type": type(error).__name__,
                               "detail": str(error)})
                continue
            if count <= MAX_TOKENS:
                pairs.append({"id": f"{ref}:body:{option_index}:0", "context_ref": ref,
                              "view": "body", "option_index": option_index, "chunk_index": 0,
                              "source_start": 0, "source_end": 0,
                              "premise": context["prefix"], "hypothesis": hypothesis})
            else:
                issues.append({"context_ref": ref, "view": "body", "option_index": option_index,
                               "reason": "metadata_exceeds_512_tokens", "token_count": count})
        return pairs, issues

    # If the fixed metadata cannot fit with even an empty body, every body
    # chunk would fail identically. Preserve the full span as an explicit issue.
    overlong_metadata = []
    metadata_errors = []
    for option_index, hypothesis in enumerate(hypotheses):
        try:
            if _pair_token_count(tokenizer, body_prefix, hypothesis) > MAX_TOKENS:
                overlong_metadata.append(option_index)
        except Exception as error:
            metadata_errors.append({"context_ref": ref, "view": "body", "option_index": option_index,
                                    "reason": "tokenization_error", "error_type": type(error).__name__,
                                    "detail": str(error), "source_start": 0,
                                    "source_end": len(body)})
    if metadata_errors:
        issues.extend(metadata_errors)
        return pairs, issues
    if overlong_metadata:
        issues.extend({"context_ref": ref, "view": "body", "option_index": option_index,
                       "reason": "metadata_exceeds_512_tokens", "source_start": 0,
                       "source_end": len(body)} for option_index in overlong_metadata)
        return pairs, issues

    start = 0
    chunk_index = 0
    while start < len(body):
        try:
            length, reason = _largest_fitting_chunk(tokenizer, context["prefix"], body, start, hypotheses)
        except Exception as error:
            issues.append({"context_ref": ref, "view": "body", "option_index": None,
                           "reason": "tokenization_error", "error_type": type(error).__name__,
                           "detail": str(error), "source_start": start, "source_end": len(body)})
            break
        if reason:
            issues.append({"context_ref": ref, "view": "body", "option_index": None,
                           "reason": reason, "source_start": start,
                           "source_end": min(start + 1, len(body))})
            # Keep the source span represented as an explicit unknown chunk.
            end = min(start + 1, len(body))
        else:
            end = start + length
            premise = body_prefix + body[start:end]
            for option_index, hypothesis in enumerate(hypotheses):
                pairs.append({"id": f"{ref}:body:{option_index}:{chunk_index}", "context_ref": ref,
                              "view": "body", "option_index": option_index,
                              "chunk_index": chunk_index, "source_start": start, "source_end": end,
                              "premise": premise, "hypothesis": hypothesis})
        start = end
        chunk_index += 1
    return pairs, issues


def aggregate_option_states(context: dict, pairs: list[dict], records: list[dict],
                            issues: list[dict] | None = None) -> list[dict]:
    """Aggregate model pair outputs; technical issues and mixed relations abstain."""
    by_id = {record.get("id"): record for record in records}
    issue_rows = issues or []
    states = []
    for view in VIEWS:
        for option_index, option in enumerate(context["option_values"]):
            relevant = [p for p in pairs if p["view"] == view and p["option_index"] == option_index]
            reasons = [dict(issue) for issue in issue_rows
                       if issue.get("view") == view and issue.get("option_index") in (None, option_index)]
            relations = []
            for pair in relevant:
                record = by_id.get(pair["id"])
                if record is None:
                    reasons.append({"reason": "missing_model_output", "pair_id": pair["id"]})
                    continue
                try:
                    probabilities = NLI._probabilities(record)
                    token_count = record.get("token_count_untruncated")
                    if (probabilities is None or record.get("error_type") is not None
                            or not isinstance(token_count, int) or isinstance(token_count, bool)
                            or token_count < 1 or token_count > MAX_TOKENS):
                        raise ValueError(record.get("note") or "technical_or_length_unknown")
                    winner = max(LABELS, key=lambda label: probabilities[label])
                    if probabilities[winner] < NLI.THRESHOLD:
                        relations.append("unknown")
                    else:
                        relations.append(winner)
                except Exception as error:
                    reasons.append({"reason": "technical_or_invalid_model_output",
                                    "pair_id": pair["id"], "error_type": type(error).__name__,
                                    "detail": str(error)})
            if reasons:
                state = "unknown"
            elif "support" in relations and "conflict" in relations:
                state = "unknown"
                reasons.append({"reason": "support_conflict_coexist"})
            elif "support" in relations:
                state = "support"
            elif "conflict" in relations:
                state = "conflict"
            else:
                state = "unknown"
                if not relations:
                    reasons.append({"reason": "no_usable_pairs"})
            states.append({"context_ref": context["context_ref"], "view": view,
                           "option_index": option_index, "option_value": option,
                           "state": state, "unknown_reasons": reasons})
    return states


def acquire_nli_weights(destination: Path) -> dict:
    """Download only pinned NLI files and verify their frozen hashes."""
    from huggingface_hub import hf_hub_download

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    downloaded = {}
    for name, expected in NLI.NLI_FILE_SHA256.items():
        source = Path(hf_hub_download(repo_id=NLI.MODEL_ID, revision=NLI.MODEL_REVISION,
                                      filename=name))
        actual = sha256(source)
        if actual != expected:
            raise RuntimeError("downloaded NLI file SHA256 differs: " + name)
        target = destination / name
        shutil.copyfile(source, target)
        downloaded[name] = {"sha256": actual, "size_bytes": target.stat().st_size}
    GPU.verify_nli_files(destination)
    return downloaded


def _write_json(path: Path, value: dict) -> None:
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()


def _append_rows(handle, rows: list[dict]) -> None:
    for row in rows:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    handle.flush()


def _record_identity(record: dict) -> tuple:
    return tuple(record.get(name) for name in (
        "id", "premise", "hypothesis", "context_ref", "view", "option_index",
        "chunk_index", "source_start", "source_end"))


def _output_file_receipt(path: Path) -> dict:
    return {"size_bytes": path.stat().st_size, "sha256": sha256(path)}


def run(bundle_dir: Path, output_dir: Path) -> dict:
    bundle_dir, output_dir = Path(bundle_dir).resolve(), Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    bundle = GPU.verify_bundle(bundle_dir)
    contexts_path = bundle_dir / "contexts.jsonl"
    contexts = read_contexts(contexts_path)
    if "contexts.jsonl" not in bundle["files"]:
        raise ValueError("verified bundle must inventory contexts.jsonl")
    manifest = json.loads((bundle_dir / "BUNDLE_MANIFEST.json").read_text(encoding="utf-8"))
    option_count = sum(len(context["option_values"]) for context in contexts)
    for field, actual in (("context_count", len(contexts)), ("option_count", option_count)):
        intended = manifest.get(field)
        if not isinstance(intended, int) or isinstance(intended, bool) or intended != actual:
            raise ValueError(f"BUNDLE_MANIFEST {field} differs from contexts.jsonl")
    output_dir.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    nli_dir = Path(tempfile.mkdtemp(prefix="sku-v7-small-nli-single-v1-", dir="/tmp"))
    downloaded = acquire_nli_weights(nli_dir)
    scorer = GPU.CudaScorer("/tmp/unused-jev", nli_dir)
    tokenizer, _model, nli_pins, load_times = scorer.load("nli")
    if list(scorer.loaded) != ["nli"]:
        raise RuntimeError("the worker must load exactly the NLI model")
    _write_json(output_dir / "model-ready.json", {
        "loaded_models": ["nli"], "nli_pin": nli_pins,
        "downloaded_files": downloaded, "load_times": load_times,
        "cuda_runtime": scorer.runtime,
    })
    code_hashes = {name: sha256(HERE / name) for name in CODE_NAMES}
    manifest_path = bundle_dir / "BUNDLE_MANIFEST.json"
    input_hashes = {"BUNDLE_MANIFEST.json": sha256(manifest_path),
                    "contexts.jsonl": sha256(contexts_path)}
    freeze = {
        "schema_version": "kaggle-v7-small-nli-single-v1",
        "input_sha256": input_hashes, "bundle_manifest_sha256": bundle["manifest_sha256"],
        "code_sha256": code_hashes, "code_names": list(CODE_NAMES),
        "policy": {"labels_read": False, "desired_selected_value_loaded": False,
                   "old_answers_loaded": False, "category_conditions_or_rules": False,
                   "subset_selection": False, "scope_status": "unproved",
                   "citation_semantics_supported": False,
                   "production_adoption_allowed": False},
        "text_policy": {"prefix": "provided legacy title/selected-row prefix unchanged",
                        "hypothesis_template": HYPOTHESIS_TEMPLATE,
                        "views": list(VIEWS), "max_body_chunk_chars": MAX_CHUNK_CHARS,
                        "max_pair_tokens": MAX_TOKENS,
                        "chunk_policy": "binary-search each nonoverlapping source span against every original option; no truncation"},
        "model_pin": {"model_id": NLI.MODEL_ID, "revision": NLI.MODEL_REVISION,
                      "file_sha256": NLI.NLI_FILE_SHA256,
                      "precision": "fp32", "device": "cuda"},
        "runtime_policy": {"batch_size": 16, "threads": 4, "attention_implementation": "eager",
                           "tf32": False, "cpu_fallback": False},
        "gpu_runtime": scorer.runtime,
    }
    _write_json(output_dir / "EXECUTION_FREEZE.json", freeze)

    cache = {}
    context_count = len(contexts)
    expected_views = context_count * len(VIEWS)
    seen_views = set()
    written_option_state_count = 0
    scored_pair_count = 0
    technical_failure_raw_record_count = 0
    issue_count = 0
    preparation_issue_count = 0
    inferred_unique_pair_count = 0
    inference_seconds = 0.0
    with (output_dir / "raw-scores.jsonl").open("x", encoding="utf-8") as raw_handle, \
            (output_dir / "option-states.jsonl").open("x", encoding="utf-8") as states_handle:
        for index, context in enumerate(contexts, 1):
            prepared, issues = prepare_context_pairs(context, tokenizer)
            records = []
            if prepared:
                try:
                    result = scorer.run_pairs(prepared, cache=cache, threads=4, batch_size=16)
                    candidate_records = result["records"]
                    if (len(candidate_records) != len(prepared)
                            or [_record_identity(record) for record in candidate_records]
                            != [_record_identity(pair) for pair in prepared]):
                        raise ValueError("model output identity/order differs from prepared pairs")
                    records = candidate_records
                    inference_seconds += float(result.get("runtime", {}).get("inference_seconds", 0.0))
                    inferred_unique_pair_count += int(result.get("runtime", {}).get("inferred_unique_pair_count", 0))
                    cache.update({record["cache_key"]: record for record in records})
                except Exception as error:
                    records = []
                    for pair in prepared:
                        records.append({**pair, "probabilities": None,
                                        "token_count_untruncated": None, "latency_seconds": 0.0,
                                        "note": str(error), "error_type": type(error).__name__,
                                        "model_id": NLI.MODEL_ID, "model_revision": NLI.MODEL_REVISION,
                                        "precision": "fp32", "device": "cuda"})
            _append_rows(raw_handle, records)
            scored_pair_count += len(records)
            technical_failure_raw_record_count += sum(record.get("error_type") is not None for record in records)
            states = aggregate_option_states(context, prepared, records, issues)
            expected_states = len(context["option_values"]) * len(VIEWS)
            if len(states) != expected_states:
                raise RuntimeError("option-state record count differs from expected context views")
            _append_rows(states_handle, states)
            written_option_state_count += len(states)
            seen_views.update((state["context_ref"], state["view"]) for state in states)
            preparation_issue_count += len(issues)
            issue_count += sum(len(state["unknown_reasons"]) for state in states)
            print(json.dumps({"stage": "context", "completed": index,
                              "total": context_count, "context_ref": context["context_ref"]}),
                  flush=True)

    expected_views = context_count * len(VIEWS)
    expected_option_states = option_count * len(VIEWS)
    if len(seen_views) != expected_views or written_option_state_count != expected_option_states:
        raise RuntimeError("written context-view or option-state count differs from expected count")
    if list(scorer.loaded) != ["nli"]:
        raise RuntimeError("the worker loaded an unexpected model")
    elapsed = time.perf_counter() - started
    output_files = {name: _output_file_receipt(output_dir / name) for name in (
        "raw-scores.jsonl", "option-states.jsonl", "model-ready.json", "EXECUTION_FREEZE.json")}
    completion = {
        "schema_version": "kaggle-v7-small-nli-single-completion-v1",
        "expected_context_count": context_count,
        "expected_context_view_count": expected_views,
        "written_context_view_count": len(seen_views),
        "option_state_count": written_option_state_count,
        "options_count": option_count,
        "scored_pair_count": scored_pair_count,
        "technical_failure_raw_record_count": technical_failure_raw_record_count,
        "issue_count": issue_count,
        "preparation_issue_count": preparation_issue_count,
        "inferred_unique_pair_count": inferred_unique_pair_count,
        "actual_loaded_models": list(scorer.loaded),
        "output_model_pin": freeze["model_pin"],
        "output_files": output_files,
        "inference_elapsed_seconds": inference_seconds,
        "worker_elapsed_seconds": elapsed,
        "scope_status": "unproved", "citation_semantics_supported": False,
        "production_adoption_allowed": False,
    }
    _write_json(output_dir / "COMPLETION.json", completion)
    return completion


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    run(args.bundle_dir, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
