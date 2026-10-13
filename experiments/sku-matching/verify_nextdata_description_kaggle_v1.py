#!/usr/bin/env python3
"""Verify a fixed-version GPU artifact without reading reference labels."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import zipfile

import kaggle_nextdata_table_alloption_v1 as TASK


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(path):
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def verify(archive, artifact_receipt, bundle, output):
    packaged = TASK.GPU.verify_bundle(bundle)
    frozen = read(bundle / "provenance/root-population-freeze.json")
    if frozen.get("root_verified") is not True:
        raise ValueError("independently verified ROOT freeze required")
    inputs = TASK.load_rows(bundle / "input/paired-public-inputs.jsonl",
                           bundle / "provenance/root-population-freeze.json",
                           bundle / "provenance/description-sourceproof.json")
    by_ref = {row["pair_ref"]: row for row in inputs}
    expected_refs = list(by_ref)
    artifact = read(artifact_receipt)
    if artifact["artifact_sha256"] != TASK.sha(archive) or artifact["artifact_bytes"] != archive.stat().st_size:
        raise ValueError("downloaded archive does not match its receipt")
    output.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as source:
        if source.testzip() is not None:
            raise ValueError("result CRC failure")
        names = source.namelist()
        if len(set(names)) != len(names):
            raise ValueError("duplicate result archive member")
        for member in source.infolist():
            relative = Path(member.filename)
            if relative.is_absolute() or ".." in relative.parts or (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("unsafe result archive member")
        source.extractall(output)
    completion = read(output / "COMPLETION.json")
    execution = read(output / "EXECUTION_FREEZE.json")
    expected_files = set(completion["files"]) | {"COMPLETION.json", "EXECUTION_FREEZE.json"}
    if set(names) != expected_files:
        raise ValueError("result archive does not have the exact completion inventory")
    for name, entry in completion["files"].items():
        path = output / name
        if entry["sha256"] != TASK.sha(path) or entry["size_bytes"] != path.stat().st_size:
            raise ValueError("result file size/SHA differs: " + name)
    for record in (completion, execution):
        if (record["input_sha256"] != frozen["public_input_sha256"]
                or record["bundle_manifest_sha256"] != packaged["manifest_sha256"]
                or record["root_population_freeze_sha256"] != TASK.sha(bundle / "provenance/root-population-freeze.json")
                or record["description_sourceproof_sha256"] != frozen["sourceproof_receipt_sha256"]):
            raise ValueError("GPU execution is not bound to ROOT source/code/input freeze")
        if (record.get("scope_status") != "unproved" or record.get("production_adoption_allowed") is not False
                or record.get("citation_semantics_supported") is not False):
            raise ValueError("numeric classification cannot certify citations/scope")
        for kind, module in (("jev", TASK.JEV), ("nli", TASK.NLI)):
            pin = record["model_pins"][kind]
            hashes = module.JEV_FILE_SHA256 if kind == "jev" else module.NLI_FILE_SHA256
            identity = (pin["model_id"], pin["revision"], pin["parameter_count"], pin["precision"], pin["device"])
            if identity != (module.MODEL_ID, module.MODEL_REVISION, module.FP32_PARAMETER_COUNT, "fp32", "cuda"):
                raise ValueError("GPU model identity/parameter/device/precision differs")
            if pin["file_sha256"] != hashes or {k:v["sha256"] for k,v in pin["files"].items()} != hashes:
                raise ValueError("GPU acquired model files differ from exact approved hashes")
        runtime = record["gpu_runtime"]
        if (runtime["device"] != "cuda" or runtime["precision"] != "fp32" or not runtime["gpu_name"]
                or not runtime["cuda_version"] or runtime["gpu_total_memory_bytes"] <= 0
                or runtime["autocast"] is not False or runtime["cuda_matmul_tf32"] is not False
                or runtime["cudnn_tf32"] is not False or runtime["attention_implementation"] != "eager"
                or runtime["float32_matmul_precision"] != "highest" or runtime["batch_size"] != 16):
            raise ValueError("actual CUDA FP32 runtime attestation missing or changed")
    if (completion["pair_count"] != 971 or completion["expected_option_prediction_count"] != 8536
            or completion["totals"]["paired_input_count"] != 1942
            or completion["totals"]["option_prediction_count"] != 8536
            or completion.get("labels_read") is not False):
        raise ValueError("GPU completion population/label policy mismatch")
    numerical, technical, issues, state_counts = {}, {}, {}, Counter()
    for row in rows(output / "option-predictions.jsonl"):
        key = (row["pair_ref"], row["variant"], row["option_index"])
        if key in numerical or row["variant"] not in TASK.VARIANTS:
            raise ValueError("duplicate or unknown numeric option output")
        source = by_ref[row["pair_ref"]]
        if source["option_values"][row["option_index"]] != row["whole_option"]:
            raise ValueError("original option identity changed")
        if set(row["states"]) != {"jev", "nli"} or any(v not in TASK.RELATIONS for v in row["states"].values()):
            raise ValueError("complete two-model three-state classification required")
        for kind, state in row["states"].items():
            if row["technical_failure"][kind] and state != "unknown":
                raise ValueError("technical failure was promoted to a semantic proof")
            state_counts[f"{kind}_{row['variant']}_{state}"] += 1
        numerical[key] = row["states"]
        technical[key] = row["technical_failure"]
        pair_key = key[:2]
        if pair_key in issues and issues[pair_key] != row["input_issue"]:
            raise ValueError("same paired input has inconsistent technical issue")
        issues[pair_key] = row["input_issue"]
    expected = {(ref, variant, index) for ref,row in by_ref.items() for variant in TASK.VARIANTS
                for index in range(len(row["option_values"]))}
    if set(numerical) != expected or len(numerical) != 8536:
        raise ValueError("missing original numeric options")
    for kind in ("jev", "nli"):
        for variant in TASK.VARIANTS:
            predictions = list(rows(output / f"predictions-{kind}-{variant}.jsonl"))
            if [p["pair_ref"] for p in predictions] != expected_refs:
                raise ValueError("model/variant does not preserve all 971 ordered original pair refs")
            for prediction in predictions:
                decisions = prediction["decisions"]
                options = by_ref[prediction["pair_ref"]]["option_values"]
                if [d["option_index"] for d in decisions] != list(range(len(options))):
                    raise ValueError("original option order/cardinality lost")
                for decision in decisions:
                    state = numerical[(prediction["pair_ref"], variant, decision["option_index"])][kind]
                    if decision["state"] != ("explicit_conflict" if state == "conflict" else state):
                        raise ValueError("collector prediction differs from numeric option classification")
    chunks, grouped = {}, defaultdict(list)
    for chunk in rows(output / "chunks.jsonl"):
        ref_text, variant, _ = chunk["chunk_ref"].split(":", 2)
        ref = int(ref_text)
        if ref not in by_ref or variant not in TASK.VARIANTS or chunk["chunk_ref"] in chunks:
            raise ValueError("chunk binding is duplicate or outside original population")
        chunks[chunk["chunk_ref"]] = (ref, variant, chunk)
        grouped[(ref, variant, chunk["field_ref"])].append(chunk)
    pair_chunk_counts = Counter((ref,variant) for ref,variant,_ in chunks.values())
    for ref,row in by_ref.items():
        for variant in TASK.VARIANTS:
            if issues[(ref,variant)] is not None:
                if pair_chunk_counts[(ref,variant)] or any(numerical[(ref,variant,i)][kind] != "unknown"
                      for i in range(len(row["option_values"])) for kind in ("jev","nli")):
                    raise ValueError("technical input failure must retain all options as unknown")
                continue
            fields = row["variants"][variant]["body_fields"]
            for field in fields:
                pieces = grouped[(ref,variant,field["field_ref"])]
                if ''.join(c["text"] for c in pieces) != field["text"]:
                    raise ValueError("GPU chunks silently lost or changed input characters")
                # raw_field_sha256 binds UTF-8 text, not JSON string serialization.
                digest = hashlib.sha256(field["text"].encode("utf-8")).hexdigest()
                for index, piece in enumerate(pieces):
                    if (piece["start"] != (pieces[index-1]["end"] if index else 0)
                            or piece["end"] - piece["start"] != len(piece["text"])
                            or piece["raw_field_sha256"] != digest):
                        raise ValueError("GPU source chunk spans/SHA differ")
    expected_score_count = sum(pair_chunk_counts[(ref,variant)] * len(row["option_values"])
                               for ref,row in by_ref.items() for variant in TASK.VARIANTS)
    if expected_score_count != completion["totals"]["option_chunk_count"] or len(chunks) != completion["totals"]["chunk_count"]:
        raise ValueError("GPU chunk completion totals mismatch")
    for kind in ("jev", "nli"):
        seen, per_option, classes, failed = set(), Counter(), defaultdict(Counter), defaultdict(bool)
        for score in rows(output / f"{kind}-chunk-scores.jsonl"):
            chunk_ref = score["chunk_ref"]
            ref,variant,_ = chunks[chunk_ref]
            option_key = (score["pair_ref"],score["variant"],score["option_index"])
            key = (*option_key,chunk_ref)
            if key in seen or option_key not in expected or option_key[:2] != (ref,variant):
                raise ValueError("chunk score binding is incomplete/duplicate/wrong")
            if score["state"] not in TASK.RELATIONS:
                raise ValueError("unknown numeric relation class")
            if kind == "jev":
                reconstruction = {"status":score["status"],"logits":score["logits"],
                                  "argmax_label":max(score["logits"],key=score["logits"].get) if score["logits"] else None}
                state, failure = TASK.jev_state(reconstruction)
            else:
                probabilities = TASK.NLI._probabilities(score)
                count = score["token_count_untruncated"]
                failure = probabilities is None or score["error_type"] is not None or (count or 513) > 512
                winner = max(probabilities,key=probabilities.get) if probabilities else "unknown"
                state = winner if not failure and probabilities[winner] >= TASK.NLI.THRESHOLD else "unknown"
            if state != score["state"]:
                raise ValueError("chunk relation does not follow frozen model probability/argmax policy")
            counts = score["token_counts"].values() if kind == "jev" and score["token_counts"] else [score.get("token_count_untruncated")]
            if any(count is not None and count > 512 for count in counts):
                raise ValueError("prepared GPU model input exceeded frozen token limit")
            seen.add(key)
            per_option[option_key] += 1
            classes[option_key][state] += 1
            failed[option_key] |= failure
        if len(seen) != expected_score_count or any(per_option[key] != pair_chunk_counts[key[:2]] for key in expected):
            raise ValueError("model did not independently score every option on every complete chunk")
        for option_key in expected:
            failure = failed[option_key] or issues[option_key[:2]] is not None
            if (failure != technical[option_key][kind]
                    or TASK.aggregate(list(classes[option_key].elements()),failure) != numerical[option_key][kind]):
                raise ValueError("numeric whole-option state differs from original chunk scores")
    result = {"schema_version":"sku-nextdata-description-gpu-verification-v1", "passed":True,
              "reference_labels_read":False, "production_adoption_allowed":False, "scope_status":"unproved",
              "pair_count":971, "paired_input_count":1942, "option_prediction_count_per_model":8536,
              "chunk_count":len(chunks), "option_chunk_count":expected_score_count, "state_counts":dict(state_counts),
              "gpu_runtime":completion["gpu_runtime"], "model_pins":completion["model_pins"],
              "input_sha256":frozen["public_input_sha256"], "bundle_manifest_sha256":packaged["manifest_sha256"],
              "archive_sha256":TASK.sha(archive), "artifact_receipt_sha256":TASK.sha(artifact_receipt),
              "prediction_files":{f"{kind}-{variant}":str(output / f"predictions-{kind}-{variant}.jsonl")
                                   for kind in ("jev","nli") for variant in TASK.VARIANTS}}
    TASK.write_json(output.parent / (output.name + "-VERIFICATION.json"), result)
    print(TASK.canonical(result))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive",type=Path,required=True)
    parser.add_argument("--artifact-receipt",type=Path,required=True)
    parser.add_argument("--bundle",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    verify(args.archive,args.artifact_receipt,args.bundle,args.output)


if __name__ == "__main__":
    main()
