"""Reuse v7 product-one facts; try bounded original contents for product two."""
from datetime import datetime, timezone
from pathlib import Path
import copy
import sys

HERE = Path(__file__).resolve().parent
EXP = HERE.parents[1]
OLD = EXP / "results/20261010T185500Z-luna-multirow62"
V6 = EXP / "results/20261010T192800Z-luna-curtain-dimension-v6"
V7 = EXP / "results/20261010T193100Z-luna-curtain-facet-v7"
sys.path.insert(0, str(EXP))
import sku_luna_closed_contents_task as task
import trial_luna_sku_matching as core


def main():
    assert not (HERE / "facet-dispatch-manifest.json").exists()
    cases = core.read(OLD / "inference/inputs.json")
    contexts = core.read(OLD / "inference/source-contexts.json")
    old_groups = core.read(V7 / "facet-dispatch-manifest.json")["entries"]
    uncertain_products = {e["au_product_id"] for e in old_groups if all(c["status"] == "unknown" for c in core.read(V7 / e["answer"])["checks"])}
    assert len(uncertain_products) == 1
    groups = {g["request_key"]: g for g in task.build_product_requests([c for c in cases if c["au_product_id"] in uncertain_products], contexts)}
    (HERE / "facet-requests").mkdir()
    (HERE / "facet-answers").mkdir()
    entries, new, bounded = [], [], {}
    for index, old in enumerate(old_groups, 1):
        e = copy.deepcopy(old)
        e["request"], e["answer"] = f"facet-requests/{index:02}.json", f"facet-answers/{index:02}.json"
        if old["au_product_id"] in uncertain_products:
            group = groups[old["request_key"]]
            assert group["covered_case_ids"] == old["covered_case_ids"]
            core.save(HERE / e["request"], group["request"])
            e["inference_required"] = True
            representative = next(c for c in cases if c["case_id"] == e["representative_case_id"])
            bounded[e["request_key"]] = task._raw_contents_block(representative, contexts[representative["case_id"]])
        else:
            (HERE / e["request"]).write_bytes((V7 / old["request"]).read_bytes())
            (HERE / e["answer"]).write_bytes((V7 / old["answer"]).read_bytes())
            e["inference_required"] = False
            e["reused_from"] = {"round": "v7", "request_sha256": core.sha((V7 / old["request"]).read_bytes()), "answer_sha256": core.sha((V7 / old["answer"]).read_bytes())}
        e["request_sha256"] = core.sha((HERE / e["request"]).read_bytes())
        entries.append(e)
        if e["inference_required"]: new.append(e)
    core.save(HERE / "facet-dispatch-manifest.json", {"planned_calls": 4, "new_model_tasks": 2, "case_coverage": 62, "labels_read": False, "entries": entries})
    core.save(HERE / "new-facet-dispatch-manifest.json", {"planned_calls": 2, "entries": new, "labels_read": False})
    core.save(HERE / "bounded-contents-provenance.json", bounded)
    old_protocol = core.read(V7 / "frozen-protocol.json")
    code = dict(old_protocol["code_sha256"])
    assert all(core.sha((EXP / f).read_bytes()) == h for f, h in code.items())
    code["sku_luna_closed_contents_task.py"] = core.sha((EXP / "sku_luna_closed_contents_task.py").read_bytes())
    paths = list((OLD / "inference").rglob("*.json")) + [OLD / "inference/links.jsonl", V6 / "frozen-protocol.json", HERE / "facet_prepare.py", HERE / "bounded-contents-provenance.json"]
    paths += [V7 / "frozen-protocol.json", V7 / "derived-v2/signature-v3-manifest.json", V7 / "derived-v2/signature-v3-evaluation.json"]
    paths += [V7 / e[k] for e in old_groups for k in ("request", "answer")]
    core.save(HERE / "frozen-protocol.json", {"createdUTC": datetime.now(timezone.utc).isoformat(), "method": "bounded original contents HTML for uncertain product; retain other product facts; same62 development comparison, complete package composition judged by Luna, not product-ID verdict",
              "new_distinct_cases": 0, "code_sha256": code, "source_sha256": {str(p.relative_to(EXP)): core.sha(p.read_bytes()) for p in paths},
              "facet_request_sha256": {e["request"]: e["request_sha256"] for e in entries}, "new_facet_tasks": 2, "modelVersion": None, "usage": None, "human_verified": False})
    print({"new_facet_tasks": 2, "reused": 2, "covered_cases": 62})


if __name__ == "__main__":
    main()
