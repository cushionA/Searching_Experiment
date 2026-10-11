#!/usr/bin/env python3
"""Fixed GLiNER comparison on prior real errors; freeze all gates before labels.

Reuse the previous evaluator without editing its frozen implementation. The
driver configuration and both evaluator sources are pinned before any scoring.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import evaluate_product_condition_share_v1 as E

VARIANTS = (
    "baseline", "product-top1-gliner", "product-top3-gliner",
    "product-top3-jev-gliner-majority-agree", "product-top3-jev-gliner-block-agree",
)


def run(prepared_dir, run_dir, legacy_labels, novel_labels, output_dir):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    sources = (Path(__file__), Path(E.__file__), Path(E.E.__file__))
    pinned = {str(path): E.E.sha(path) for path in sources}
    output_dir.mkdir(parents=True, exist_ok=False)
    E.E.write_json(output_dir / "driver-freeze.json", {
        "schema_version": "product-condition-gliner-evaluation-driver-v1",
        "labels_read": False, "threshold_search": False,
        "variants": list(VARIANTS), "code_sha256": pinned,
        "reference_kind": "machine_labels_not_human_gold",
    })
    for path in sources:
        (output_dir / path.name).write_bytes(path.read_bytes())
    previous = E.VARIANTS
    try:
        E.VARIANTS = VARIANTS
        result = E.run(prepared_dir, run_dir, legacy_labels, novel_labels, output_dir / "fixed-gates")
    finally:
        E.VARIANTS = previous
    if any(E.E.sha(Path(path)) != value for path, value in pinned.items()):
        raise ValueError("evaluation driver changed during the fixed comparison")
    summary = {**result, "schema_version": "product-condition-gliner-evaluation-result-v1",
        "driver_code_sha256": pinned, "fixed_gate_directory": "fixed-gates",
        "driver_configuration_frozen_before_labels": True,
        "output_sha256": {str(p.relative_to(output_dir)): E.E.sha(p)
                          for p in sorted(output_dir.rglob("*")) if p.is_file()}}
    E.E.write_json(output_dir / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("prepared-dir", "run-dir", "legacy-labels", "novel-labels", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    run(**vars(parser.parse_args()))
