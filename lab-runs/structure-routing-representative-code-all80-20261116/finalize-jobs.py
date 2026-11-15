import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
stages = {"sampling": {"jobs": 0, "sites": 0, "input_artifact_characters": 0}, "code": {"jobs": 0, "sites": 0, "input_artifact_characters": 0}}
failures = []
for file in sorted((ROOT / "model-jobs").glob("*.request.json")):
    job = json.loads(file.read_text(encoding="utf-8"))
    outputs = {}
    for item in job["inputs"]:
        input_file = Path(item["input_file"])
        if hashlib.sha256(input_file.read_bytes()).hexdigest() != item["input_sha256"]:
            failures.append(f"changed_input:{job['job_id']}:{item['id']}")
        names = ("sampling-answer.json",) if job["stage"] == "sampling" else ("crawl.js", "generation-answer.json")
        for name in names:
            output = ROOT / "sites" / item["id"] / name
            if output.exists():
                outputs[str(output.relative_to(ROOT))] = hashlib.sha256(output.read_bytes()).hexdigest()
            else:
                failures.append(f"missing_output:{job['job_id']}:{item['id']}:{name}")
    answer = file.with_name(job["job_id"] + ".answer.json")
    if not answer.exists():
        failures.append(f"missing_job_answer:{job['job_id']}")
    job.update({"state": "completed" if len(outputs) == len(job["inputs"]) * (1 if job["stage"] == "sampling" else 2) else "incomplete_initial_job",
                "audit_at": datetime.now(timezone.utc).isoformat(), "output_sha256": outputs,
                "native_model_invocations": None, "actual_native_tokens": None})
    file.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    stage = stages[job["stage"]]
    stage["jobs"] += 1
    stage["sites"] += len(job["site_ids"])
    stage["input_artifact_characters"] += job["input_artifact_characters"]
prior_file = ROOT / "adopted-pilot" / "model-usage.json"
prior = json.loads(prior_file.read_text()) if prior_file.exists() else {"actual_native_tokens": None, "source": "prior six-site pilot"}
usage = {"requested_model": "gpt-6-luna", "new_initial_jobs": stages, "adopted_pilot_usage": prior,
         "actual_native_tokens": None, "actual_native_context_characters": None, "native_model_invocations": None,
         "model_identity_independently_verified": False, "unknown_usage_not_zero": True,
         "execution_model_calls": 0, "per_target_HTML_model_judgment_calls": 0, "local_model_calls": 0,
         "LLM_repairs_after_code_freeze": 0, "heldout_HTML_supplied_to_initial_actors": False,
         "engineering_and_final_report_native_usage": None, "input_character_counts_are_artifact_sizes_not_token_usage": True}
(ROOT / "model-usage.json").write_text(json.dumps(usage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
audit = {"ok": not failures, "failures": failures, "new_initial_jobs": stages}
(ROOT / "job-validation.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(audit, ensure_ascii=False))
