import json
from pathlib import Path


root = Path(__file__).resolve().parent
child = root / "repair-run"


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


parent_rows = rows(root / "results.jsonl")
child_rows = rows(child / "results.jsonl")
decisions = []
for result in child_rows:
    if result["phase"] != "cold":
        continue
    site_id = result["id"]
    site_target = next(row for row in rows(child / "targets.jsonl") if row["id"] == site_id)
    operations = sum(row.get("cost", {}).get("ui_operations", 0) for row in parent_rows + child_rows if row["id"] == site_id)
    remaining_ops = (100 if site_target["practice"] else 30) - operations
    remaining_docs = result["budget"]["cumulative_document_cap"] - result["budget"]["cumulative_document_requests"]
    reason = None
    if result.get("refusal") or result["status"] in ("error", "access_stopped"):
        reason = "cold_access_or_error"
    elif any(item.get("basic_html_check", {}).get("reason") in ("blocked_or_login", "error_page", "http_401", "http_403", "http_429") for item in result.get("selected_items", [])):
        reason = "cold_access_or_error"
    elif remaining_docs < 4 or remaining_ops < 16:
        reason = "cumulative_document_or_operation_budget"
    decisions.append({"id": site_id, "eligible": reason is None, "skip_reason": reason,
                      "remaining_documents": remaining_docs, "remaining_ui_operations": remaining_ops,
                      "warm_max_ui_operations_bound": 16, "prior_results_included": True})
(child / "warm-eligibility.json").write_text(json.dumps(decisions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(",".join(row["id"] for row in decisions if row["eligible"]))
