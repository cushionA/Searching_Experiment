#!/usr/bin/env python3
"""Select 62 fresh multirow cases, then prepare their frozen v4 requests."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
EXP = HERE.parents[1]
sys.path.insert(0, str(EXP))
import sku_luna_inputs as inputs
import trial_luna_sku_matching as core

ARCHIVE = EXP / "results/20261010-sku-generic-presence-checkpoint.zip"
PRIOR_INPUTS = [
    EXP / "results/20261010T160225Z-luna-task-improvement/r02-reference-development/inputs.json",
    EXP / "results/20261010T160225Z-luna-task-improvement/r03-reference-holdout/inputs.json",
    EXP / "results/20261010T174640Z-luna-expanded50/inputs.json",
    EXP / "results/20261010T183000Z-luna-improvement-v3/baseline/inputs.json",
    EXP / "results/20261010T183754Z-luna-output-shape-v4/baseline/inputs.json",
]
TARGET = 62
SEED = "multirow62-20261010|"


def raw_files(value):
    found = set()
    if isinstance(value, dict):
        raw_file = value.get("raw_file")
        if isinstance(raw_file, str) and raw_file:
            found.add(raw_file)
        for child in value.values():
            found.update(raw_files(child))
    elif isinstance(value, list):
        for child in value:
            found.update(raw_files(child))
    return found


def prior_sets(previous):
    return {
        "case_ids": {c["case_id"] for c in previous},
        "sku_au_pairs": {(c.get("rakuten_sku_key"), c["au_product_id"]) for c in previous},
        "variants": {(c["au_product_id"], c.get("rakuten_url"), c.get("rakuten_variant_id")) for c in previous},
        "urls": {c.get("rakuten_url") for c in previous if c.get("rakuten_url")},
        "raw_files": set().union(*(raw_files(c) for c in previous)),
        "au_products": {c["au_product_id"] for c in previous},
    }


def select():
    previous = [case for path in PRIOR_INPUTS for case in core.read(path)]
    if len(previous) != 138:
        raise AssertionError(f"expected 138 prior cases, found {len(previous)}")
    prior = prior_sets(previous)
    with ZipFile(ARCHIVE) as bundle:
        cases_all = [json.loads(line) for line in bundle.read(inputs.INPUT_ROOT + "cases.jsonl").decode().splitlines() if line.strip()]
        products = {p["dossier_id"]: p for p in (json.loads(line) for line in bundle.read(inputs.INPUT_ROOT + "products.jsonl").decode().splitlines() if line.strip())}

    groups = defaultdict(list)
    excluded_case_ids = excluded_sku_pairs = excluded_variants = excluded_shape = 0
    for case in cases_all:
        rak = case.get("rakuten", {})
        au_product = case.get("au_product_id")
        rows = products[case["dossier_id"]]["au"].get("rows", [])
        pair = (rak.get("source_sku_key"), au_product)
        variant = (au_product, rak.get("url"), rak.get("variant_id"))
        if case.get("cohort") not in {"legacy", "novel"}:
            continue
        if case["case_id"] in prior["case_ids"]:
            excluded_case_ids += 1
            continue
        if pair in prior["sku_au_pairs"]:
            excluded_sku_pairs += 1
            continue
        if variant in prior["variants"]:
            excluded_variants += 1
            continue
        if len(rows) not in {27, 30}:
            excluded_shape += 1
            continue
        priority = hashlib.sha256((SEED + case["case_id"]).encode("utf-8")).hexdigest()
        groups[au_product].append((priority, case["case_id"], case))
    for group in groups.values():
        group.sort(key=lambda entry: (entry[0], entry[1]))

    # Visit previously unused AU products first; within each wave use hash priority.
    product_order = sorted(groups, key=lambda pid: (pid in prior["au_products"], groups[pid][0][0], str(pid)))
    selected = []
    selected_pairs, selected_variants, selected_ids = set(), set(), set()
    depth = 0
    while len(selected) < TARGET:
        added = 0
        for pid in product_order:
            if depth >= len(groups[pid]):
                continue
            _, cid, case = groups[pid][depth]
            rak = case["rakuten"]
            pair = (rak.get("source_sku_key"), case.get("au_product_id"))
            variant = (case.get("au_product_id"), rak.get("url"), rak.get("variant_id"))
            if cid in selected_ids or pair in prior["sku_au_pairs"] or pair in selected_pairs or variant in prior["variants"] or variant in selected_variants:
                continue
            selected.append(cid)
            selected_ids.add(cid)
            selected_pairs.add(pair)
            selected_variants.add(variant)
            added += 1
            if len(selected) == TARGET:
                break
        if not added:
            raise AssertionError(f"only {len(selected)} eligible cases; cannot reach {TARGET}")
        depth += 1

    cases, hashes = inputs.load_cases(ARCHIVE, selected)
    if len(cases) != TARGET or len({c["case_id"] for c in cases}) != TARGET:
        raise AssertionError("selected case count or uniqueness mismatch")
    if hashes.get("zip_sha256") != hashlib.sha256(ARCHIVE.read_bytes()).hexdigest():
        raise AssertionError("checkpoint archive hash mismatch")
    if any(not all(axis.get("source_verified") for axis in c["rakuten_conditions"])
           or not all(cond.get("source_verified") for row in c["au_rows"] for cond in row["conditions"])
           for c in cases):
        raise AssertionError("source span verification failed")

    selected_raw = {c["case_id"]: raw_files(c) for c in cases}
    audit_rows = []
    for case in cases:
        overlap = selected_raw[case["case_id"]] & prior["raw_files"]
        row = {"case_id": case["case_id"], "rakuten_url_seen_before": case.get("rakuten_url") in prior["urls"],
               "au_product_seen_before": case["au_product_id"] in prior["au_products"],
               "raw_source_files_seen_before": sorted(overlap),
               "raw_sources_all_unseen": not overlap}
        row["all_three_dimensions_unseen"] = not row["rakuten_url_seen_before"] and not row["au_product_seen_before"] and row["raw_sources_all_unseen"]
        audit_rows.append(row)
    selection = {
        "createdUTC": datetime.now(timezone.utc).isoformat(), "selected_count": TARGET,
        "selected_case_ids": selected, "prior_case_count": len(previous),
        "source_input_hashes": hashes, "labels_or_previous_predictions_used": False,
        "rule": "legacy/novel; catalog AU row count 27 or 30; exclude prior case ID, Rakuten source_sku_key x AU product, and AU product + Rakuten URL + variant identity; prioritize unused AU products, then deterministic SHA256 multirow62-20261010|case_id order with product round robin",
        "selected_product_count": len({c["au_product_id"] for c in cases}),
        "selected_candidate_rows": sum(len(c["au_rows"]) for c in cases),
        "selected_cohort_counts": {cohort: sum(c.get("cohort") == cohort for c in cases) for cohort in ("legacy", "novel")},
        "shape_counts": {str(n): sum(len(c["au_rows"]) == n for c in cases) for n in (27, 30)},
        "purpose": "cumulative 200-case structural multirow evaluation; selection uses input metadata only",
        "excluded_counts": {"prior_case_id": excluded_case_ids, "prior_sku_au_pair": excluded_sku_pairs,
                            "prior_variant_identity": excluded_variants, "row_count_not_27_or_30": excluded_shape},
    }
    novelty_audit = {
        "prior_case_count": 138, "selected_count": TARGET,
        "unique_selected_urls": len({c.get("rakuten_url") for c in cases}),
        "url_unseen_count": sum(not r["rakuten_url_seen_before"] for r in audit_rows),
        "unique_selected_au_products": len({c["au_product_id"] for c in cases}),
        "au_product_unseen_count": sum(not r["au_product_seen_before"] for r in audit_rows),
        "raw_source_files_unseen_count": sum(r["raw_sources_all_unseen"] for r in audit_rows),
        "all_three_dimensions_unseen_count": sum(r["all_three_dimensions_unseen"] for r in audit_rows),
        "per_case": audit_rows,
        "dimensions": {"url": "Rakuten URL string membership against prior 138", "au_product": "AU product ID membership against prior 138",
                       "raw_sources": "all raw_file references available in selected input metadata versus all raw_file references in prior 138; source spans separately hash/quote verified by inputs.load_cases"},
    }
    core.save(HERE / "selection.json", selection)
    core.save(HERE / "selected-case-ids.json", {"case_ids": selected})
    core.save(HERE / "novelty-audit.json", novelty_audit)
    print(json.dumps({k: selection[k] for k in ("selected_count", "selected_product_count", "selected_candidate_rows", "selected_cohort_counts", "shape_counts")} | {
        "url_unseen_count": novelty_audit["url_unseen_count"], "au_product_unseen_count": novelty_audit["au_product_unseen_count"],
        "raw_source_files_unseen_count": novelty_audit["raw_source_files_unseen_count"],
        "all_three_dimensions_unseen_count": novelty_audit["all_three_dimensions_unseen_count"]}, ensure_ascii=False))


def prepare():
    import sku_luna_output_shape as shape
    selection = core.read(HERE / "selection.json")
    cases, hashes = inputs.load_cases(ARCHIVE, selection["selected_case_ids"])
    if hashes != selection["source_input_hashes"]:
        raise AssertionError("selected input hashes changed")
    directory = HERE / "inference"
    manifest = shape.prepare_shape_round(directory, cases, hashes, ARCHIVE, selection=selection)
    # This additive request contract was selected before the first inference.
    strict = core.read(HERE / "strict-fields-preparation.json")
    for entry in manifest["entries"]:
        path = directory / entry["request"]
        request = core.read(path)
        part = request["body"]["contents"][0]["parts"][0]
        part["text"] = part["text"].replace("INPUT=", strict["strict_fields_instruction"] + "INPUT=", 1)
        core.save(path, request)
        entry["request_sha256"] = core.sha(path.read_bytes())
    manifest["task_version"] = strict["task_version"]
    manifest["shape_policy"] = "v4_cardinality_plus_explicit_allowed_output_fields"
    core.save(directory / "manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("select", "prepare"))
    command = parser.parse_args().command
    select() if command == "select" else prepare()
