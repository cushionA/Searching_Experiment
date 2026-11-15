import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


root = Path(__file__).resolve().parent
child = root / "repair-run"
repo = root.parent.parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if child.exists():
    raise SystemExit("repair_run_already_exists")
policy_ref = read(root / "evaluation-policy-ref.json")
for path, expected in ((Path(policy_ref["policy_file"]), policy_ref["sha256"]), (repo / "experiments/structure-routing/sitemap-agent-score.py", policy_ref["classifier_source_sha256"])):
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit("frozen_evaluation_changed")
results = rows(root / "results.jsonl")
protocol = read(root / "protocol.json")
site_prior = dict(protocol["prior_shared_site_document_reservations"])
global_prior = protocol["prior_shared_browser_document_reservations_upper_bound"]
for row in results + rows(root / "attempts.jsonl"):
    count = row.get("budget", {}).get("document_reserved", row.get("document_requests") or row.get("document_requests_reserved_upper_bound") or 0)
    site_prior[row["id"]] = site_prior.get(row["id"], 0) + count
    global_prior += count
eligible = []
excluded = []
for row in results:
    if row["phase"] != "cold" or row.get("refusal") != "robots_or_document_guard":
        continue
    events = rows(root / row["progress"])
    hard_statuses = [event for event in events if event.get("event") == "goto_end" and event.get("status") in (401, 403, 429)]
    if hard_statuses:
        excluded.append({"id": row["id"], "reason": "actual_http_refusal", "events": hard_statuses})
    else:
        eligible.append(row["id"])
targets = [target for target in rows(root / "targets.jsonl") if target["id"] in eligible]
child.mkdir()
(child / "targets.jsonl").write_text("".join(json.dumps(target, ensure_ascii=False) + "\n" for target in targets), encoding="utf-8")
protocol.update({"created_at": datetime.now(timezone.utc).isoformat(), "registered_sites": eligible,
                 "repair_of": str(root), "repair_reason": "Child document denial incorrectly aborted valid main document; preserve host/method/safe-route/document guard and actual HTTP refusal stops.",
                 "prior_shared_browser_document_reservations_upper_bound": global_prior,
                 "prior_shared_site_document_reservations": {key: site_prior[key] for key in eligible},
                 "same_method_existing_results_reused": [], "new_url_choice_model_jobs": 0,
                 "metadata_requests_this_repair": 0, "input_choices_and_policy_unchanged": True,
                 "warm_strategy": "Only eligible non-refused phases with remaining document budget and at least16 remaining UI operations; otherwise retain explicit budget skip."})
write(child / "protocol.json", protocol)
shutil.copyfile(root / "evaluation-policy-ref.json", child / "evaluation-policy-ref.json")
copied = []
for target in targets:
    src = root / "sites" / target["id"]
    dst = child / "sites" / target["id"]
    dst.mkdir(parents=True)
    for name in ("inventory.json", "request.json", "answer.json", "groups.json", "preparation.json"):
        shutil.copyfile(src / name, dst / name)
        copied.append({"id": target["id"], "file": name, "sha256": hashlib.sha256((dst / name).read_bytes()).hexdigest()})
write(child / "repair-provenance.json", {"parent": str(root), "parent_results_sha256": hashlib.sha256((root / "results.jsonl").read_bytes()).hexdigest(),
                                        "eligible": eligible, "excluded": excluded, "parent_global_documents_upper_bound": global_prior,
                                        "new_model_calls": 0, "new_metadata_requests": 0, "input_hashes": copied,
                                        "browser_source_sha256": hashlib.sha256((repo / "experiments/structure-routing/sitemap-agent-browser.mjs").read_bytes()).hexdigest()})
print(json.dumps({"child": str(child), "eligible": eligible, "excluded": excluded, "prior_global_documents_upper_bound": global_prior}))
