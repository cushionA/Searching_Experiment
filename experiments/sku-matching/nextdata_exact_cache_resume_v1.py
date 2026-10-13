#!/usr/bin/env python3
"""Exact-input, first-record cache for a bounded continuation of GPU v2.

No labels, scoring, model changes, input normalization, or semantic retries.
An already-written occurrence keeps its own score even when another occurrence
of identical text has a different FP32 result. Only unwritten occurrences use
the first complete record for that exact model input and execution identity.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
from functools import lru_cache
import json
import math
from pathlib import Path
import shutil
import sqlite3
import sys

import kaggle_nextdata_table_alloption_v1 as TASK

JEV, NLI, GPU = TASK.JEV, TASK.NLI, TASK.GPU
KINDS = ("jev", "nli")
PINNED_INPUT_SHA = "49e1892ecf6de7d7091cf8ca233e336f8e142951e5ce8f78429a84e8826d9e51"
PINNED_ROOT_SHA = "258da1552d6d83ac9bf8162ca6b7e0b8fcac9b3681ee3a1cb34a072bb1bfb153"
PINNED_PROOF_SHA = "d5ace37e0bec59bbeff091e68deff5f66c7ea5772272ff1a34b844905f1813b5"
PINNED_COVERAGE_SHA = "c6b3f7dd7f9124404c1b91e4d076863de21a64733e11ad39eeeb11dc209d75c8"
PINNED_PREFLIGHT_SHA = "7966da886ee45ae5474d0ab6ebdc348f0f3125ceae7237302324e004df5dabe7"
PINNED_DRIVER_SHA = "8d90706914ac116f480cbd35bacdd429900430d0e7fbe74f2f70f3fb006e9c3e"
ORIGINAL_BUNDLE_SHA = "fff988ccaf69ca0612e0b32604597cdbca8f77074192fd573a9a892a56aeb9f1"
ORIGINAL_REF = "superbigzabuton/sku-nextdata-table-fp32-20261013-v1"
ORIGINAL_VERSION = 2
PINNED_PRIVATE_CLOSURE_SHA = "37f6855e7e7b70d7c4953562710493e5bb5624424b534d17dbe33f3d91e28d9a"
PINNED_SOURCE_JOIN_SHA = {
    "public-paired-inputs.jsonl": PINNED_INPUT_SHA,
    "membership.jsonl": "1b681018324eb01a63a46e1d6c0b39f3498c5f7a0f0ceeed979efb6f3f70b593",
    "row-plans.jsonl": "fce3bc26c8201ed3088e11d793676a661105708077aae300f9eca85cfc28d412",
    "tasks.jsonl": "b5602841212bdfe482bb841369181753fb037a4f6d79a83ff9c360dfd2a31506",
    "validation-bindings.jsonl": "b87c7878bed500334cbdc032cae709ce1efc4bc0cab4d3294c195ed2d9af4571",
}
PINNED_INDEX_GRAPH = {
    "requests": {"count": 231872, "sha256": "c28fc05696893daa56c28f32aadc1de0365a53811908251450401ceda3567a41"},
    "occurrences": {"count": 412052, "sha256": "d28c0565acd7e743ad2e593e34b981d2ccec3795862f45dbea29a04f6a01998c"},
}
ORIGINAL_CODE_SHA = {
    "kaggle_full_condition_choices_v1.py": "5b141fdad9fec7d02b21138b296fe43656c255b3da219b11193c29c07e735b79",
    "kaggle_nextdata_table_alloption_v1.py": PINNED_DRIVER_SHA,
    "trial_cpu_requirement_relations_v1.py": "d5bf039e5c799a2f703c1fd3e1ff3e73e3d495cea96178c8cee45dc04c120eb2",
    "trial_full_scoped_condition_choices_v1.py": "e6ffd64babfb3d1f96180b42cc877e2b5300d2ef2ac0a090276376a4fd2586de",
    "trial_generic_gliner_choice_v1.py": "e76683e22dcd1593300cb083fee93aadd3b7d4076789ffc9e5aa06d616d68197",
    "trial_generic_model_jev_v1.py": "100b43a09e41c0bce4de09cdfcc9bad6623ab41e4bb4db43dc9ed2d18a790863",
    "trial_generic_model_nli_v1.py": "97ac4af19534c2f26b4e7cf85961e7e331c9b6cdf9695d1b7d6cec019caf22eb",
    "trial_scoped_condition_gliner_v1.py": "9839d550865d058c4f17b98230ec525c90d5779bd0ab239b790a329d26e2504c",
    "trial_scoped_condition_nli_choice_v1.py": "e6d83ff283483454db20ccf335d7c93e2917453fd57520cbae4581e120901f54",
    "trial_scoped_condition_nli_v1.py": "7e7df5fe85d098ed167886684c5ca42483495b739e5298431ce5114ac7b75496",
}
HEADINGS = {
    "current_selected_product": "現在の実選択の商品情報の本文断片（全選択肢の説明を含む場合がある）",
    "current_all_options_not_actual_selection": "現在の商品に存在する全SKUの一覧断片（実選択を示すリストではない）",
    "reference_source_product": "参照元の商品情報の本文断片（現在の実選択の判定対象商品ではない）",
    "unspecified_body": "商品情報の本文断片",
}
RUNTIME_FIELDS = {
    "device": "cuda", "precision": "fp32", "autocast": False,
    "attention_implementation": "eager", "cuda_matmul_tf32": False,
    "cudnn_tf32": False, "float32_matmul_precision": "highest",
    "batch_size": 16, "threads": 4, "cpu_numeric_equality_assumed": False,
}
canonical, value_sha, sha, write_json = TASK.canonical, TASK.value_sha, TASK.sha, TASK.write_json


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


@lru_cache(maxsize=2)
def model_identity(kind):
    module = JEV if kind == "jev" else NLI
    return {"model_id": module.MODEL_ID, "revision": module.MODEL_REVISION,
            "parameter_count": module.FP32_PARAMETER_COUNT,
            "file_sha256": dict(JEV.JEV_FILE_SHA256 if kind == "jev" else NLI.NLI_FILE_SHA256),
            "runtime": RUNTIME_FIELDS, "policy": TASK.POLICY}


@lru_cache(maxsize=2)
def identity_sha(kind):
    return value_sha(model_identity(kind))


def validate_pins(kind, pins):
    expected = model_identity(kind)
    for name in ("model_id", "revision", "parameter_count", "file_sha256"):
        if pins.get(name) != expected[name]:
            raise ValueError("model pins differ: " + kind + "/" + name)
    if (pins.get("device"), pins.get("precision")) != ("cuda", "fp32"):
        raise ValueError("numeric cache requires actual CUDA FP32 provenance")


def validate_runtime(runtime):
    if any(runtime.get(k) != v for k, v in RUNTIME_FIELDS.items()):
        raise ValueError("frozen CUDA/eager/batch16 runtime differs")
    if not isinstance(runtime.get("gpu_name"), str) or not runtime["gpu_name"]:
        raise ValueError("actual CUDA device attestation is required")


def validate_loaded_original_code():
    for name, expected in ORIGINAL_CODE_SHA.items():
        module = NLI._PINNED if name == "trial_cpu_requirement_relations_v1.py" else sys.modules.get(Path(name).stem)
        if module is None or sha(module.__file__) != expected:
            raise ValueError("loaded original numeric/input code differs: " + name)


def validate_private_closure(closure):
    if value_sha(closure) != PINNED_PRIVATE_CLOSURE_SHA:
        raise ValueError("private whole-product closure differs from frozen original source join")
    if closure.get("source_file_sha256") != PINNED_SOURCE_JOIN_SHA:
        raise ValueError("private closure source provenance differs")
    for product in closure["products"]:
        projected = {k: v for k, v in product.items() if k != "closure_sha256"}
        if value_sha(projected) != product["closure_sha256"]:
            raise ValueError("product closure digest differs from its full case/pair/task vector")


def validate_worker_cache_origin(plan, freeze, state_manifest_sha):
    identities = {k: model_identity(k) for k in KINDS}
    if (plan.get("original_input_sha256"), plan.get("original_bundle_manifest_sha256"), plan.get("model_identities")) != (
            PINNED_INPUT_SHA, ORIGINAL_BUNDLE_SHA, identities):
        raise ValueError("worker plan does not bind the immutable original task")
    if "local_product_receipts" in plan or plan.get("labels_read") is not False:
        raise ValueError("worker plan includes private source joins or labels")
    expected = {"original_input_sha256": PINNED_INPUT_SHA,
                "original_bundle_manifest_sha256": ORIGINAL_BUNDLE_SHA,
                "state_bundle_manifest_sha256": state_manifest_sha, "index_sha256": plan["index_sha256"],
                "model_identities": identities, "labels_read": False}
    if any(freeze.get(k) != v for k, v in expected.items()):
        raise ValueError("new cache does not bind its actual frozen worker source/index")
    if not 0 < freeze.get("max_seconds", 0) <= 6000:
        raise ValueError("new cache was not a bounded execution")
    shard = next((s for s in plan["shards"] if s["shard_id"] == freeze.get("shard_id")), None)
    if shard is None:
        raise ValueError("new cache freeze has an unrecognized bounded shard")
    keys = [tuple(key) for key in shard["request_keys"]]
    if len(keys) != len(set(keys)) or len(keys) != shard["unique_request_count"] or len(keys) > plan["max_unique_requests_per_shard"]:
        raise ValueError("new cache shard repeats/omits requests or exceeds its frozen budget")
    return set(keys)


def restore_chunks(row, variant, coverage):
    """Replay the exact old ranges; do not split or tokenize the text again."""
    if (coverage["pair_ref"], coverage["variant"], coverage["option_count"]) != (
            row["pair_ref"], variant, len(row["option_values"])):
        raise ValueError("preflight coverage identity differs")
    ranges, issue = coverage["chunk_source_ranges"], coverage.get("input_issue")
    if issue is not None:
        if ranges or coverage["chunk_count"] != 0:
            raise ValueError("technical input issue has unexpected chunks")
        return [], issue
    fields = row["variants"][variant]["body_fields"]
    cursor = Counter()
    by_ref = {field["field_ref"]: field for field in fields}
    chunks = []
    for index, span in enumerate(ranges):
        field_ref = span["field_ref"]
        if field_ref is None:
            if len(ranges) != 1 or any(f["text"] for f in fields) or span["chunk_ref"] != f"{row['pair_ref']}:{variant}:metadata":
                raise ValueError("metadata-only chunk differs")
            chunks.append({**span, "text": "", "premise": TASK.metadata(row)})
            continue
        if field_ref not in by_ref:
            raise ValueError("preflight field is absent from exact source")
        field, start, end = by_ref[field_ref], span["start"], span["end"]
        text, role = field["text"], field.get("role", "unspecified_body")
        if (type(start) is not int or type(end) is not int or start != cursor[field_ref]
                or not start < end <= len(text) or span["chunk_ref"] != f"{row['pair_ref']}:{variant}:{index}"
                or span["raw_field_sha256"] != hashlib.sha256(text.encode("utf-8")).hexdigest()):
            raise ValueError("preflight chunk does not cover the exact raw character range")
        cursor[field_ref] = end
        piece = text[start:end]
        chunks.append({**span, "text": piece, "role": role,
                       "premise": TASK.metadata(row) + "\n" + HEADINGS[role] + "：\n" + piece})
    expected_order = [f["field_ref"] for f in fields if f["text"]]
    actual_order = list(dict.fromkeys(s["field_ref"] for s in ranges if s["field_ref"] is not None))
    if actual_order != expected_order or any(cursor[f["field_ref"]] != len(f["text"]) for f in fields):
        raise ValueError("preflight omits or reorders an original body field")
    if len(chunks) != coverage["chunk_count"]:
        raise ValueError("preflight chunk count differs")
    return chunks, None


class Catalog:
    def __init__(self, input_file, root_freeze, proof, coverage_file, preflight_receipt):
        validate_loaded_original_code()
        for path, expected in ((input_file, PINNED_INPUT_SHA), (root_freeze, PINNED_ROOT_SHA),
                               (proof, PINNED_PROOF_SHA), (coverage_file, PINNED_COVERAGE_SHA),
                               (preflight_receipt, PINNED_PREFLIGHT_SHA), (Path(TASK.__file__), PINNED_DRIVER_SHA)):
            if sha(path) != expected:
                raise ValueError("immutable original source/preflight SHA differs: " + Path(path).name)
        frozen = json.loads(Path(root_freeze).read_text(encoding="utf-8"))
        if frozen.get("root_verified") is not True:
            raise ValueError("authoritative ROOT freeze required")
        self.rows = TASK.load_rows(input_file, root_freeze, proof)
        self.by_ref = {row["pair_ref"]: row for row in self.rows}
        self.coverage = {(r["pair_ref"], r["variant"]): r for r in read_jsonl(coverage_file)}
        if set(self.coverage) != {(r["pair_ref"], v) for r in self.rows for v in TASK.VARIANTS}:
            raise ValueError("coverage does not contain the frozen paired population")
        self.chunks = {}
        totals = Counter()
        for row in self.rows:
            for variant in TASK.VARIANTS:
                chunks, issue = restore_chunks(row, variant, self.coverage[row["pair_ref"], variant])
                self.chunks[row["pair_ref"], variant] = (chunks, issue)
                totals.update(paired_input_count=1, option_prediction_count=len(row["option_values"]),
                              chunk_count=len(chunks), option_chunk_count=len(chunks) * len(row["option_values"]),
                              technical_unknown_pair_count=int(issue is not None))
        expected = {"paired_input_count": 1942, "option_prediction_count": 8536, "chunk_count": 44099,
                    "option_chunk_count": 206026, "technical_unknown_pair_count": 0}
        if dict(totals) != expected:
            raise ValueError("immutable preflight counts differ")
        self.totals = dict(totals)

    def request(self, kind, locator):
        ref, variant, option_index, chunk_index = locator
        row = self.by_ref[ref]
        chunks, issue = self.chunks[ref, variant]
        if issue is not None or type(option_index) is not int or not 0 <= option_index < len(row["option_values"]):
            raise ValueError("invalid model request locator")
        chunk, option = chunks[chunk_index], row["option_values"][option_index]
        request_id = chunk["chunk_ref"] + f":option-{option_index}"
        if kind == "jev":
            return TASK.choice_request(row, option, chunk["premise"], request_id)
        return {"id": request_id, "premise": chunk["premise"],
                "hypothesis": TASK.HYPOTHESIS.format(axis_name=row["axis_name"], option=option)}

    def occurrences(self):
        for row in self.rows:
            for variant in TASK.VARIANTS:
                chunks, _ = self.chunks[row["pair_ref"], variant]
                for option_index in range(len(row["option_values"])):
                    for chunk_index, chunk in enumerate(chunks):
                        yield [row["pair_ref"], variant, option_index, chunk_index], chunk["chunk_ref"]


def request_identity(kind, request):
    if kind == "jev":
        legacy_key = JEV.request_key(request["question"], request["state"], request["choices"])
        context = JEV.render_context(request["question"], request["state"])
        texts = [[context, JEV.render_candidate(c)] for c in request["choices"]]
    elif kind == "nli":
        legacy_key = NLI.pair_key(request["premise"], request["hypothesis"])
        texts = [request["premise"], request["hypothesis"]]
    else:
        raise ValueError("unknown model kind")
    text_sha = value_sha(texts)
    return {"exact_key": value_sha([identity_sha(kind), legacy_key, text_sha]),
            "legacy_key": legacy_key, "model_text_sha256": text_sha}


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def build_index(path, catalog):
    if Path(path).exists():
        raise FileExistsError(path)
    db = connect(path)
    db.executescript("""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE requests (kind TEXT, exact_key TEXT, legacy_key TEXT, text_sha TEXT,
            locator TEXT, ordinal INTEGER, PRIMARY KEY(kind,exact_key));
        CREATE TABLE occurrences (kind TEXT, pair_ref TEXT, variant TEXT, option_index INTEGER,
            chunk_ref TEXT, exact_key TEXT, ordinal INTEGER,
            PRIMARY KEY(kind,pair_ref,variant,option_index,chunk_ref));
        CREATE INDEX occurrence_keys ON occurrences(kind,exact_key);
        CREATE INDEX occurrence_refs ON occurrences(pair_ref);
        CREATE TABLE saved (kind TEXT, pair_ref TEXT, variant TEXT, option_index INTEGER,
            chunk_ref TEXT, exact_key TEXT, score TEXT, origin TEXT,
            PRIMARY KEY(kind,pair_ref,variant,option_index,chunk_ref));
        CREATE TABLE cache (kind TEXT, exact_key TEXT, score TEXT, origin TEXT,
            PRIMARY KEY(kind,exact_key));
    """)
    db.execute("INSERT INTO metadata VALUES (?,?)", ("identity", canonical({k: model_identity(k) for k in KINDS})))
    db.execute("INSERT INTO metadata VALUES (?,?)", ("input_sha256", PINNED_INPUT_SHA))
    calls = defaultdict(set)
    for ordinal, (locator, chunk_ref) in enumerate(catalog.occurrences()):
        for kind in KINDS:
            identity = request_identity(kind, catalog.request(kind, locator))
            ref, variant, option_index, _ = locator
            db.execute("INSERT OR IGNORE INTO requests VALUES (?,?,?,?,?,?)",
                       (kind, identity["exact_key"], identity["legacy_key"], identity["model_text_sha256"], canonical(locator), ordinal))
            db.execute("INSERT INTO occurrences VALUES (?,?,?,?,?,?,?)",
                       (kind, canonical(ref), variant, option_index, chunk_ref, identity["exact_key"], ordinal))
            calls[kind, ref, variant].add(identity["exact_key"])
    db.commit()
    statistics = {}
    for kind in KINDS:
        occurrences = db.execute("SELECT count(*) FROM occurrences WHERE kind=?", (kind,)).fetchone()[0]
        globally_unique = db.execute("SELECT count(*) FROM requests WHERE kind=?", (kind,)).fetchone()[0]
        old_inferred_unique = sum(len(keys) for (k, _, _), keys in calls.items() if k == kind)
        statistics[kind] = {"original_occurrence_requests": occurrences,
                            "old_intra_call_unique_requests": old_inferred_unique,
                            "old_intra_call_duplicate_requests": occurrences - old_inferred_unique,
                            "global_exact_unique_requests": globally_unique,
                            "additional_cross_call_saved_requests": old_inferred_unique - globally_unique}
    db.close()
    return statistics


def index_graph(db):
    result = {}
    for table, order in (("requests", "kind,exact_key"),
                         ("occurrences", "kind,ordinal,pair_ref,variant,option_index,chunk_ref")):
        digest, count = hashlib.sha256(), 0
        for row in db.execute(f"SELECT * FROM {table} ORDER BY {order}"):
            digest.update(canonical(list(row)).encode("utf-8"))
            digest.update(b"\n")
            count += 1
        result[table] = {"count": count, "sha256": digest.hexdigest()}
    return result


def verify_index_identity(db):
    validate_loaded_original_code()
    stored = dict(db.execute("SELECT key,value FROM metadata"))
    expected = {"identity": canonical({k: model_identity(k) for k in KINDS}), "input_sha256": PINNED_INPUT_SHA}
    if any(stored.get(k) != v for k, v in expected.items()) or set(stored) - set(expected) - {"v2_origin_status", "v2_raw_snapshot_sha256"}:
        raise ValueError("resume index execution identity differs")
    if index_graph(db) != PINNED_INDEX_GRAPH:
        raise ValueError("resume index modifies/omits an original exact request or occurrence")


def verify_resume_ready(db):
    """A planning-only index can never start numeric continuation of v2."""
    verify_index_identity(db)
    stored = dict(db.execute("SELECT key,value FROM metadata"))
    if stored.get("v2_origin_status") != "frozen_terminal_v2_imported" or len(stored.get("v2_raw_snapshot_sha256", "")) != 64:
        raise ValueError("original fixed-v2 terminal raw first scores must be imported before continuation")


def validate_merge_base(initial, target):
    """No merge may lose, replace, or re-age a previously written first score."""
    verify_resume_ready(initial)
    verify_resume_ready(target)
    if dict(initial.execute("SELECT key,value FROM metadata")) != dict(target.execute("SELECT key,value FROM metadata")):
        raise ValueError("merge source and target have different original first-pass origins")
    for row in initial.execute("SELECT * FROM cache"):
        found = target.execute("SELECT * FROM cache WHERE kind=? AND exact_key=?", (row["kind"], row["exact_key"])).fetchone()
        if found is None or tuple(found) != tuple(row):
            raise ValueError("merge target loses/replaces an already-written first cache record")
    for row in initial.execute("SELECT * FROM saved"):
        found = target.execute("SELECT * FROM saved WHERE kind=? AND pair_ref=? AND variant=? AND option_index=? AND chunk_ref=?",
                (row["kind"], row["pair_ref"], row["variant"], row["option_index"], row["chunk_ref"])).fetchone()
        if found is None or tuple(found) != tuple(row):
            raise ValueError("merge target loses/replaces an original occurrence record")


def checked_request(db, catalog, kind, key):
    entry = db.execute("SELECT * FROM requests WHERE kind=? AND exact_key=?", (kind, key)).fetchone()
    if entry is None:
        raise ValueError("request is outside the frozen population")
    request = catalog.request(kind, json.loads(entry["locator"]))
    identity = request_identity(kind, request)
    if (identity["exact_key"], identity["legacy_key"], identity["model_text_sha256"]) != (key, entry["legacy_key"], entry["text_sha"]):
        raise ValueError("index does not reconstruct the exact old model text")
    return request, identity


def validate_score(kind, raw, request, identity):
    """Check the old state against its original numeric record, never improve it."""
    if raw.get("cache_key") != identity["legacy_key"]:
        raise ValueError("record cache key differs from exact original input")
    if kind == "jev":
        labels = [c["label"] for c in request["choices"]]
        record = deepcopy(raw)
        for field in ("logits", "probabilities", "token_counts"):
            mapping = record.get(field)
            if isinstance(mapping, dict):
                if any(k not in labels for k in mapping):
                    raise ValueError("unknown JEV score label")
                record[field] = {label: mapping[label] for label in labels if label in mapping}
        if record["status"] == "ok":
            winner = max(range(len(labels)), key=lambda index: record["logits"][labels[index]])
            record.update(argmax_index=winner, argmax_label=labels[winner],
                          argmax_probability=record["probabilities"][labels[winner]])
        else:
            record.update(argmax_index=None, argmax_label=None, argmax_probability=None)
        JEV._validate_scores(record, request)
        expected, failed = TASK.jev_state(record)
        if record["status"] == "ok" and record.get("error_type") is not None:
            raise ValueError("successful JEV record has a technical error")
    else:
        probabilities = NLI._probabilities(raw)
        count = raw.get("token_count_untruncated")
        if count is not None and (type(count) is not int or count < 0):
            raise ValueError("invalid untruncated NLI token count")
        if probabilities is not None and count is not None and count <= 512:
            # Preserve original JSON key order for ties; ties never reach .90.
            # Original relation_probabilities insertion order; canonical JSON
            # sorts its keys, so JSON order cannot reconstruct the original tie.
            winner = max(("support", "conflict", "unknown"), key=raw["probabilities"].get)
            confidence = probabilities[winner]
        else:
            winner, confidence = "unknown", None
        if raw.get("predicted_class") != winner or raw.get("predicted_confidence") != confidence:
            raise ValueError("NLI original prediction disagrees with original probabilities")
        expected = winner if confidence is not None and confidence >= NLI.THRESHOLD else "unknown"
        failed = probabilities is None or raw.get("error_type") is not None or (count or 513) > 512
    if raw.get("state") != expected:
        raise ValueError("stored state differs from frozen original decision rule")
    return failed


def put_cache(db, kind, key, score, origin):
    """First record wins, including technical unknown; no best-of selection."""
    previous = db.execute("SELECT score FROM cache WHERE kind=? AND exact_key=?", (kind, key)).fetchone()
    db.execute("INSERT OR IGNORE INTO cache VALUES (?,?,?,?)", (kind, key, canonical(score), canonical(origin)))
    def numeric_payload(record):
        return {k: v for k, v in record.items() if k not in {"pair_ref", "variant", "option_index", "chunk_ref"}}
    return previous is not None and numeric_payload(json.loads(previous["score"])) != numeric_payload(score)


def resolve_score(db, kind, occurrence):
    ref, variant, option_index, chunk_ref = occurrence
    parameters = (kind, canonical(ref), variant, option_index, chunk_ref)
    own = db.execute("SELECT score,origin FROM saved WHERE kind=? AND pair_ref=? AND variant=? AND option_index=? AND chunk_ref=?", parameters).fetchone()
    if own is not None:
        return json.loads(own["score"]), {"resolution": "original_occurrence", "origin": json.loads(own["origin"])}
    row = db.execute("SELECT exact_key FROM occurrences WHERE kind=? AND pair_ref=? AND variant=? AND option_index=? AND chunk_ref=?", parameters).fetchone()
    if row is None:
        raise ValueError("unknown original occurrence")
    cached = db.execute("SELECT score,origin FROM cache WHERE kind=? AND exact_key=?", (kind, row["exact_key"])).fetchone()
    if cached is None:
        return None, {"resolution": "unwritten", "exact_key": row["exact_key"]}
    return json.loads(cached["score"]), {"resolution": "first_exact_input_record", "origin": json.loads(cached["origin"])}


def snapshot_original_v2(raw_dir, receipt_file, output_dir):
    """Copy first; reject ambiguous partial tails rather than silently retrying."""
    receipt = json.loads(Path(receipt_file).read_text(encoding="utf-8"))
    if (receipt.get("remote_ref"), receipt.get("version")) != (ORIGINAL_REF, ORIGINAL_VERSION):
        raise ValueError("only fixed original v2 output may seed this importer")
    if receipt.get("terminal_status") not in {"complete", "error", "cancelled", "timeout"}:
        raise ValueError("running/mutable v2 output cannot be imported")
    required = {"EXECUTION_FREEZE.json", "jev-chunk-scores.jsonl", "nli-chunk-scores.jsonl", "chunks.jsonl"}
    inventory = receipt.get("files", {})
    if not required <= set(inventory):
        raise ValueError("ROOT fixed-version raw file inventory is incomplete")
    allowed = required | {"COMPLETION.json", "option-predictions.jsonl", *[
        f"predictions-{kind}-{variant}.jsonl" for kind in KINDS for variant in TASK.VARIANTS]}
    if not set(inventory) <= allowed:
        raise ValueError("recovery inventory includes a file outside original numeric outputs")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    captured = {}
    for name, pin in inventory.items():
        rel = Path(name)
        source = Path(raw_dir) / rel
        if rel.is_absolute() or ".." in rel.parts or len(rel.parts) != 1 or source.is_symlink():
            raise ValueError("raw snapshot requires ordinary top-level files")
        if source.stat().st_size != pin.get("size_bytes") or sha(source) != pin.get("sha256"):
            raise ValueError("ROOT recovered original bytes differ: " + name)
        target = output / rel
        shutil.copyfile(source, target)
        if sha(target) != pin["sha256"] or sha(source) != pin["sha256"]:
            raise ValueError("original output changed while being snapshotted")
        captured[name] = pin
    shutil.copyfile(receipt_file, output / "ROOT_RECOVERY_RECEIPT.json")
    tails = []
    for name in required - {"EXECUTION_FREEZE.json"}:
        path = output / name
        with path.open("rb") as handle:
            if path.stat().st_size:
                handle.seek(-1, 2)
                if handle.read(1) != b"\n":
                    tails.append(name)
    write_json(output / "RAW_SNAPSHOT.json", {"files": captured, "source_receipt_sha256": sha(receipt_file),
               "remote_ref": ORIGINAL_REF, "version": 2, "partial_tail_files": tails,
               "original_bytes_preserved": True, "semantic_retry_allowed": False})
    if tails:
        raise ValueError("original raw bytes preserved; incomplete first-score tail requires ROOT review: " + ",".join(tails))
    return output


def import_original(db, catalog, snapshot):
    raw_receipt = json.loads((Path(snapshot) / "RAW_SNAPSHOT.json").read_text(encoding="utf-8"))
    for name, pin in raw_receipt["files"].items():
        path = Path(snapshot) / name
        if path.stat().st_size != pin["size_bytes"] or sha(path) != pin["sha256"]:
            raise ValueError("original snapshotted first-score bytes changed")
    if raw_receipt.get("source_receipt_sha256") != sha(Path(snapshot) / "ROOT_RECOVERY_RECEIPT.json"):
        raise ValueError("ROOT fixed-version recovery receipt changed")
    freeze = json.loads((Path(snapshot) / "EXECUTION_FREEZE.json").read_text(encoding="utf-8"))
    for name, expected in TASK.POLICY.items():
        if freeze.get(name) != expected:
            raise ValueError("original execution policy differs: " + name)
    expected = {"input_sha256": PINNED_INPUT_SHA, "root_population_freeze_sha256": PINNED_ROOT_SHA,
                "description_sourceproof_sha256": PINNED_PROOF_SHA, "bundle_manifest_sha256": ORIGINAL_BUNDLE_SHA,
                "pair_count": 971, "expected_option_prediction_count": 8536}
    if any(freeze.get(k) != v for k, v in expected.items()):
        raise ValueError("original v2 execution does not bind the frozen population")
    validate_runtime(freeze["gpu_runtime"])
    for kind in KINDS:
        validate_pins(kind, freeze["model_pins"][kind])
    expected_chunks = {chunk["chunk_ref"]: chunk for chunks, _ in catalog.chunks.values() for chunk in chunks}
    seen_chunks = set()
    for chunk in read_jsonl(Path(snapshot) / "chunks.jsonl"):
        expected_chunk = expected_chunks.get(chunk["chunk_ref"])
        if expected_chunk is None or chunk["chunk_ref"] in seen_chunks:
            raise ValueError("original chunk identity is absent or duplicated")
        if chunk != {k: v for k, v in expected_chunk.items() if k != "premise"}:
            raise ValueError("original written chunk does not replay exact preflight source")
        seen_chunks.add(chunk["chunk_ref"])
    counts = Counter()
    for kind in KINDS:
        path = Path(snapshot) / f"{kind}-chunk-scores.jsonl"
        source_sha = sha(path)
        for line_number, raw in enumerate(read_jsonl(path), 1):
            occurrence = (kind, canonical(raw["pair_ref"]), raw["variant"], raw["option_index"], raw["chunk_ref"])
            row = db.execute("SELECT exact_key FROM occurrences WHERE kind=? AND pair_ref=? AND variant=? AND option_index=? AND chunk_ref=?", occurrence).fetchone()
            if row is None or raw["chunk_ref"] not in seen_chunks:
                raise ValueError("original score is outside frozen/written source chunks")
            request, identity = checked_request(db, catalog, kind, row["exact_key"])
            failed = validate_score(kind, raw, request, identity)
            origin = {"type": "original_v2", "remote_ref": ORIGINAL_REF, "version": 2,
                      "file_sha256": source_sha, "line_number": line_number}
            db.execute("INSERT INTO saved VALUES (?,?,?,?,?,?,?,?)", (*occurrence, row["exact_key"], canonical(raw), canonical(origin)))
            different = put_cache(db, kind, row["exact_key"], raw, origin)
            counts[f"{kind}_original_occurrences"] += 1
            counts[f"{kind}_technical_first_records"] += failed
            counts[f"{kind}_duplicate_exact_key_different_record"] += different
    # v2 has already logged >8042 complete score occurrences per model. Empty
    # or earlier-version artifacts would discard known first-pass outputs.
    if any(counts[f"{kind}_original_occurrences"] < 8042 for kind in KINDS):
        raise ValueError("recovered v2 output omits scores already observed in its fixed running trace")
    db.execute("INSERT INTO metadata VALUES (?,?)", ("v2_origin_status", "frozen_terminal_v2_imported"))
    db.execute("INSERT INTO metadata VALUES (?,?)", ("v2_raw_snapshot_sha256", sha(Path(snapshot) / "RAW_SNAPSHOT.json")))
    db.commit()
    return dict(counts)


def product_closure(public_input, membership_file, row_plans_file, tasks_file, bindings_file):
    """Read source joins only. IDs stay local, never in the model worker bundle."""
    by_product = {}
    case_product = {}
    original_tasks = {}
    for path in (public_input, membership_file, row_plans_file, tasks_file, bindings_file):
        if sha(path) != PINNED_SOURCE_JOIN_SHA.get(Path(path).name):
            raise ValueError("frozen original source join bytes differ: " + Path(path).name)
    for row in read_jsonl(membership_file):
        product, case = row["au_product_id"], row["case_id"]
        if case in case_product:
            raise ValueError("duplicate source case membership")
        case_product[case] = product
        by_product.setdefault(product, {"product_id": product, "case_ids": [], "row_keys": [], "task_ids": [], "pair_refs": set()})["case_ids"].append(case)
    for path, destination, id_key in ((row_plans_file, "row_keys", "au_row_key"), (tasks_file, "task_ids", "task_id")):
        for row in read_jsonl(path):
            if case_product.get(row["case_id"]) != row["au_product_id"]:
                raise ValueError("source case/product closure changed")
            by_product[row["au_product_id"]][destination].append([row["case_id"], row[id_key]])
            if destination == "task_ids":
                if row["task_id"] in original_tasks:
                    raise ValueError("duplicate original task identity")
                original_tasks[row["task_id"]] = (row["case_id"], row["au_product_id"])
    pair_to_ref = {r["pair_id"]: r["pair_ref"] for r in read_jsonl(public_input)}
    matched = set()
    for binding in read_jsonl(bindings_file):
        pair_id = binding["pair_id"]
        if pair_id not in pair_to_ref:
            continue
        task = binding["task_binding"]
        if case_product.get(task["case_id"]) != task["au_product_id"]:
            raise ValueError("source model pair joins a different case/product")
        if original_tasks.get(task["task_id"]) != (task["case_id"], task["au_product_id"]):
            raise ValueError("source model pair is absent from original task closure")
        by_product[task["au_product_id"]]["pair_refs"].add(pair_to_ref[pair_id])
        matched.add(pair_id)
    if matched != set(pair_to_ref):
        raise ValueError("public model population has orphan source cases")
    products = []
    for product in sorted(by_product):
        value = by_product[product]
        value["pair_refs"] = sorted(value["pair_refs"])
        value["closure_sha256"] = value_sha(value)
        products.append(value)
    totals = {"product_count": len(products), "case_count": sum(len(p["case_ids"]) for p in products),
              "row_count": sum(len(p["row_keys"]) for p in products), "task_count": sum(len(p["task_ids"]) for p in products),
              "ml_product_count": sum(bool(p["pair_refs"]) for p in products),
              "literal_only_product_count": sum(not p["pair_refs"] for p in products)}
    if totals != {"product_count": 46, "case_count": 569, "row_count": 13159, "task_count": 62252,
                  "ml_product_count": 25, "literal_only_product_count": 21}:
        raise ValueError("original all-product/case/row/task closure differs")
    closure = {"products": products, "totals": totals,
            "source_file_sha256": {str(Path(path).name): sha(path) for path in
             (public_input, membership_file, row_plans_file, tasks_file, bindings_file)},
            "labels_read": False, "worker_export_allowed": False}
    validate_private_closure(closure)
    return closure


def bounded_plan(db, closure, max_unique_requests=20000):
    """Deterministic whole-product planning with deferred closure for large ones."""
    if type(max_unique_requests) is not int or max_unique_requests <= 0:
        raise ValueError("positive bounded request budget required")
    available = {(row["kind"], row["exact_key"]) for row in db.execute("SELECT kind,exact_key FROM cache")}
    required_by_product = {}
    for product in closure["products"]:
        required = set()
        for ref in product["pair_refs"]:
            required.update((r["kind"], r["exact_key"]) for r in db.execute(
                "SELECT DISTINCT kind,exact_key FROM occurrences WHERE pair_ref=?", (canonical(ref),)))
        required_by_product[product["product_id"]] = required
    first_ordinals = {(r["kind"], r["exact_key"]): r["ordinal"] for r in db.execute("SELECT kind,exact_key,ordinal FROM requests")}
    def ordered(keys):
        return sorted(keys, key=lambda key: (first_ordinals[key], KINDS.index(key[0]), key[1]))
    # Cost-only order permits a <=2h whole-product pilot without label selection.
    product_order = sorted(closure["products"], key=lambda p: (len(required_by_product[p["product_id"]] - available), str(p["product_id"])))
    shards, scheduled = [], set(available)
    key_shard = {}
    current_keys, current_groups = [], []
    def flush():
        nonlocal current_keys, current_groups
        if not current_keys:
            return
        shards.append({"shard_id": len(shards), "request_keys": [[k, v] for k, v in current_keys],
                       "unique_request_count": len(current_keys),
                       "jev_sequence_count": 3 * sum(k == "jev" for k, _ in current_keys),
                       "nli_sequence_count": sum(k == "nli" for k, _ in current_keys),
                       "product_group_refs": current_groups,
                       "product_publication_deferred_until_all_required_keys": True})
        current_keys, current_groups = [], []
    product_receipts = []
    for product in product_order:
        required = required_by_product[product["product_id"]]
        missing = ordered(required - scheduled)
        if missing and len(current_keys) + len(missing) > max_unique_requests:
            # Keep ordinary whole products together; oversized products span
            # shards and cannot publish until their complete closure is ready.
            flush()
        group_ref = value_sha(["product-closure", product["product_id"]])
        for key in missing:
            if len(current_keys) == max_unique_requests:
                flush()
            current_keys.append(key)
            if group_ref not in current_groups:
                current_groups.append(group_ref)
            key_shard[key] = len(shards)
            scheduled.add(key)
        shard_ids = sorted({key_shard[key] for key in missing})
        required_shard_ids = sorted({key_shard[key] for key in required - available})
        product_receipts.append({"product_id": product["product_id"], "closure_sha256": product["closure_sha256"],
                                 "case_count": len(product["case_ids"]), "row_count": len(product["row_keys"]),
                                 "task_count": len(product["task_ids"]), "pair_count": len(product["pair_refs"]),
                                 "required_unique_request_count": len(required),
                                 "new_unique_request_count": len(missing), "new_shard_ids": shard_ids,
                                 "required_shard_ids": required_shard_ids,
                                 "ready_before_new_inference": required <= available})
    flush()
    all_keys = set(first_ordinals)
    if scheduled != all_keys or any(s["unique_request_count"] > max_unique_requests for s in shards):
        raise ValueError("bounded shards omit an original request or exceed the budget")
    return {"schema_version": "sku-nextdata-exact-cache-product-closure-plan-v1", "labels_read": False,
            "semantics_changed": False, "production_adoption_allowed": False,
            "max_unique_requests_per_shard": max_unique_requests, "totals": closure["totals"],
            "already_cached_unique_request_count": len(available), "planned_unique_request_count": len(all_keys - available),
            "shards": shards, "local_product_receipts": product_receipts,
            "product_order": "ascending uncomputed unique request count, then source product ID; no labels",
            "worker_export_excluded_fields": ["local_product_receipts"]}
