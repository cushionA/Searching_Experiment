"""Aggregate fixed CPU diagnostics while preserving model/batch distinctions."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read(path):
    data = path.read_bytes()
    return json.loads(data), {"path": str(path.relative_to(HERE)), "sha256": hashlib.sha256(data).hexdigest()}


def summarize(output):
    result = {"date_utc": datetime.now(timezone.utc).isoformat(),
              "branch": "codex/sku-model-comparison-20261009", "status": "fixed diagnostic comparison, no production model selected",
              "observed_data": {"products": 32, "au_skus": 440, "rakuten_skus": 891, "total_skus": 1331},
              "evaluation_basis": {"synthetic_control_cases": 1000, "same": 500, "different": 417, "review": 83,
                                   "underlying_labeled_families": 1,
                                   "query_direction": "Rakuten SKU to au candidates",
                                   "real_ranking_reference": "canonical attribute correspondence within user-provided family; stock and price ignored",
                                   "production_threshold_selected": False, "independent_holdout_accuracy_available": False},
              "models": [], "extraction": [], "inputs_and_outputs": []}
    for key in ("minilm", "bekko", "granite", "ruri", "reranker"):
        for batch in (1,8):
            source, metadata = read(HERE/f"results/model-comparison-20261009/{key}-b{batch}/summary.json")
            result["inputs_and_outputs"].append(metadata)
            result["models"].append({field: source[field] for field in (
                "model_key", "manifest", "score_kind", "threads", "batch_size", "runtime_versions",
                "cold_load_seconds_including_import_and_hash_check", "latency_probe", "process_peak_rss_mib",
                "controls", "rankings", "source_sha256", "limitations")})
    for batch in (1,8):
        source, metadata = read(HERE/f"results/20261009-gliner-extraction-b{batch}.json")
        result["inputs_and_outputs"].append(metadata)
        for mode, styles in source["input_modes"].items():
            for style, config in styles.items():
                entry = {"batch_size": batch, "mode": mode, "label_style": style,
                         "model": source["model"], "revision": source["model_revision"],
                         "runtime": source["actual_runtime"], "source_sha256": source["source_sha256"],
                         "schema_labels": source["schema_labels"][style]}
                entry.update({field: value for field,value in config.items()
                              if field not in ("records", "pair_decision_diagnostics")})
                entry["pair_decision_diagnostics"] = {field: value for field,value in config["pair_decision_diagnostics"].items()
                                                       if field != "cases"}
                result["extraction"].append(entry)
    for name in ("20261009-model-input-audit.json", "20261009-snapshot-audit.json", "20261009-gliner-smoke.json"):
        _, metadata = read(HERE/"results"/name)
        result["inputs_and_outputs"].append(metadata)
    result["conclusions"] = [
        "All compared candidates run on CPU; embedding retrieval and identity classification are distinct tasks.",
        "Within this family, SKU-only retrieval ranks better than literal title+SKU for all four embedding candidates.",
        "Ruri SKU-only is a useful next retrieval baseline for this fixture; high similarity still accepts known contradictions.",
        "Reranker can improve ordering only within its fixed MiniLM top10 candidate set; retrieval misses stay in recall denominator.",
        "Final acceptance requires verified attribute equality and availability/single-price checks; incomplete or ambiguous evidence remains review.",
        "GLiNER extraction cannot recover product composition metadata absent from the input; inspect spans and retain missing evidence.",
        "No production threshold, broad winner, or independent held-out accuracy is established by one-family synthetic controls."]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as target:
        target.write(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    return {"output": str(output), "models_and_batches": len(result["models"]), "extraction_configs": len(result["extraction"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(summarize(args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
