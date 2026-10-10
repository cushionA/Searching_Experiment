"""Reproduce frozen v5/v6 scores, then score the v7 facet derivation externally."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
EXP = HERE.parents[1]
REPO = EXP.parents[1]
OLD = EXP / "results/20261010T192800Z-luna-curtain-dimension-v6"
sys.path.insert(0, str(EXP))
import evaluate_frozen_sku_accuracy as scorer
import trial_luna_sku_matching as core


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-dir", type=Path, required=True)
    external = parser.parse_args().evaluation_dir.resolve()
    assert external != REPO and REPO not in external.parents and not external.exists()
    freeze = core.read(HERE / "grading-freeze.json")
    assert all(core.sha((EXP / name).read_bytes()) == expected for name, expected in freeze["sha256"].items())
    subprocess.run([sys.executable, "-B", str(OLD / "grade.py"), "--evaluation-dir", str(external)], check=True, stdout=subprocess.DEVNULL)
    labels = [json.loads(line) for file in (external / "labels").glob("*.jsonl") for line in file.read_text().splitlines() if line.strip()]
    assert len(labels) == 1439
    inputs = core.read(HERE / "derived-v2/inputs.json")
    cases = core.read(HERE / "derived-v2/reviewed-summary.json")["cases"]
    prior_paths = ["20261010T160225Z-luna-task-improvement/r02-reference-development", "20261010T160225Z-luna-task-improvement/r03-reference-holdout", "20261010T174640Z-luna-expanded50", "20261010T183000Z-luna-improvement-v3/improved", "20261010T183754Z-luna-output-shape-v4/improved"]
    prior_inputs = [c for path in prior_paths for c in core.read(EXP / "results" / path / "inputs.json")]
    prior = core.read(EXP / "results/20261010T160225Z-luna-task-improvement/summary.json")["cases"]
    prior += [c for path in prior_paths[2:] for c in core.read(EXP / "results" / path / "reviewed-summary.json")["cases"]]
    candidates = lambda cs: {c["case_id"]: {r["row_key"] for r in c["au_rows"]} for c in cs}
    scores = {"v7_62": scorer.score(cases, labels, candidates(inputs)), "cumulative_200_v7": scorer.score(prior + cases, labels, candidates(prior_inputs + inputs))}
    for tag, score in scores.items():
        dest = external / tag
        dest.mkdir()
        core.save(dest / "summary.json", {"metrics": score["metrics"], "case_count": len(score["cases"]), "human_verified": False})
        (dest / "case-results.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in score["cases"]), encoding="utf-8")
    baseline = {c["case_id"]: c for c in map(json.loads, (external / "v6_62/case-results.jsonl").read_text().splitlines())}
    changes = [{"case_id": c["case_id"], "before": baseline[c["case_id"]]["correct"], "after": c["correct"]} for c in scores["v7_62"]["cases"] if c["correct"] != baseline[c["case_id"]]["correct"]]
    comparison = core.read(external / "comparisons.json")
    comparison["metrics"].update({tag: s["metrics"] for tag, s in scores.items()})
    comparison.update({"paired_changes": changes, "development_reuse": True, "new_distinct_case_count": 0, "note": "same62; frozen matching answers composed with four new product/value lace-facet answers; newly needed dimensions and 62 isolated semantic reviews; development comparison, not independent holdout"})
    core.save(external / "comparisons.json", comparison)
    assert all(core.sha((EXP / name).read_bytes()) == expected for name, expected in freeze["sha256"].items())
    core.save(external / "v7-source-manifest.json", {"source_hashes_unchanged": True, "sha256": freeze["sha256"], "human_verified": False})
    if (HERE / "grading/comparisons.json").exists():
        assert core.read(HERE / "grading/comparisons.json") == comparison
    print(json.dumps({tag: score["metrics"]["known_case_accuracy"] for tag, score in scores.items()}))


if __name__ == "__main__":
    main()
