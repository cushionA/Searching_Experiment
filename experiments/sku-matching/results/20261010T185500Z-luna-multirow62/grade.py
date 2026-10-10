#!/usr/bin/env python3
"""Score a frozen multirow round and the historical 200 distinct SKU pairs."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
EXP = REPO / "experiments/sku-matching"
sys.path.insert(0, str(EXP))
import evaluate_frozen_sku_accuracy as scorer


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def candidates(cases):
    return {c["case_id"]: {r["row_key"] for r in c["au_rows"]} for c in cases}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-dir", type=Path, required=True)
    external = parser.parse_args().evaluation_dir.resolve()
    assert external != REPO and REPO not in external.parents
    assert not external.exists(), "use a fresh external evaluation directory"
    protocol = read(HERE / "frozen-protocol.json")
    for filename, expected in protocol["code_sha256"].items():
        assert sha(EXP / filename) == expected, f"changed frozen code {filename}"
    for filename, expected in protocol["artifact_scripts_sha256"].items():
        assert sha(HERE / filename) == expected, f"changed frozen companion {filename}"
    assert sha(HERE / "selection.json") == protocol["selection_sha256"]
    assert sha(HERE / "strict-fields-preparation.json") == protocol["strict_fields_preparation_sha256"]
    results = EXP / "results"
    old18 = results / "20261010T160225Z-luna-task-improvement"
    old50 = results / "20261010T174640Z-luna-expanded50"
    v3 = results / "20261010T183000Z-luna-improvement-v3"
    v4 = results / "20261010T183754Z-luna-output-shape-v4"
    previous_inputs = read(old18 / "r02-reference-development/inputs.json") + read(old18 / "r03-reference-holdout/inputs.json")
    previous_inputs += read(old50 / "inputs.json") + read(v3 / "improved/inputs.json") + read(v4 / "improved/inputs.json")
    inputs = read(HERE / "inference/inputs.json")
    assert len(previous_inputs) == 138 and len(inputs) == 62
    all_inputs = previous_inputs + inputs
    for identities in ([c["case_id"] for c in all_inputs],
                       [(c["rakuten_sku_key"], c["au_product_id"]) for c in all_inputs],
                       [(c["au_product_id"], c["rakuten_url"], c["rakuten_variant_id"]) for c in all_inputs]):
        assert len(set(identities)) == 200
    previous = read(old18 / "summary.json")["cases"] + read(old50 / "reviewed-summary.json")["cases"]
    previous += read(v3 / "improved/reviewed-summary.json")["cases"] + read(v4 / "improved/reviewed-summary.json")["cases"]
    final = read(HERE / "inference/reviewed-summary.json")["cases"]
    assert [c["case_id"] for c in final] == [c["case_id"] for c in inputs]
    inventory_file = results / "20261010T171543Z-labeled-sku-accuracy/source-inventory.json"
    sources = [HERE / "frozen-protocol.json", HERE / "selection.json", inventory_file, old50 / "grade.py"]
    sources += [EXP / f for f in protocol["code_sha256"]] + [HERE / f for f in protocol["artifact_scripts_sha256"]]
    sources += list((HERE / "inference").rglob("*.json")) + [HERE / "inference/links.jsonl"]
    sources += [old18 / "summary.json", old50 / "reviewed-summary.json", v3 / "improved/reviewed-summary.json", v4 / "improved/reviewed-summary.json"]
    sources += [old18 / "r02-reference-development/inputs.json", old18 / "r03-reference-holdout/inputs.json", old50 / "inputs.json", v3 / "improved/inputs.json", v4 / "improved/inputs.json"]
    before = {str(p): sha(p) for p in sources}
    spec = importlib.util.spec_from_file_location("verified_label_loader", old50 / "grade.py")
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    payloads, label_records = loader.load_label_bytes(read(inventory_file), REPO)
    labels = [json.loads(line) for data in payloads.values() for line in data.decode().splitlines() if line.strip()]
    assert len(labels) == 1439
    evaluated = read(HERE / "inference/evaluation.json")["cases"]
    raw = [{"case_id": c["case_id"], "decision": "adopt" if c["decision"] == "candidate" else c["decision"],
            "row_key": c["candidate_row_key"] if c["decision"] == "candidate" else None} for c in evaluated]
    scored = {"multirow62": scorer.score(final, labels, candidates(inputs)),
              "matching62": scorer.score(raw, labels, candidates(inputs)),
              "cumulative_200": scorer.score(previous + final, labels, candidates(all_inputs))}
    assert before == {str(p): sha(p) for p in sources}, "frozen sources changed"
    external.mkdir()
    (external / "labels").mkdir()
    for tag, data in payloads.items():
        (external / "labels" / (tag + "-labels.jsonl")).write_bytes(data)
    for tag, score in scored.items():
        destination = external / tag
        destination.mkdir()
        save(destination / "summary.json", {"metrics": score["metrics"], "case_count": len(score["cases"]), "human_verified": False})
        (destination / "case-results.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in score["cases"]), encoding="utf-8")
    comparison = {"metrics": {tag: score["metrics"] for tag, score in scored.items()}, "human_verified": False,
                  "remaining_unprocessed_label_count": 1239,
                  "limitations": ["existing human-unverified Luna labels", "single trial per SKU", "62 variants correlated within two AU products", "31 cases have previously unused AU product, all share prior Rakuten source/URL", "cumulative score combines historical task versions"]}
    save(external / "comparisons.json", comparison)
    save(external / "grading-source-manifest.json", {"createdUTC": datetime.now(timezone.utc).isoformat(), "labels": label_records,
         "source_hashes_unchanged": True, "all_source_sha256_pre": before,
         "all_source_sha256_post": {str(p): sha(p) for p in sources}, "new_inference_calls": 0, "human_verified": False})
    saved = HERE / "grading/comparisons.json"
    if saved.exists():
        assert read(saved) == comparison, "reproduction differs"
    print(json.dumps({tag: s["metrics"]["known_case_accuracy"] for tag, s in scored.items()}))


if __name__ == "__main__":
    main()
