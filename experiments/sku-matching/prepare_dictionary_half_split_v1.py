#!/usr/bin/env python3
"""Freeze a metadata-only, component-safe TRAINdict/EVALtest split."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

import networkx as nx


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".lab-output/sku-dictionary-half-20261015-v1/split-v1"
SOURCES = (
    ("dev", "source-dev", ROOT / ".lab-output/sku-classical-parser-20261015-v1/source-dev/population/run/inputs"),
    ("historical_test", "source-test-v2", ROOT / ".lab-output/sku-library-volume-20261015-v1/source-test-v2/inputs"),
)
SEED = 20261015


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def source_version(case: dict) -> str:
    path = case.get("source_record", {}).get("file", "")
    for part in Path(path.replace("\\", "/")).parts:
        if part in {"eval-v2", "eval-v3"}:
            return part
    return "unknown"


def product_hashes(product: dict) -> set[str]:
    hashes = set()
    for key in ("au_product", "rakuten_product"):
        value = product.get(key)
        if isinstance(value, dict) and isinstance(value.get("sha256"), str):
            hashes.add(value["sha256"].lower())
    return hashes


def keys_for(product: dict) -> set[tuple[str, str]]:
    keys = set()
    dossier = product.get("dossier_id")
    if dossier:
        keys.add(("dossier", str(dossier)))
    au_id = product.get("au_product", {}).get("product_id")
    if au_id:
        keys.add(("au_product_id", str(au_id)))
    keys.update(("raw_sha256", sha) for sha in product_hashes(product))
    return keys


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    output = args.output.resolve()
    source_manifest = []
    all_cases, all_products = [], []
    for source_name, source_dir, directory in SOURCES:
        case_path, product_path = directory / "cases.jsonl", directory / "products.jsonl"
        cases, products = read_jsonl(case_path), read_jsonl(product_path)
        source_manifest.append({
            "source_name": source_name,
            "source_directory": str(directory.relative_to(ROOT)),
            "source_directory_version": source_dir,
            "cases_path": str(case_path.relative_to(ROOT)),
            "cases_sha256": digest(case_path),
            "cases_count": len(cases),
            "products_path": str(product_path.relative_to(ROOT)),
            "products_sha256": digest(product_path),
            "products_count": len(products),
        })
        all_cases.extend((source_name, c) for c in cases)
        all_products.extend((source_name, p) for p in products)

    # Product rows are graph nodes. Shared dossier, AU product ID, or either
    # source raw-record SHA connects rows into a single leakage-safe component.
    graph = nx.Graph()
    key_owner: dict[tuple[str, str], int] = {}
    product_by_node: dict[int, tuple[str, dict]] = {}
    for index, item in enumerate(all_products):
        graph.add_node(index)
        product_by_node[index] = item
        for key in keys_for(item[1]):
            owner = key_owner.setdefault(key, index)
            graph.add_edge(index, owner)

    case_nodes: dict[str, int] = {}
    for index, (source_name, case) in enumerate(all_cases):
        possible = [
            key_owner.get(("dossier", str(case.get("dossier_id")))),
            key_owner.get(("au_product_id", str(case.get("au_product_id")))),
        ]
        matches = {n for n in possible if n is not None}
        if len(matches) != 1:
            raise ValueError(f"Case does not map to exactly one product component: {case.get('case_id')}")
        case_nodes[str(case["case_id"])] = next(iter(matches))

    components = []
    for members in nx.connected_components(graph):
        ordered = sorted(members)
        dossier_ids = sorted({str(product_by_node[i][1]["dossier_id"]) for i in ordered})
        au_ids = sorted({str(product_by_node[i][1]["au_product"]["product_id"]) for i in ordered})
        raw_shas = sorted({sha for i in ordered for sha in product_hashes(product_by_node[i][1])})
        case_ids = sorted(cid for cid, node in case_nodes.items() if node in members)
        identity = {"dossiers": dossier_ids, "au_product_ids": au_ids, "raw_sha256": raw_shas}
        group_id = "component-" + hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]
        components.append({"nodes": set(members), "products": len(members), "cases": len(case_ids), "case_ids": case_ids, "group_id": group_id, **identity})
    components.sort(key=lambda c: c["group_id"])

    rng = random.Random(SEED)
    shuffled = list(components)
    rng.shuffle(shuffled)
    side_counts = {"TRAINdict": {"cases": 0, "products": 0}, "EVALtest": {"cases": 0, "products": 0}}
    assignment: dict[str, str] = {}
    # One pass: place each whole shuffled component on the side with fewer
    # cases; use product count as the tie-break. This pursues half cases and
    # then half products without reading labels or outcomes.
    for component in shuffled:
        side = min(side_counts, key=lambda s: (side_counts[s]["cases"], side_counts[s]["products"], s))
        assignment[component["group_id"]] = side
        side_counts[side]["cases"] += component["cases"]
        side_counts[side]["products"] += component["products"]

    component_for_node = {node: component for component in components for node in component["nodes"]}
    case_rows = {"TRAINdict": [], "EVALtest": []}
    product_rows = {"TRAINdict": [], "EVALtest": []}
    membership = []
    cases_seen, dossiers_seen, au_ids_seen = set(), set(), set()
    raw_hashes_by_side = {"TRAINdict": set(), "EVALtest": set()}
    for node, (source_name, product) in product_by_node.items():
        side = assignment[component_for_node[node]["group_id"]]
        product_rows[side].append(product)
        dossiers_seen.add(str(product["dossier_id"]))
        au_ids_seen.add(str(product["au_product"]["product_id"]))
        raw_hashes_by_side[side].update(product_hashes(product))
    for source_name, case in all_cases:
        node = case_nodes[str(case["case_id"])]
        component = component_for_node[node]
        side = assignment[component["group_id"]]
        case_rows[side].append(case)
        cases_seen.add(str(case["case_id"]))
        membership.append({
            "case_id": case["case_id"], "dossier_id": case["dossier_id"],
            "source_name": source_name, "split": side,
            "source_version": source_version(case), "source_group_id": component["group_id"],
        })
    for side in case_rows:
        case_rows[side].sort(key=lambda x: str(x["case_id"]))
        product_rows[side].sort(key=lambda x: str(x["dossier_id"]))
        write_jsonl(output / side / "inputs/cases.jsonl", case_rows[side])
        write_jsonl(output / side / "inputs/products.jsonl", product_rows[side])
    # Check that output parsing preserves every original case/product JSON value.
    case_readback = [row for side in case_rows for row in read_jsonl(output / side / "inputs/cases.jsonl")]
    product_readback = [row for side in product_rows for row in read_jsonl(output / side / "inputs/products.jsonl")]
    source_cases = [row for _, row in all_cases]
    source_products = [row for _, row in all_products]
    records_unchanged = (
        sorted(source_cases, key=lambda row: row["case_id"]) == sorted(case_readback, key=lambda row: row["case_id"])
        and sorted(source_products, key=lambda row: row["dossier_id"]) == sorted(product_readback, key=lambda row: row["dossier_id"])
    )
    if not records_unchanged:
        raise ValueError("Serialized train/eval records do not match source JSON values")
    membership.sort(key=lambda x: x["case_id"])
    write_jsonl(output / "membership.jsonl", membership)

    raw_overlap = sorted(raw_hashes_by_side["TRAINdict"] & raw_hashes_by_side["EVALtest"])
    if raw_overlap:
        raise ValueError(f"Raw SHA leakage across sides: {raw_overlap[:5]}")
    id_overlap = {
        "case_ids": sorted({x["case_id"] for x in case_rows["TRAINdict"]} & {x["case_id"] for x in case_rows["EVALtest"]}),
        "dossier_ids": sorted({str(x["dossier_id"]) for x in product_rows["TRAINdict"]} & {str(x["dossier_id"]) for x in product_rows["EVALtest"]}),
        "au_product_ids": sorted({str(x["au_product"]["product_id"]) for x in product_rows["TRAINdict"]} & {str(x["au_product"]["product_id"]) for x in product_rows["EVALtest"]}),
    }
    if any(id_overlap.values()):
        raise ValueError(f"ID leakage across sides: {id_overlap}")

    components_manifest = [{
        "source_group_id": c["group_id"], "split": assignment[c["group_id"]],
        "case_count": c["cases"], "product_count": c["products"],
        "dossier_ids": c["dossiers"], "au_product_ids": c["au_product_ids"], "raw_sha256": c["raw_sha256"],
    } for c in components]
    (output / "groups.json").write_text(json.dumps(components_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    counts = {side: {"cases": len(case_rows[side]), "products": len(product_rows[side]), "groups": sum(assignment[c["group_id"]] == side for c in components)} for side in case_rows}
    freeze = {
        "schema_version": "sku_dictionary_half_split_v1",
        "seed": SEED,
        "source_code": {"path": str(Path(__file__).resolve().relative_to(ROOT)), "sha256": digest(Path(__file__).resolve())},
        "networkx_version": nx.__version__,
        "split_names": ["TRAINdict", "EVALtest"],
        "allocation_objective": "Deterministic one-pass greedy allocation of shuffled connected components: fewer cases first, then fewer products; metadata counts only.",
        "graph_connectivity": "NetworkX connected_components over product rows joined on dossier_id, AU product_id, AU raw SHA256, or Rakuten raw SHA256.",
        "component_identity": "SHA256 of canonical sorted dossier IDs, AU product IDs, and raw SHA256 values, truncated to 20 hex characters.",
        "input_policy": "Used IDs, source_record.file version routing, dossier/product metadata, and source raw SHA256 values only. No labels, grades, predictions, or accuracy values read for split allocation.",
        "native_rows": "Original case and product JSON objects are preserved as parsed; JSONL serialization is canonicalized (sorted keys, compact separators, UTF-8).",
        "sources": source_manifest,
        "counts": counts,
        "total": {"cases": sum(v["cases"] for v in counts.values()), "products": sum(v["products"] for v in counts.values()), "groups": len(components)},
        "assurance": {
            "all_input_case_ids_present_once": len(cases_seen) == sum(s["cases_count"] for s in source_manifest) and len(membership) == len(cases_seen),
            "case_dossier_au_product_ids_disjoint": id_overlap,
            "source_raw_sha256_disjoint": raw_overlap,
            "groups_assigned_exclusively": True,
            "original_json_values_preserved_after_readback": records_unchanged,
            "input_records_represented": {"cases": len(all_cases), "products": len(all_products)},
        },
        "artifact_sha256": {},
    }
    # Freeze hashes after writing all deliverable data files (manifest excludes itself).
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "SPLIT_FREEZE.json":
            freeze["artifact_sha256"][str(path.relative_to(output))] = digest(path)
    (output / "SPLIT_FREEZE.json").write_text(json.dumps(freeze, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "counts": counts, "total": freeze["total"], "raw_sha256_overlap": len(raw_overlap), "id_overlap": {k: len(v) for k, v in id_overlap.items()}}, sort_keys=True))


if __name__ == "__main__":
    main()
