"""Freeze four product/value facet tasks before any facet answers exist."""
from datetime import datetime, timezone
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
EXP = HERE.parents[1]
OLD = EXP / "results/20261010T185500Z-luna-multirow62"
V6 = EXP / "results/20261010T192800Z-luna-curtain-dimension-v6"
sys.path.insert(0, str(EXP))
import sku_luna_curtain_task as task
import trial_luna_sku_matching as core


def main():
    assert not (HERE / "facet-dispatch-manifest.json").exists()
    cases = core.read(OLD / "inference/inputs.json")
    contexts = core.read(OLD / "inference/source-contexts.json")
    groups = task.build_curtain_lace_requests(cases, contexts)
    assert len(groups) == 4 and sum(g["coverage_count"] for g in groups) == 62
    assert len({cid for g in groups for cid in g["covered_case_ids"]}) == 62
    (HERE / "facet-requests").mkdir()
    (HERE / "facet-answers").mkdir()
    entries = []
    for index, group in enumerate(groups, 1):
        path = HERE / f"facet-requests/{index:02}.json"
        core.save(path, group["request"])
        metadata = {k: v for k, v in group.items() if k != "request"}
        metadata.update({"case_id": group["request_key"], "representative_case_id": group["covered_case_ids"][0],
                         "request": f"facet-requests/{index:02}.json", "answer": f"facet-answers/{index:02}.json",
                         "request_sha256": core.sha(path.read_bytes())})
        entries.append(metadata)
    manifest = {"task_version": "v7-curtain-lace-facet", "planned_calls": 4, "case_coverage": 62,
                "new_whole_matching_calls": 0, "labels_read": False, "entries": entries}
    core.save(HERE / "facet-dispatch-manifest.json", manifest)
    old_protocol = core.read(OLD / "frozen-protocol.json")
    code = {f: core.sha((EXP / f).read_bytes()) for f in old_protocol["code_sha256"]}
    assert code == old_protocol["code_sha256"]
    code.update({f: core.sha((EXP / f).read_bytes()) for f in ("sku_luna_curtain_task.py", "sku_luna_curtain_dimension_alias.py")})
    paths = list((OLD / "inference").rglob("*.json")) + [OLD / "inference/links.jsonl"]
    paths += [V6 / "derived/signature-v3-evaluation.json", V6 / "derived/reviewed-summary.json", V6 / "frozen-protocol.json", HERE / "facet_prepare.py"]
    core.save(HERE / "frozen-protocol.json", {"createdUTC": datetime.now(timezone.utc).isoformat(),
              "method": "compose ONLY Rakuten lace checks with four shared product/value facet tasks; preserve all other matching checks; mandatory dimensions plus isolated single-case semantic reviews",
              "development_reuse": True, "new_distinct_cases": 0, "code_sha256": code,
              "source_sha256": {str(p.relative_to(EXP)): core.sha(p.read_bytes()) for p in paths},
              "facet_request_sha256": {e["request"]: e["request_sha256"] for e in entries},
              "human_verified": False, "modelVersion": None, "usage": None})
    print({"facet_tasks": 4, "covered_pairs": 62, "checks_per_task": 27})


if __name__ == "__main__":
    main()
