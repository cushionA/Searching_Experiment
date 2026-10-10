#!/usr/bin/env python3
"""Select a label-free fresh SKU holdout and, after task freeze, prepare two arms."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
EXP = ROOT / "experiments/sku-matching"
ZIP_PATH = EXP / "results/20261010-sku-generic-presence-checkpoint.zip"
INPUT_ROOT = ".lab-output/sku-generic-model-inputs-20261010-v2/"
ROUND = "20261010T183000Z-luna-improvement-v3"
PRIORITY_PREFIX = "v3-fresh50-20261010|"

sys.path.insert(0, str(EXP))
import sku_luna_inputs as luna_inputs
import trial_luna_sku_matching as core


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(data: bytes):
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def old_inputs():
    # The prior 18 are the six r02 reference-development and twelve r03 reference-holdout inputs.
    paths = [
        EXP / "results/20261010T160225Z-luna-task-improvement/r02-reference-development/inputs.json",
        EXP / "results/20261010T160225Z-luna-task-improvement/r03-reference-holdout/inputs.json",
        EXP / "results/20261010T174640Z-luna-expanded50/inputs.json",
    ]
    datasets = [read_json(p) for p in paths]
    if [len(x) for x in datasets] != [6, 12, 50]:
        raise ValueError("expected old r02/r03 18 plus expanded50 50 inputs")
    return paths, datasets


def source_files(case):
    files = set()
    for axis in case.get("rakuten_conditions", []):
        src = axis.get("source") or {}
        if src.get("raw_file"):
            files.add(src["raw_file"])
    return files


def choose():
    paths, old_sets = old_inputs()
    old = [item for group in old_sets for item in group]
    old_ids = {x["case_id"] for x in old}
    old_pairs = {(str(x.get("rakuten_sku_key")), str(x.get("au_product_id"))) for x in old}
    old_docs = set().union(*(source_files(x) for x in old))

    with ZipFile(ZIP_PATH) as zf:
        cases_bytes = zf.read(INPUT_ROOT + "cases.jsonl")
        products_bytes = zf.read(INPUT_ROOT + "products.jsonl")
        cases = read_jsonl(cases_bytes)
        products = read_jsonl(products_bytes)
    products_by_dossier = {p["dossier_id"]: p for p in products}

    eligible = []
    for case in cases:
        rak = case.get("rakuten", {})
        product = products_by_dossier.get(case.get("dossier_id"))
        rows = (product or {}).get("au", {}).get("rows", [])
        cid = case.get("case_id")
        sku = rak.get("source_sku_key")
        auid = case.get("au_product_id")
        if (case.get("cohort") not in {"legacy", "novel"} or
                not isinstance(cid, str) or not isinstance(sku, str) or not sku or
                auid is None or not 1 <= len(rows) <= 15 or
                cid in old_ids or (str(sku), str(auid)) in old_pairs):
            continue
        eligible.append({"case_id": cid, "dossier_id": case["dossier_id"],
                         "cohort": case["cohort"], "au_product_id": str(auid),
                         "rakuten_sku_key": str(sku), "candidate_rows": len(rows),
                         "priority": hashlib.sha256((PRIORITY_PREFIX + cid).encode()).hexdigest()})

    # Product round robin: first one fresh SKU per AU product absent from old68,
    # then distinct SKUs for AU products already represented in old68.
    new = defaultdict(list)
    repeat = defaultdict(list)
    old_products = {str(x.get("au_product_id")) for x in old}
    for item in eligible:
        bucket = repeat if item["au_product_id"] in old_products else new
        bucket[item["au_product_id"]].append(item)
    for groups in (new, repeat):
        for entries in groups.values():
            entries.sort(key=lambda x: (x["priority"], x["case_id"]))
    selected = []
    new_order = sorted(new, key=lambda pid: (new[pid][0]["priority"], pid))
    selected.extend(new[pid][0] for pid in new_order[:50])
    if len(selected) < 50:
        repeat_order = sorted(repeat, key=lambda pid: (repeat[pid][0]["priority"], pid))
        max_depth = max((len(repeat[pid]) for pid in repeat_order), default=0)
        for depth in range(max_depth):
            for pid in repeat_order:
                if depth < len(repeat[pid]) and len(selected) < 50:
                    selected.append(repeat[pid][depth])
            if len(selected) >= 50:
                break

    if len(selected) != 50:
        raise RuntimeError(f"only {len(selected)} eligible fresh cases available; requested exactly 50")
    ids = [x["case_id"] for x in selected]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate selected case IDs")
    if len({(x["rakuten_sku_key"], x["au_product_id"]) for x in selected}) != len(selected):
        raise ValueError("duplicate source SKU/AU product pair")

    # load_cases verifies every selected source span against the checkpoint ZIP bytes.
    loaded, input_hashes = luna_inputs.load_cases(ZIP_PATH, ids)
    if len(loaded) != 50 or [x["case_id"] for x in loaded] != ids:
        raise ValueError("source verification did not return the selected cases in order")
    selected_docs = {x["case_id"]: source_files(x) for x in loaded}
    shared_case_ids = [cid for cid, docs in selected_docs.items() if docs & old_docs]
    shared_docs = set().union(*(docs & old_docs for docs in selected_docs.values()))
    row_count = sum(len(x["au_rows"]) for x in loaded)
    product_counts = defaultdict(int)
    for x in selected:
        product_counts[x["au_product_id"]] += 1
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    selection = {
        "schema": "luna-fresh-sku-selection-v3",
        "round_id": ROUND,
        "created_utc": now,
        "requested_count": 50,
        "selected_count": len(selected),
        "selection_rule": "sha256('v3-fresh50-20261010|' + case_id); round robin across AU products absent from old68, then across AU products present in old68 using distinct Rakuten SKU pairs",
        "scope": {"cohorts": ["legacy", "novel"], "au_rows_inclusive": [1, 15],
                  "selection_uses_labels_or_prior_decisions": False},
        "excluded_prior": {"r02_r03_input_count": len(old_sets[0]) + len(old_sets[1]),
                           "expanded50_input_count": len(old_sets[2]),
                           "case_id_count": len(old_ids), "sku_au_pair_count": len(old_pairs),
                           "input_paths": [str(p.relative_to(ROOT)) for p in paths]},
        "eligible_count_after_exclusions": len(eligible),
        "selected_au_product_count": len(product_counts),
        "selected_product_case_counts": dict(sorted(product_counts.items())),
        "selected_candidate_au_row_count": row_count,
        "selected_case_ids_unique": len(ids) == len(set(ids)),
        "selected_sku_au_pairs_unique": len({(x['rakuten_sku_key'], x['au_product_id']) for x in selected}) == 50,
        "source_validation": {"method": "sku_luna_inputs.load_cases; all selected Rakuten/AU value spans verified against raw members",
                              "verified_case_count": len(loaded), "all_selected_cases_verified": True},
        "old68_source_sharing": {"old_input_count": len(old), "selected_cases_sharing_rakuten_source_file_count": len(shared_case_ids),
                                 "selected_cases_sharing_rakuten_source_file_ids": shared_case_ids,
                                 "shared_rakuten_raw_file_count": len(shared_docs)},
        "source_hashes": {**input_hashes, "checkpoint_zip_sha256": sha(ZIP_PATH.read_bytes()),
                          "cases_jsonl_sha256": sha(cases_bytes), "products_jsonl_sha256": sha(products_bytes)},
        "selected": [{k: x[k] for k in ("case_id", "dossier_id", "cohort", "au_product_id", "rakuten_sku_key", "candidate_rows", "priority")} for x in selected],
    }
    save(HERE / "selection.json", selection)
    save(HERE / "selected-case-ids.json", {"schema": "luna-selected-case-ids-v1", "case_ids": ids})
    return selection


def save(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare():
    selection = read_json(HERE / "selection.json")
    ids = read_json(HERE / "selected-case-ids.json")["case_ids"]
    loaded, hashes = luna_inputs.load_cases(ZIP_PATH, ids)
    if len(loaded) != 50:
        raise ValueError("selection no longer loads exactly 50 verified cases")
    # Call only after the parent freezes task-v3; this subcommand intentionally is not run during selection.
    arms = {}
    for name in ("baseline", "improved"):
        target = HERE / name
        mode = "scoped" if name == "baseline" else "scoped-v3"
        arms[name] = core.prepare(target, loaded, hashes, ZIP_PATH, mode=mode, selection=selection)
    return arms


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("select", "prepare"))
    args = parser.parse_args()
    result = choose() if args.command == "select" else prepare()
    print(json.dumps({"selected_count": result["selected_count"]} if args.command == "select" else
                     {arm: {"planned_calls": manifest["planned_calls"], "task_version": manifest["task_version"]}
                      for arm, manifest in result.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
