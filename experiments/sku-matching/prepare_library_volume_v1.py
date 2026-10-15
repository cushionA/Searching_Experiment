#!/usr/bin/env python3
"""Bind another historical population to the frozen input/view builders.

Only population metadata and filesystem paths are adapted here. The existing
whole-condition preparer, native normalization, and JPY-span masking run as-is.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

import installed_library_views_v1 as views
import prepare_classical_population_v1 as population
import prepare_library_price_free_views_v1 as prices


HERE = Path(__file__).resolve().parent


def write(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, sort_keys=True, allow_nan=False)
        f.write("\n")


def prepare(original: Path, output: Path, split: str = "historical-test") -> None:
    if output.exists():
        raise FileExistsError(output)
    original = original.resolve()
    code_paths = [Path(__file__), Path(population.__file__), Path(views.__file__), Path(prices.__file__)]
    code_pins = {p.name: population.file_sha(p) for p in code_paths}
    input_pins = {n: population.file_sha(original / n) for n in ("cases.jsonl", "products.jsonl")}
    output.mkdir(parents=True)
    started = time.perf_counter()
    staging = output / "unmodified-preparer-staging"
    prior = population.build(original, staging)
    inputs = output / "population-inputs"
    inputs.mkdir()
    names = tuple(prior["output_sha256"])
    for name in names:
        if name in ("case-plans.jsonl", "row-plans.jsonl"):
            with (inputs / name).open("x", encoding="utf-8") as f:
                for _, item in population.rows(staging / name):
                    item["split"] = split
                    f.write(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        else:
            # These files remain identical to the frozen preparer's output.
            os.link(staging / name, inputs / name)
    manifest = {**prior, "split": split, "test_data_read": True,
                "historical_data_already_used_by_other_methods": True,
                "independent_test": False,
                "population_adapter_sha256": population.file_sha(Path(__file__)),
                "parent_preparation_manifest_sha256": population.file_sha(staging / "manifest.json"),
                "output_sha256": {n: population.file_sha(inputs / n) for n in names}}
    write(inputs / "manifest.json", manifest)
    print(json.dumps({"phase": "population_prepared", "cases": manifest["case_count"],
                      "products": manifest["product_count"], "rows": manifest["row_plan_count"],
                      "bindings": manifest["task_binding_count"]}), flush=True)

    values = set()
    for _, req in population.rows(inputs / "requests.jsonl"):
        values.update(req["option_values"])
        values.add(req["selected_value"])
        values.update(a["value"] for a in req["target_selected_axes"])
    for _, product in population.rows(original / "products.jsonl"):
        values.update(a["value"] for row in product["au_rows"] for a in row.get("axes", []))
        values.update(v for family in product["rakuten_product"].get("selector_families", [])
                      for v in family.get("values", []))
    if any(not isinstance(v, str) for v in values):
        raise ValueError("non-string native values; do not silently discard them")
    view_dir = output / "views"
    view_dir.mkdir()
    raw_values = sorted(values)
    write(view_dir / "raw-values.json", raw_values)
    write(view_dir / "views.json", views.build_views(raw_values))
    print(json.dumps({"phase": "native_views_prepared", "unique_values": len(raw_values)}), flush=True)
    previous_base, previous_argv = prices.BASE, sys.argv
    try:
        prices.BASE = view_dir
        sys.argv = [str(Path(prices.__file__)), "--output-dir", str(view_dir)]
        if prices.main() != 0:
            raise RuntimeError("existing price-mask adapter failed")
    finally:
        prices.BASE, sys.argv = previous_base, previous_argv
    if any(population.file_sha(p) != code_pins[p.name] for p in code_paths):
        raise ValueError("preparation code changed")
    if any(population.file_sha(original / n) != d for n, d in input_pins.items()):
        raise ValueError("original source inputs changed")
    files = [inputs / n for n in (*names, "manifest.json")]
    files += list(view_dir.iterdir())
    write(output / "PREPARATION_FREEZE.json", {
        "schema": "sku-library-volume-preparation-v1", "split": split,
        "independent_test": False, "historical_data_already_used_by_other_methods": True,
        "original_path": str(original), "source_input_pins": input_pins, "code_pins": code_pins,
        "output_pins": {str(p.relative_to(output)): population.file_sha(p) for p in files},
        "case_count": manifest["case_count"], "product_count": manifest["product_count"],
        "row_count": manifest["row_plan_count"], "binding_count": manifest["task_binding_count"],
        "labels_read": False, "grades_read": False, "models_loaded": [],
        "new_sku_rules_or_dictionaries": False, "all_native_catalog_values_retained": True,
        "elapsed_wall_seconds": time.perf_counter() - started})
    print(json.dumps({"phase": "preparation_frozen", "output": str(output),
                      "elapsed_wall_seconds": time.perf_counter() - started}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", default="historical-test")
    prepare(**vars(parser.parse_args()))
