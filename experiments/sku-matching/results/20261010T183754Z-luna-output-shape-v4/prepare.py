#!/usr/bin/env python3
"""Select fresh cases by input arity, then prepare frozen v3/v4 arms."""
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
]


def select():
    previous = [case for path in PRIOR_INPUTS for case in core.read(path)]
    assert len(previous) == 118
    ids = {c["case_id"] for c in previous}
    pairs = {(c["rakuten_sku_key"], c["au_product_id"]) for c in previous}
    variants = {(c["au_product_id"], c["rakuten_url"], c["rakuten_variant_id"]) for c in previous}
    with ZipFile(ARCHIVE) as bundle:
        all_cases = [json.loads(line) for line in bundle.read(inputs.INPUT_ROOT + "cases.jsonl").decode().splitlines() if line.strip()]
        products = {p["dossier_id"]: p for p in [json.loads(line) for line in bundle.read(inputs.INPUT_ROOT + "products.jsonl").decode().splitlines() if line.strip()]}
    groups = defaultdict(list)
    for case in all_cases:
        product = products[case["dossier_id"]]
        rak = case["rakuten"]
        rows = product["au"]["rows"]
        unit = (case["au_product_id"], rak.get("url"), rak.get("variant_id"))
        if case["cohort"] not in {"legacy", "novel"} or case["case_id"] in ids or (rak.get("source_sku_key"), case["au_product_id"]) in pairs or unit in variants:
            continue
        if not 1 <= len(rows) <= 15 or not all(len(rak["axes"]) > len(row["axes"]) for row in rows):
            continue
        priority = hashlib.sha256(("v4-shape-fresh20-20261010|" + case["case_id"]).encode()).hexdigest()
        groups[case["au_product_id"]].append((priority, case["case_id"]))
    for group in groups.values():
        group.sort()
    order = sorted(groups, key=lambda pid: groups[pid][0])
    selected = []
    depth = 0
    while len(selected) < 20:
        added = 0
        for pid in order:
            if len(groups[pid]) > depth and len(selected) < 20:
                selected.append(groups[pid][depth][1])
                added += 1
        assert added, "fewer than twenty fresh arity challenge cases available"
        depth += 1
    cases, hashes = inputs.load_cases(ARCHIVE, selected)
    assert len({(c["rakuten_sku_key"], c["au_product_id"]) for c in cases}) == 20
    assert len({(c["au_product_id"], c["rakuten_url"], c["rakuten_variant_id"]) for c in cases}) == 20
    selection = {"createdUTC": datetime.now(timezone.utc).isoformat(), "selected_count": 20,
                 "selected_case_ids": selected, "prior_case_count": 118,
                 "source_input_hashes": hashes, "labels_or_previous_predictions_used": False,
                 "rule": "legacy/novel; AU rows1..15; Rakuten check count strictly greater than every AU row check count; hash priority and product round robin",
                 "selected_product_count": len({c["au_product_id"] for c in cases}),
                 "selected_candidate_rows": sum(len(c["au_rows"]) for c in cases),
                 "purpose": "structural challenge by input shape, not a random population accuracy estimate"}
    core.save(HERE / "selection.json", selection)
    core.save(HERE / "selected-case-ids.json", {"case_ids": selected})
    print(json.dumps({k: selection[k] for k in ("selected_count", "selected_product_count", "selected_candidate_rows")}))


def prepare():
    import sku_luna_output_shape as shape
    selection = core.read(HERE / "selection.json")
    cases, hashes = inputs.load_cases(ARCHIVE, selection["selected_case_ids"])
    assert hashes == selection["source_input_hashes"]
    core.prepare(HERE / "baseline", cases, hashes, ARCHIVE, mode="scoped-v3", selection=selection)
    shape.prepare_shape_round(HERE / "improved", cases, hashes, ARCHIVE, selection=selection)
    print("prepared v3/v4 twenty-case paired arms")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("select", "prepare"))
    args = parser.parse_args()
    select() if args.command == "select" else prepare()
