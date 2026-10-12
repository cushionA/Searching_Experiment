#!/usr/bin/env python3
"""Prepare the label-blind next DEV source-NLI cohort after coordinate fixes are frozen."""
from __future__ import annotations
import json, hashlib, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
sys.path.insert(0,str(HERE))
import sku_directional_choice_nli_au_coordinate_v1 as adapter
COHORT=ROOT/".lab-output/sku-next-dev-coordinate-refids-20261013-v1"
SOURCE=ROOT/".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
OUT=COHORT/"source-nli-v3"
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    if OUT.exists(): raise FileExistsError(OUT)
    result=adapter.prepare(COHORT,SOURCE,OUT)
    freeze=json.loads((OUT/"freeze.json").read_text(encoding="utf-8"))
    if (freeze.get("selected_split")!="dev" or freeze.get("independent_test_set") is not False
            or freeze.get("labels_read") is not False or freeze.get("test_split_read") is not False
            or freeze.get("inference_run") is not False or freeze.get("scores_or_gate_outputs_created") is not False):
        raise ValueError("source-NLI v3 did not preserve the blind source-only contract")
    summary={"schema_version":"sku-next-dev-coordinate-source-nli-v3-report-v1","dataset":"sku-next-dev-coordinate-refids-20261013-v1","prepared_directory":"source-nli-v3","selected_split":"dev","independent_test_set":False,"selected_products":10,"selected_cases":20,"selected_rows":320,"original_bidirectional_tasks":1220,"requests":773,"documents":20,"prepared_semantic_pairs":freeze["prepared_pair_count"],"ready_semantic_pairs":freeze["ready_pair_count"],"abstention_tasks":freeze["abstention_task_count"],"source_fact_errors":freeze["source_fact_errors"],"labels_read":False,"test_split_read":False,"inference_run":False,"scores_created":False,"production_adoption_allowed":False,"selection_policy_sha256":sha(COHORT/"selection-policy.json"),"selection_plan_sha256":sha(COHORT/"selection-plan.json"),"input_manifest_sha256":sha(COHORT/"manifest.json"),"freeze_sha256":sha(OUT/"freeze.json"),"output_sha256":freeze["output_sha256"]}
    with (COHORT/"source-only-report-v3.json").open("x",encoding="utf-8") as f: json.dump(summary,f,ensure_ascii=False,indent=2);f.write("\n")
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
