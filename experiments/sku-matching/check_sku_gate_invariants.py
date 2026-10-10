"""Label-free invariant checks on frozen SKU-gate predictions.

Runs after `run_sku_gates.py predict` and never opens labels. Each check states a
property the gates must hold regardless of any gold answer:

  row_injectivity      one AU row is never accepted for two different Rakuten selections
  b_within_a           every B accept is an A accept of the same row (B only removes evidence)
  no_opposite          A and B never disagree as matched vs unmatched
  config_no_flip       an accept under any source config is, under the primary config, the
                       same row or review (more evidence may only withhold, never redirect)
  sibling_swap         replacing one selected value with a sibling option never re-accepts
                       the row accepted for the original SKU
  unquoted_guard       dropping the source span of a selected value always yields review
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_sku_gates as runner  # noqa: E402
import sku_gate_sources as src  # noqa: E402
import sku_gates as gates  # noqa: E402

ROOT = runner.ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=runner.DEFAULT_OUT)
    args = parser.parse_args()
    out = ROOT / args.out
    runner.check_freeze(out)
    pmanifest = json.loads((out / "predictions" / "manifest.json").read_text(encoding="utf-8"))
    if pmanifest["freeze_sha256"] != src.sha256_file(out / "freeze.json"):
        raise RuntimeError("Predictions were not produced under this freeze")
    preds = {}
    for name, meta in pmanifest["files"].items():
        if src.sha256_file(out / name) != meta["sha256"]:
            raise RuntimeError(f"Prediction file changed: {name}")
        preds[Path(name).stem] = {r["case_id"]: r for r in src.read_jsonl(out / name)}
    cases = {c["case_id"]: c for c in src.read_jsonl(out / "inputs" / "cases.jsonl")}
    contexts = {c["dossier_id"]: c for c in src.read_jsonl(out / "inputs" / "products.jsonl")}
    facts = {k: gates.PairFacts(v) for k, v in contexts.items()}
    store = src.RawStore(ROOT)
    primary = gates.PRIMARY_CONFIG
    violations, checks = [], {}

    def selection(cid):
        return tuple(a["value"] for a in cases[cid]["rakuten_selected"]["axes"])

    for method in ("A", "B"):
        by_row = defaultdict(set)
        for cid, p in preds[f"{method}-{primary}"].items():
            if p["decision"] == "matched":
                by_row[(p["dossier_id"], p["top_row_key"])].add(selection(cid))
        bad = {k: sorted(v) for k, v in by_row.items() if len(v) > 1}
        checks[f"row_injectivity_{method}"] = {"accepted_rows": len(by_row), "violations": len(bad)}
        violations += [{"check": f"row_injectivity_{method}", "row": list(k), "selections": v} for k, v in bad.items()]

    a, b = preds[f"A-{primary}"], preds[f"B-{primary}"]
    bad = [c for c in b if b[c]["decision"] == "matched"
           and not (a[c]["decision"] == "matched" and a[c]["top_row_key"] == b[c]["top_row_key"])]
    checks["b_within_a"] = {"b_accepts": sum(p["decision"] == "matched" for p in b.values()), "violations": len(bad)}
    violations += [{"check": "b_within_a", "case_id": c} for c in bad]
    bad = [c for c in a if {a[c]["decision"], b[c]["decision"]} == {"matched", "unmatched"}]
    checks["no_opposite"] = {"violations": len(bad)}
    violations += [{"check": "no_opposite", "case_id": c} for c in bad]

    for method in ("A", "B"):
        full = preds[f"{method}-{primary}"]
        for config in gates.SOURCE_CONFIGS:
            if config == primary:
                continue
            outcome = Counter()
            for cid, p in preds[f"{method}-{config}"].items():
                if p["decision"] != "matched":
                    continue
                f = full[cid]
                kind = "same_row" if f["decision"] == "matched" and f["top_row_key"] == p["top_row_key"] else \
                    "review" if f["decision"] == "review" else "flip_" + f["decision"]
                outcome[kind] += 1
                if kind.startswith("flip"):
                    violations.append({"check": f"config_no_flip_{method}_{config}", "case_id": cid,
                                       "config_row": p["top_row_key"], "primary": f["decision"],
                                       "primary_row": f["top_row_key"]})
            checks[f"config_no_flip_{method}_{config}"] = {
                "accepts": sum(outcome.values()), "outcome_under_primary": dict(outcome),
                "violations": sum(v for k, v in outcome.items() if k.startswith("flip"))}

    real_index = {}
    for cid, c in cases.items():
        real_index.setdefault((c["rakuten_selected"]["raw_file"], selection(cid)), cid)
    for method in ("A", "B"):
        evaluators, outcome, real = {}, Counter(), Counter()
        original_preds = preds[f"{method}-{primary}"]
        for cid in sorted(cases):
            original = original_preds[cid]
            if original["decision"] != "matched":
                continue
            ci, key = cases[cid], cases[cid]["dossier_id"]
            evaluators.setdefault(key, gates.make_evaluator(method, facts[key], primary))
            for swap, swapped in gates.sibling_swaps(store, ci):
                r = gates.run_method(method, swapped, facts[key], primary, evaluators[key])
                same = r["top_row_key"] == original["top_row_key"]
                outcome[r["decision"] + ("_same_row" if r["decision"] == "matched" and same else "")] += 1
                if r["decision"] == "matched" and same:
                    violations.append({"check": f"sibling_swap_{method}", "case_id": cid, "swap": swap,
                                       "row": r["top_row_key"]})
                twin = real_index.get((ci["rakuten_selected"]["raw_file"],
                                       tuple(x["value"] for x in swapped["rakuten_selected"]["axes"])))
                if twin:
                    t = original_preds[twin]
                    real["agrees_with_real_case" if (t["decision"], t["top_row_key"]) == (r["decision"], r["top_row_key"])
                         else "differs_from_real_case"] += 1
        checks[f"sibling_swap_{method}"] = {"swaps": sum(outcome.values()), "outcomes": dict(outcome),
                                            "real_case_comparison": dict(real),
                                            "violations": outcome["matched_same_row"]}

    for method in ("A", "B"):
        evaluators, outcome = {}, Counter()
        for cid, p in preds[f"{method}-{primary}"].items():
            if p["decision"] == "review":
                continue
            ci = copy.deepcopy(cases[cid])
            ci["rakuten_selected"]["axes"][0]["value_span"] = None
            key = ci["dossier_id"]
            evaluators.setdefault(key, gates.make_evaluator(method, facts[key], primary))
            r = gates.run_method(method, ci, facts[key], primary, evaluators[key])
            ok = (r["decision"], r["reason"]) == ("review", "unquoted_requirement")
            outcome["review_unquoted" if ok else "decided_" + r["decision"]] += 1
            if not ok:
                violations.append({"check": f"unquoted_guard_{method}", "case_id": cid, "decision": r["decision"]})
        checks[f"unquoted_guard_{method}"] = {"decided_cases": sum(outcome.values()), "outcomes": dict(outcome),
                                              "violations": sum(v for k, v in outcome.items() if k != "review_unquoted")}

    summary = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "labels_read": False,
               "freeze_sha256": src.sha256_file(out / "freeze.json"),
               "prediction_manifest_sha256": src.sha256_file(out / "predictions" / "manifest.json"),
               "checker_sha256": src.sha256_file(Path(__file__)),
               "all_passed": not violations, "violation_count": len(violations), "checks": checks}
    runner.write_new(out / "invariants" / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    runner.write_jsonl_new(out / "invariants" / "violations.jsonl", violations)
    for name, check in checks.items():
        print(name, check)
    print("all_passed", summary["all_passed"])
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
