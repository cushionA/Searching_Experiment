#!/usr/bin/env python3
"""Variant-scoped immutable product-condition decision cache, without inference.

The source plan SHA is provenance, not a lookup restriction. Stable product
model-input keys allow reuse when only wanted SKU values or fan-out counts
change. Positive, alternative, unknown and tied outcomes share the same exact
validation contract as the frozen condition-sharing helper.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

import product_condition_sharing_v1 as sharing

VERSION = "product-condition-decision-cache-v1"


def _text(value, field):
    if not isinstance(value, str) or not value:
        raise ValueError("nonempty string required: " + field)
    return value


def _groups(plan):
    if (plan.get("schema_version") != sharing.VERSION or plan.get("plan_sha256") !=
            sharing.digest({k: v for k, v in plan.items() if k != "plan_sha256"})):
        raise ValueError("immutable sharing plan SHA differs")
    groups = {}
    for group in plan["groups"]:
        identity = _text(group.get("group_id"), "group_id")
        if identity in groups:
            raise ValueError("duplicate current group_id")
        _text(group.get("model_cache_key"), "model_cache_key")
        document = plan["documents"][group["target_document_id"]]
        sharing._validate_document(document)
        if (group["target_document_sha256"] != document["document_sha256"] or
                group["canonical_au_url"] != sharing.canonical_au_url(document)):
            raise ValueError("current group document version/fixed AU URL differs")
        groups[identity] = group
    return groups


def effective_key(variant, model_cache_key):
    return sharing.digest({"variant": _text(variant, "variant"),
                           "group_model_cache_key": _text(model_cache_key, "model_cache_key")})


def build_cache(plan, group_results, variant):
    """Validate all frozen group decisions and preserve their exact originals."""
    _text(variant, "variant")
    groups = _groups(plan)
    if not isinstance(group_results, dict) or set(group_results) != set(groups):
        raise ValueError("group results must cover exactly every source group")
    entries = {}
    for identity, group in groups.items():
        result = group_results[identity]
        if not isinstance(result, dict) or result.get("variant") != variant:
            raise ValueError("group result variant must equal the decision-cache namespace")
        if result.get("group_id", identity) != identity:
            raise ValueError("source result group_id differs")
        winner, refs, reason, tie = sharing._group_outcome(group, result, plan["documents"])
        key = effective_key(variant, group["model_cache_key"])
        if key in entries:
            raise ValueError("duplicate effective decision-cache input")
        entry = {
            "effective_decision_cache_key": key, "variant": variant,
            "source_group_id": identity, "source_group_sha256": group["group_sha256"],
            "source_group_model_cache_key": group["model_cache_key"],
            "source_plan_sha256": plan["plan_sha256"],
            "original_group_result": copy.deepcopy(result),
            "original_group_result_sha256": sharing.digest(result),
            "validated_outcome": {"winning_value": winner, "quote_refs": refs, "reason": reason, "tie": tie},
        }
        entry["entry_sha256"] = sharing.digest(entry)
        entries[key] = entry
    cache = {
        "schema_version": VERSION, "variant": variant,
        "source_plan_sha256": plan["plan_sha256"],
        "source_model_identity": copy.deepcopy(plan["model_identity"]),
        "source_content_version": plan["content_version"],
        "source_group_results_sha256": sharing.digest(group_results),
        "entries": entries, "entry_count": len(entries),
        "labels_read": False, "model_inference_run": False,
        "actual_saved_inference_count": 0, "production_adoption_allowed": False,
        "lookup_policy": "variant plus exact group model-input key; source plan SHA is provenance only",
    }
    cache["cache_sha256"] = sharing.digest(cache)
    return cache


def _validate_cache(cache):
    if (not isinstance(cache, dict) or cache.get("schema_version") != VERSION or
            cache.get("cache_sha256") != sharing.digest({k: v for k, v in cache.items() if k != "cache_sha256"})):
        raise ValueError("immutable decision-cache SHA differs")
    _text(cache.get("variant"), "cached variant")
    if (cache.get("labels_read") is not False or cache.get("model_inference_run") is not False or
            cache.get("production_adoption_allowed") is not False or cache.get("actual_saved_inference_count") != 0):
        raise ValueError("decision cache cannot claim model execution, labels or measured savings")
    entries = cache.get("entries")
    if not isinstance(entries, dict) or cache.get("entry_count") != len(entries):
        raise ValueError("decision cache entry coverage/count differs")
    originals = {}
    for key, entry in entries.items():
        if (entry.get("entry_sha256") != sharing.digest({k: v for k, v in entry.items() if k != "entry_sha256"}) or
                key != entry.get("effective_decision_cache_key") or entry.get("variant") != cache["variant"] or
                key != effective_key(entry["variant"], entry["source_group_model_cache_key"])):
            raise ValueError("immutable decision cache entry key/hash/variant differs")
        original = entry.get("original_group_result")
        if (not isinstance(original, dict) or original.get("variant") != entry["variant"] or
                entry.get("original_group_result_sha256") != sharing.digest(original) or
                original.get("group_sha256") != entry["source_group_sha256"] or
                original.get("group_id", entry["source_group_id"]) != entry["source_group_id"] or
                entry.get("source_plan_sha256") != cache["source_plan_sha256"]):
            raise ValueError("original cached decision/version provenance differs")
        source_id = _text(entry.get("source_group_id"), "source group_id")
        if source_id in originals:
            raise ValueError("duplicate saved source group_id")
        originals[source_id] = original
    if sharing.digest(originals) != cache["source_group_results_sha256"]:
        raise ValueError("complete original group-results SHA differs")


def lookup_cache(cache, plan, variant):
    """Return (hits, misses), keyed only by the current plan's group IDs.

    A hit is a current-group-compatible result with source-cache provenance.
    Rebinding modifies a returned copy; all original cached results survive.
    Miss values are diagnostic dictionaries. No inference occurs on a miss.
    """
    _text(variant, "variant")
    _validate_cache(cache)
    groups = _groups(plan)
    hits, misses = {}, {}
    for identity, group in groups.items():
        key = effective_key(variant, group["model_cache_key"])
        reason = "variant_namespace_mismatch" if cache["variant"] != variant else "exact_model_input_not_cached"
        entry = cache["entries"].get(key) if cache["variant"] == variant else None
        if entry is None:
            misses[identity] = {"group_id": identity, "group_sha256": group["group_sha256"],
                                "effective_decision_cache_key": key, "reason": reason}
            continue
        result = copy.deepcopy(entry["original_group_result"])
        result["group_id"] = identity
        result["group_sha256"] = group["group_sha256"]
        result["variant"] = variant
        winner, refs, reason, tie = sharing._group_outcome(group, result, plan["documents"])
        if {"winning_value": winner, "quote_refs": refs, "reason": reason, "tie": tie} != entry["validated_outcome"]:
            raise ValueError("cached decision outcome changed after current-group rebinding")
        result["effective_decision_cache_key"] = key
        result["source_decision_cache"] = {
            "cache_sha256": cache["cache_sha256"], "variant": cache["variant"],
            "source_plan_sha256": cache["source_plan_sha256"],
            "source_group_id": entry["source_group_id"], "source_group_sha256": entry["source_group_sha256"],
            "original_group_result_sha256": entry["original_group_result_sha256"],
            "effective_decision_cache_key": key, "actual_saved_inference_count": 0,
        }
        hits[identity] = result
    return hits, misses


def _sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _read_results(path):
    values = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            result = json.loads(line)
            identity = _text(result.get("group_id"), "result group_id")
            if identity in values:
                raise ValueError("duplicate group-result JSONL record")
            values[identity] = result
    return values


def export_cache(plan_path, results_path, variant, output):
    """Write one new immutable JSON, preserving source files and raw SHAs."""
    output = Path(output)
    if output.exists():
        raise FileExistsError("refusing to overwrite decision cache: " + str(output))
    inputs = {"plan": Path(plan_path), "group_results": Path(results_path)}
    before = {name: _sha(path) for name, path in inputs.items()}
    plan = json.loads(inputs["plan"].read_text(encoding="utf-8"))
    cache = build_cache(plan, _read_results(inputs["group_results"]), variant)
    if before != {name: _sha(path) for name, path in inputs.items()}:
        raise ValueError("source files changed while building decision cache")
    manifest = {"input_file_sha256": before, "input_files": {name: str(path.resolve()) for name, path in inputs.items()},
                "input_file_sha256_unchanged": True, "code_sha256": _sha(Path(__file__)),
                "cache_semantic_sha256": cache["cache_sha256"], "actual_saved_inference_count": 0}
    manifest["manifest_sha256"] = sharing.digest(manifest)
    artifact = {"cache": cache, "manifest": manifest}
    artifact["artifact_sha256"] = sharing.digest(artifact)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as f:
        json.dump(artifact, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
    return artifact


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--group-results", required=True, type=Path)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    artifact = export_cache(args.plan, args.group_results, args.variant, args.output)
    print(json.dumps({"output": str(args.output), "variant": args.variant,
                      "entry_count": artifact["cache"]["entry_count"],
                      "cache_sha256": artifact["cache"]["cache_sha256"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
