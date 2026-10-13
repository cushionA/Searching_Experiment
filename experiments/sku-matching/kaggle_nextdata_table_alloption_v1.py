#!/usr/bin/env python3
"""Pinned GPU all-option relation diagnostic on paired full/description text.

This is not the Luna grounding protocol. Neither checkpoint writes citations or
classifies applicability scope. Numeric option states are experimental only.
All original options are scored separately; no winner excludes other options.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import time

import kaggle_full_condition_choices_v1 as GPU

JEV, NLI = GPU.JEV, GPU.NLI
VARIANTS = ("fullbody", "tablebody")
RELATIONS = ("support", "conflict", "unknown")
RELATION_LABELS = {
    "support": "支持（同じ規格）",
    "conflict": "矛盾（違う規格）",
    "unknown": "不明（特定できない）",
}
QUESTION = "現在の実選択の商品について、〈{axis_name}〉が〈{option}〉であるという記述は支持されますか。"
HYPOTHESIS = "現在の実選択の商品の〈{axis_name}〉は〈{option}〉である。"
DESCRIPTIONS = {
    "support": "現在の実選択の商品がこの規格に該当することを記載が示す。",
    "conflict": "現在の実選択の商品がこの規格に該当しないことを記載が示す。",
    "unknown": "現在の実選択の商品がこの規格に該当するか記載から特定できない。",
}
POLICY = {
    "schema_version": "sku-nextdata-table-alloption-gpu-v1",
    "precision": "fp32", "device": "cuda", "batch_size": 16,
    "nli_confidence_threshold": NLI.THRESHOLD,
    "jev_relation_decision": "unique argmax among three relation descriptions; ties unknown",
    "chunk_aggregation": "any technical failure unknown; support and conflict coexist unknown; otherwise any support/support or any conflict/conflict; otherwise unknown",
    "input_length_policy": "all text in consecutive nonoverlapping character chunks; no truncation; actual selected facts and all source options repeated in every chunk; metadata or one-character-overflow unknown",
    "scope_status": "unproved", "citation_semantics_supported": False,
    "production_adoption_allowed": False,
    "task_difference_from_luna": "independent numeric relation classification of every option using complete text chunks; no cross-chunk reasoning, quotation generation or semantic scope certification",
    "question_template": QUESTION, "hypothesis_template": HYPOTHESIS,
    "relation_labels": RELATION_LABELS, "relation_descriptions": DESCRIPTIONS,
    "input_variants": {"fullbody": "original native full body",
                       "tablebody": "cleaned_description_fields_with_full_fallback; tablebody is a transport alias"},
    "image_semantics": "images and mediaOnly metadata are provenance-only; no OCR or pixels enter these text checkpoints",
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def value_sha(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def validate_document_identity(full, table):
    if not re.fullmatch(r"[0-9a-f]{64}", str(full.get("document_sha256", ""))):
        raise ValueError("frozen document SHA required")
    if table.get("document_sha256") != full["document_sha256"]:
        raise ValueError("paired original SKU document identity differs")
    for body in (full, table):
        blocks = body.get("blocks", [])
        if len({block["block_id"] for block in blocks}) != len(blocks):
            raise ValueError("duplicate body blocks")
        for index, block in enumerate(blocks):
            if not isinstance(block.get("support_text"), str):
                raise ValueError("body text must be an exact string from frozen view")
            if "body_index" in block and block["body_index"] != index:
                raise ValueError("local body index differs")


def verify_root_population(input_file, source_rows, root_freeze, sourceproof_receipt):
    """Bind coverage to independently frozen ROOT expectations, not input size."""
    frozen = json.loads(Path(root_freeze).read_text(encoding="utf-8"))
    expected = (frozen.get("pair_count"), frozen.get("product_count"), frozen.get("case_count"),
                frozen.get("option_prediction_count"))
    if expected != (971, 46, 569, 8536):
        raise ValueError("ROOT expected complete population differs from requested experiment")
    if sha(input_file) != frozen.get("public_input_sha256"):
        raise ValueError("public input SHA differs from independent ROOT freeze")
    refs = [row["pair_ref"] for row in source_rows]
    if len(refs) != 971 or len(set(refs)) != 971 or value_sha(refs) != frozen.get("pair_ref_vector_sha256"):
        raise ValueError("exact ROOT frozen 971 pair-ref vector differs")
    vectors = [{"pair_ref": row["pair_ref"], "axis_name": row["full_payload"]["context"]["hypothesis"]["axis_name"],
                "option_values": [c["whole_option"] for c in row["full_payload"]["candidate_vector"]]}
               for row in source_rows]
    if value_sha(vectors) != frozen.get("option_vector_sha256") or 2 * sum(len(v["option_values"]) for v in vectors) != 8536:
        raise ValueError("exact ROOT original option vectors differ")
    for payload_key, digest_key in (("full_payload", "full_body_vector_sha256"),
                                    ("table_payload", "description_body_vector_sha256")):
        body_vector = [{"pair_ref": row["pair_ref"],
                        "eligible_source_bodies": row[payload_key]["context"]["eligible_source_bodies"],
                        "eligible_target_body": row[payload_key]["context"]["eligible_target_body"]}
                       for row in source_rows]
        if value_sha(body_vector) != frozen.get(digest_key):
            raise ValueError("exact ROOT source-proved body view differs: " + payload_key)
    if sha(sourceproof_receipt) != frozen.get("sourceproof_receipt_sha256"):
        raise ValueError("cleaned NEXT_DATA source proof differs from ROOT freeze")
    proof = json.loads(Path(sourceproof_receipt).read_text(encoding="utf-8"))
    if proof.get("product_count") != 46 or proof.get("variant") != "cleaned_description_fields_with_full_fallback":
        raise ValueError("source proof covers a different population or extraction variant")
    counts = proof.get("collector_status_counts", {})
    fallback_count = proof.get("fallback_count")
    if (not isinstance(counts.get("collected"), int) or not isinstance(fallback_count, int)
            or fallback_count < 0 or counts["collected"] + fallback_count != 46):
        raise ValueError("source proof does not cover all collected/fallback original products")
    return frozen


def adapt_public_pair(source):
    """Remove transport IDs from model text; retain every semantic field."""
    payload = source["full_payload"]
    context = payload["context"]
    options = [member["whole_option"] for member in payload["candidate_vector"]]
    if options != context["hypothesis"]["family_values"]:
        raise ValueError("original candidate vector and family differ")
    selected = [{"axis_name": fact["axis_name"], "value": fact["value"]}
                for fact in context["target_selected_facts"]]
    variants = {}
    for variant, key in (("fullbody", "full_payload"), ("tablebody", "table_payload")):
        current = source[key]
        current_context = current["context"]
        shared = set(context) - {"eligible_source_bodies", "eligible_target_body"}
        if (current["candidate_vector"] != payload["candidate_vector"]
                or set(current_context) - {"eligible_source_bodies", "eligible_target_body"} != shared
                or any(current_context[k] != context[k] for k in shared)):
            raise ValueError("paired variants changed original options, selections or inventory")
        validate_document_identity(context["eligible_target_body"], current_context["eligible_target_body"])
        if context["eligible_target_body"]["document_sha256"] != context["target_document_sha256"]:
            raise ValueError("target body document SHA differs from selected target identity")
        full_sources, table_sources = context["eligible_source_bodies"], current_context["eligible_source_bodies"]
        if len(full_sources) != len(table_sources):
            raise ValueError("paired source body corpus differs")
        for full_body, table_body in zip(full_sources, table_sources, strict=True):
            validate_document_identity(full_body, table_body)
        fields = []
        inventory = current_context["target_native_sku_inventory"]
        if inventory:
            semantic_inventory = {
                "axes": [{"axis_name": axis.get("axis_name"),
                          "members": [member["value"] for member in axis.get("members", [])]}
                         for axis in inventory.get("axes", [])],
                "all_option_states": [[{"axis_name": v["axis_name"], "value": v["value"]}
                                      for v in state.get("values", [])]
                                     for state in inventory.get("all_option_states", [])],
            }
            fields.append({"field_ref": "target-native-inventory", "text": canonical(semantic_inventory),
                           "role": "current_all_options_not_actual_selection"})
        target = current_context["eligible_target_body"]
        for index, block in enumerate(target.get("blocks", [])):
            fields.append({"field_ref": f"target-body:{index}", "text": block["support_text"],
                           "role": "current_selected_product"})
        for source_index, body in enumerate(current_context["eligible_source_bodies"]):
            # The native pipeline may include target blocks again in the
            # source-body union. Exact field identity is already retained above.
            if body.get("document_sha256") == target.get("document_sha256"):
                target_blocks = {b["block_id"]: b["support_text"] for b in target.get("blocks", [])}
            else:
                target_blocks = {}
            for index, block in enumerate(body.get("blocks", [])):
                if target_blocks.get(block["block_id"]) == block["support_text"]:
                    continue
                fields.append({"field_ref": f"reference-body:{source_index}:{index}", "text": block["support_text"],
                               "role": "reference_source_product"})
        variants[variant] = {"body_fields": fields}
    return {"pair_ref": source["pair_ref"], "axis_name": context["hypothesis"]["axis_name"],
            "option_values": options, "target_selected_facts": selected, "context_header": "",
            "variants": variants}


def load_rows(path, root_freeze=None, sourceproof_receipt=None):
    source_rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]
    if root_freeze is not None:
        verify_root_population(path, source_rows, root_freeze, sourceproof_receipt)
    rows = [adapt_public_pair(row) if "full_payload" in row else row for row in source_rows]
    if not rows or len({r.get("pair_ref") for r in rows}) != len(rows):
        raise ValueError("nonempty unique pair_ref required")
    for row in rows:
        if not isinstance(row.get("axis_name"), str) or not isinstance(row.get("option_values"), list) or not row["option_values"]:
            raise ValueError("axis and original options required")
        if any(not isinstance(v, str) or not v for v in row["option_values"]):
            raise ValueError("options must be nonempty raw strings")
        if len(set(row["option_values"])) != len(row["option_values"]):
            raise ValueError("duplicate raw options")
        if not isinstance(row.get("target_selected_facts"), list):
            raise ValueError("complete actual selected facts required")
        if not isinstance(row.get("context_header"), str):
            raise ValueError("context header required")
        if set(row.get("variants", {})) != set(VARIANTS):
            raise ValueError("both paired variants required")
        for variant in VARIANTS:
            fields = row["variants"][variant].get("body_fields")
            if not isinstance(fields, list):
                raise ValueError("complete body field list required")
            if any(not isinstance(f.get("field_ref"), str) or not isinstance(f.get("text"), str) for f in fields):
                raise ValueError("body field reference and raw text required")
            if len({f["field_ref"] for f in fields}) != len(fields):
                raise ValueError("duplicate body field references")
    return rows


def metadata(row):
    selected = "\n".join(f"{f['axis_name']}：{f['value']}" for f in row["target_selected_facts"])
    options = "\n".join(f"{i + 1}. {v}" for i, v in enumerate(row["option_values"]))
    return ("現在の実選択の事実：\n" + selected + "\n" + row["context_header"]
            + "\n比較対象の全選択肢（実選択を示すリストではない）：\n" + options)


def choice_request(row, option, premise, request_id):
    return {"id": request_id, "question": QUESTION.format(axis_name=row["axis_name"], option=option),
            "state": premise, "choices": [{"label": RELATION_LABELS[state], "description": DESCRIPTIONS[state]}
                                          for state in RELATIONS]}


def max_pair_tokens(row, premise, tokenizers):
    longest = 0
    jev, nli = tokenizers["jev"], tokenizers["nli"]
    jev_candidate_count = max(len(jev.encode(JEV.render_candidate({"label": RELATION_LABELS[state],
                          "description": DESCRIPTIONS[state]}), add_special_tokens=False)) for state in RELATIONS)
    nli_premise_count = len(nli.encode(premise, add_special_tokens=False))
    for option in row["option_values"]:
        request = choice_request(row, option, premise, "length-check")
        context = JEV.render_context(request["question"], premise)
        longest = max(longest, len(jev.encode(context, add_special_tokens=False)) + jev_candidate_count
                      + jev.num_special_tokens_to_add(pair=True))
        hypothesis = HYPOTHESIS.format(axis_name=row["axis_name"], option=option)
        longest = max(longest, nli_premise_count + len(nli.encode(hypothesis, add_special_tokens=False))
                      + nli.num_special_tokens_to_add(pair=True))
    return longest


def prepare_chunks(row, variant, tokenizers):
    """Cover every raw character, or retain an explicit technical unknown."""
    prefix = metadata(row)
    base_count = max_pair_tokens(row, prefix, tokenizers)
    fields = row["variants"][variant]["body_fields"]
    if base_count > 512:
        return [], {"reason": "metadata_exceeds_512", "metadata_max_tokens": base_count,
                    "retained_body_character_count": sum(len(f["text"]) for f in fields)}
    chunks = []
    for field in fields:
        text, start = field["text"], 0
        role = field.get("role", "unspecified_body")
        body_heading = {
            "current_selected_product": "現在の実選択の商品情報の本文断片（全選択肢の説明を含む場合がある）",
            "current_all_options_not_actual_selection": "現在の商品に存在する全SKUの一覧断片（実選択を示すリストではない）",
            "reference_source_product": "参照元の商品情報の本文断片（現在の実選択の判定対象商品ではない）",
            "unspecified_body": "商品情報の本文断片",
        }[role]
        while start < len(text):
            end = min(len(text), start + 800)
            def premise_at(stop):
                return prefix + "\n" + body_heading + "：\n" + text[start:stop]
            if max_pair_tokens(row, premise_at(end), tokenizers) > 512:
                lower, upper = start, end
                while upper - lower > 1:
                    midpoint = (lower + upper) // 2
                    if max_pair_tokens(row, premise_at(midpoint), tokenizers) <= 512:
                        lower = midpoint
                    else:
                        upper = midpoint
                end = lower
            if end == start:
                return [], {"reason": "one_character_exceeds_512", "metadata_max_tokens": base_count,
                            "retained_body_character_count": sum(len(f["text"]) for f in fields)}
            chunk = {"chunk_ref": f"{row['pair_ref']}:{variant}:{len(chunks)}", "field_ref": field["field_ref"],
                     "start": start, "end": end, "text": text[start:end], "premise": premise_at(end), "role": role,
                     "raw_field_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
            if max_pair_tokens(row, chunk["premise"], tokenizers) > 512:
                raise ValueError("prepared chunk exceeds actual model token limit")
            chunks.append(chunk)
            start = end
    if not chunks:
        chunks = [{"chunk_ref": f"{row['pair_ref']}:{variant}:metadata", "field_ref": None,
                   "start": 0, "end": 0, "text": "", "premise": prefix, "raw_field_sha256": None}]
    return chunks, None


def aggregate(states, technical_failure=False):
    if technical_failure or not states or ("support" in states and "conflict" in states):
        return "unknown"
    if "support" in states:
        return "support"
    if "conflict" in states:
        return "conflict"
    return "unknown"


def jev_state(record):
    if record["status"] != "ok":
        return "unknown", True
    logits = record["logits"]
    if sum(v == max(logits.values()) for v in logits.values()) != 1:
        return "unknown", False
    inverse = {value: key for key, value in RELATION_LABELS.items()}
    return inverse[record["argmax_label"]], False


def plan(rows, tokenizers):
    for row in rows:
        for variant in VARIANTS:
            chunks, issue = prepare_chunks(row, variant, tokenizers)
            yield row, variant, chunks, issue


def run(args):
    bundle = GPU.verify_bundle(args.bundle_dir)
    if not args.input_file.resolve().is_relative_to(args.bundle_dir.resolve()):
        raise ValueError("input must belong to the verified source-only bundle")
    root_freeze = args.bundle_dir / "provenance/root-population-freeze.json"
    sourceproof = args.bundle_dir / "provenance/description-sourceproof.json"
    if json.loads(root_freeze.read_text(encoding="utf-8")).get("root_verified") is not True:
        raise ValueError("ROOT independently verified population freeze required for GPU inference")
    rows = load_rows(args.input_file, root_freeze, sourceproof)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)
    roots = GPU.acquire_weights(args.weights_dir, args.jev_provenance, download=not args.no_download)
    scorer = GPU.CudaScorer(roots["jev"], roots["nli"])
    tokenizers = {kind: scorer.load(kind)[0] for kind in ("jev", "nli")}
    started = time.perf_counter()
    totals = Counter()
    files = ["chunks.jsonl", "jev-chunk-scores.jsonl", "nli-chunk-scores.jsonl", "option-predictions.jsonl",
             *[f"predictions-{kind}-{variant}.jsonl" for kind in ("jev", "nli") for variant in VARIANTS]]
    handles = {name: (output / name).open("x", encoding="utf-8") for name in files}
    input_sha = sha(args.input_file)
    write_json(output / "EXECUTION_FREEZE.json", {**POLICY, "input_sha256": input_sha,
               "root_population_freeze_sha256": sha(root_freeze), "description_sourceproof_sha256": sha(sourceproof),
               "bundle_manifest_sha256": bundle["manifest_sha256"], "pair_count": len(rows),
               "expected_option_prediction_count": 2 * sum(len(row["option_values"]) for row in rows),
               "gpu_runtime": scorer.runtime,
               "model_pins": {kind: scorer.loaded[kind][2] for kind in ("jev", "nli")}})
    def append(name, record):
        handles[name].write(canonical(record) + "\n")
        handles[name].flush()
    try:
        for row, variant, chunks, issue in plan(rows, tokenizers):
            totals["paired_input_count"] += 1
            totals["chunk_count"] += len(chunks)
            totals["technical_unknown_input_count"] += issue is not None
            for chunk in chunks:
                append("chunks.jsonl", {k: v for k, v in chunk.items() if k != "premise"})
            decisions = {"jev": [], "nli": []}
            requests = [choice_request(row, option, chunk["premise"], chunk["chunk_ref"] + f":option-{option_index}")
                        for option_index, option in enumerate(row["option_values"]) for chunk in chunks]
            pairs = [{"id": request["id"], "premise": request["state"],
                      "hypothesis": HYPOTHESIS.format(axis_name=row["axis_name"], option=option)}
                     for option_index, option in enumerate(row["option_values"])
                     for request in requests[option_index * len(chunks):(option_index + 1) * len(chunks)]]
            print(canonical({"stage": "inference-start", "pair_ref": row["pair_ref"], "variant": variant,
                             "option_chunk_count": len(requests)}), flush=True)
            jev_records = scorer.run_choices(requests)["records"] if requests else []
            nli_records = scorer.run_pairs(pairs)["records"] if pairs else []
            for option_index, option in enumerate(row["option_values"]):
                jev_states, nli_states, jev_failed, nli_failed = [], [], issue is not None, issue is not None
                if chunks:
                    jev = jev_records[option_index * len(chunks):(option_index + 1) * len(chunks)]
                    nli = nli_records[option_index * len(chunks):(option_index + 1) * len(chunks)]
                    for chunk, j, n in zip(chunks, jev, nli, strict=True):
                        jstate, jfailure = jev_state(j)
                        nfailure = n["probabilities"] is None or n["error_type"] is not None or (n["token_count_untruncated"] or 513) > 512
                        jev_states.append(jstate)
                        nli_states.append(n["relation"])
                        jev_failed |= jfailure
                        nli_failed |= nfailure
                        common = {"pair_ref": row["pair_ref"], "variant": variant, "option_index": option_index,
                                  "chunk_ref": chunk["chunk_ref"]}
                        append("jev-chunk-scores.jsonl", {**common, "state": jstate, "status": j["status"],
                              "logits": j["logits"], "probabilities": j["probabilities"], "token_counts": j["token_counts"],
                              "cache_key": j["cache_key"], "error_type": j["error_type"]})
                        append("nli-chunk-scores.jsonl", {**common, "state": n["relation"], "probabilities": n["probabilities"],
                              "predicted_class": n["predicted_class"], "predicted_confidence": n["predicted_confidence"],
                              "token_count_untruncated": n["token_count_untruncated"], "cache_key": n["cache_key"],
                              "error_type": n["error_type"]})
                        totals["option_chunk_count"] += 1
                states = {"jev": aggregate(jev_states, jev_failed), "nli": aggregate(nli_states, nli_failed)}
                append("option-predictions.jsonl", {"pair_ref": row["pair_ref"], "variant": variant,
                      "option_index": option_index, "whole_option": option, "states": states,
                      "chunk_state_counts": {"jev": dict(Counter(jev_states)), "nli": dict(Counter(nli_states))},
                      "technical_failure": {"jev": jev_failed, "nli": nli_failed}, "input_issue": issue,
                      "scope_status": "unproved", "citation_semantics_supported": False,
                      "production_adoption_allowed": False})
                totals["option_prediction_count"] += 1
                for kind, state in states.items():
                    totals[f"{kind}_{variant}_{state}"] += 1
                    decisions[kind].append({"option_index": option_index,
                          "state": "explicit_conflict" if state == "conflict" else state,
                          "chunk_state_counts": dict(Counter(jev_states if kind == "jev" else nli_states)),
                          "technical_failure": jev_failed if kind == "jev" else nli_failed})
            for kind in ("jev", "nli"):
                append(f"predictions-{kind}-{variant}.jsonl", {"pair_ref": row["pair_ref"],
                       "decisions": decisions[kind], "input_issue": issue,
                       "scope_status": "unproved", "citation_semantics_supported": False,
                       "production_adoption_allowed": False})
            print(canonical({"stage": "pair-complete", "pair_ref": row["pair_ref"], "variant": variant,
                             "totals": dict(totals), "elapsed_seconds": time.perf_counter() - started}), flush=True)
    finally:
        for handle in handles.values():
            handle.close()
    if sha(args.input_file) != input_sha:
        raise ValueError("model input changed during inference")
    if GPU.verify_bundle(args.bundle_dir)["manifest_sha256"] != bundle["manifest_sha256"]:
        raise ValueError("source-only bundle changed during inference")
    expected_options = 2 * sum(len(row["option_values"]) for row in rows)
    if totals["paired_input_count"] != 2 * len(rows) or totals["option_prediction_count"] != expected_options:
        raise ValueError("output does not cover every input and every original option")
    receipt = {**POLICY, "input_sha256": input_sha, "bundle_manifest_sha256": bundle["manifest_sha256"],
               "root_population_freeze_sha256": sha(root_freeze), "description_sourceproof_sha256": sha(sourceproof),
               "labels_read": False, "pair_count": len(rows), "expected_option_prediction_count": expected_options,
               "totals": dict(totals), "gpu_runtime": scorer.runtime,
               "model_pins": {kind: scorer.loaded[kind][2] for kind in ("jev", "nli")},
               "elapsed_seconds": time.perf_counter() - started,
               "elapsed_seconds_scope": "after model loading; complete input chunking, tokenization, model inference and prediction writing",
               "files": {name: {"size_bytes": (output / name).stat().st_size, "sha256": sha(output / name)} for name in files}}
    write_json(output / "COMPLETION.json", receipt)
    print(canonical({"stage": "complete", "totals": dict(totals), "gpu": scorer.runtime["gpu_name"]}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--input-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--weights-dir", type=Path, required=True)
    parser.add_argument("--jev-provenance", type=Path, required=True)
    parser.add_argument("--no-download", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
