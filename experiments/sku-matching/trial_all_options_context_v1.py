#!/usr/bin/env python3
"""Run the frozen compound trial with all options in every JEV context.

The wrapper and its transitive prompt helper are independently frozen before
calling the unchanged trial, which freezes predictions before label scoring.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil

import all_options_context_v1 as ALL_OPTIONS
import compound_product_choices_v1 as COMPOUND
import trial_compound_product_choices_v1 as TRIAL

DEFAULT_OUTPUT = TRIAL.ROOT / ".lab-output/sku-all-options-context-20261012-v1"
DEFAULT_WRAPPER = TRIAL.ROOT / ".lab-output/sku-all-options-context-wrapper-20261012-v1"
CONTENT_VERSION = "all-options-context-v1/axis+complete-ordered-raw-options-json/actual-specs/top3-min2"


def run(output_dir=DEFAULT_OUTPUT, wrapper_dir=DEFAULT_WRAPPER, **trial_paths):
    output_dir, wrapper_dir = Path(output_dir).resolve(), Path(wrapper_dir).resolve()
    if output_dir.exists() or wrapper_dir.exists():
        raise FileExistsError("trial and wrapper output directories must both be new")
    code_paths = [Path(__file__), Path(ALL_OPTIONS.__file__), Path(COMPOUND.__file__), Path(TRIAL.__file__)]
    snapshot = TRIAL.VERIFY.snapshot_paths({str(p): p for p in code_paths})
    wrapper_dir.mkdir(parents=True, exist_ok=False)
    code_dir = wrapper_dir / "code"
    code_dir.mkdir()
    for path in code_paths:
        shutil.copyfile(path, code_dir / path.name)
    TRIAL.E.write_json(wrapper_dir / "execution-freeze.json", {
        "schema_version": "sku-all-options-context-wrapper-v1", "content_version": CONTENT_VERSION,
        "experiment_status": "exploratory_dev_informed", "labels_read": False, "independent_test": False,
        "production_adoption_allowed": False, "source_snapshot": snapshot,
        "complete_options_in_each_candidate_context": True,
        "option_encoding": "JSON array of all literal raw values in original order plus the original unknown control",
        "selected_value_separately_injected": False, "selected_row_injected": False,
        "fixed_inputs": "original title/block state, ranking, votes, nonshared baseline, gate and references",
        "output_dir": str(output_dir), "trial_paths": {k: str(v) for k, v in trial_paths.items()},
        "copied_code_sha256": {p.name: TRIAL.E.sha(p) for p in code_dir.iterdir()}})
    # This changes only this interpreter's renderer selection. The original
    # trial source, its previous run, and its frozen code remain unchanged.
    old_builder, old_version = TRIAL.COMPOUND, TRIAL.CONTENT_VERSION
    try:
        TRIAL.COMPOUND, TRIAL.CONTENT_VERSION = ALL_OPTIONS, CONTENT_VERSION
        summary = TRIAL.run(output_dir=output_dir, **trial_paths)
    finally:
        TRIAL.COMPOUND, TRIAL.CONTENT_VERSION = old_builder, old_version
    TRIAL.VERIFY.verify_snapshot(snapshot, phase="after all-options inference and scoring")
    TRIAL.E.write_json(wrapper_dir / "summary.json", {
        "schema_version": "sku-all-options-context-wrapper-result-v1", "content_version": CONTENT_VERSION,
        "source_verified_after_inference_and_scoring": True,
        "labels_read_after_all_gates_frozen": summary["labels_read_after_all_gates_frozen"],
        "metrics": summary["metrics"], "output_summary_sha256": TRIAL.E.sha(output_dir / "summary.json"),
        "wrapper_sha256": {str(p.relative_to(wrapper_dir)): TRIAL.E.sha(p)
                           for p in wrapper_dir.rglob("*") if p.is_file()}})
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default in (("prepared-dir", TRIAL.DEFAULT_PREP), ("cpu-dir", TRIAL.DEFAULT_CPU),
                          ("output-dir", DEFAULT_OUTPUT), ("wrapper-dir", DEFAULT_WRAPPER),
                          ("jev-model-dir", TRIAL.DEFAULT_MODEL), ("old-evaluation-dir", TRIAL.DEFAULT_OLD_EVAL),
                          ("reference-routes-dir", TRIAL.DEFAULT_ROUTES), ("comparison-dir", TRIAL.DEFAULT_COMPARISON)):
        parser.add_argument("--" + name, type=Path, default=default)
    run(**vars(parser.parse_args()))
