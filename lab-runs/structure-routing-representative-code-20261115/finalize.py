import hashlib
import importlib.util
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


ref = read(ROOT / "evaluation-policy-ref.json")
policy_path = ROOT / "evaluation-policy-frozen.json"
assert hashlib.sha256(policy_path.read_bytes()).hexdigest() == ref["sha256"]
source = REPO / ref["classifier_file"]
assert hashlib.sha256(source.read_bytes()).hexdigest() == ref["classifier_sha256"]
spec = importlib.util.spec_from_file_location("representative_fixed_scorer", source)
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)
policy = read(policy_path)
adopted = {}
all_trials = []
for base in (ROOT, ROOT / "repair-run"):
    for row in rows(base / "results.jsonl"):
        scored = scorer.evaluate_row(row, policy, base)
        record = {"source": str(base.relative_to(ROOT)) or ".", "raw": row, "score": scored}
        all_trials.append(record)
        adopted[row["id"], row["phase"]] = record
costs = rows(ROOT / "cost-ledger.jsonl") + [row for row in rows(ROOT / "repair-run/cost-ledger.jsonl") if not row.get("shared_prior")]
summary_rows = []
metrics = Counter()
for target in rows(ROOT / "targets.jsonl"):
    sid = target["id"]
    rep = read(ROOT / "sites" / sid / "representatives.json")
    initial = set(rep["initial_document_urls"])
    cold_targets = {url for item in adopted[sid, "cold"]["raw"]["selected_items"] for url in (item["url"], item["final_url"])}
    entry = {"id": sid, "representative_body_count": len(initial), "phases": {}}
    for phase in ("cold", "warm"):
        selected = adopted[sid, phase]
        row, score = selected["raw"], selected["score"]
        saved = [item for item in row["selected_items"] if item["saved"]]
        assert all(item["url"] not in initial for item in saved)
        if phase == "warm":
            assert all(item["url"] not in cold_targets for item in saved)
        scroll_states = {(item["url"], item["state_id"]) for item in row["scroll_states"]}
        before_links = {item["url"] for item in read(ROOT / selected["source"] / row["snapshots"]["before"])["links"]}
        true_scroll = [item for item in saved if item["url"] not in before_links and (item["observed_edge"]["from"], item["observed_edge"]["state_id"]) in scroll_states]
        strict_urls = {item["url"] for item in score["items"] if item["classification"] == "strict_success"}
        phase_entry = {"status": row["status"], "saved_new_HTML": len(saved), "expected_type_strict": score["strict_success"],
                       "unverified": score["unverified"], "scroll_revealed_saved_HTML": len(true_scroll),
                       "scroll_revealed_expected_type_strict": sum(item["url"] in strict_urls for item in true_scroll),
                       "source": selected["source"], "process_pid": row["separate_process_pid"],
                       "source_ref": f"{selected['source']}/results.jsonl#{sid}:{phase}"}
        entry["phases"][phase] = phase_entry
        metrics[f"{phase}_saved_HTML"] += len(saved)
        metrics[f"{phase}_type_strict"] += score["strict_success"]
        metrics[f"{phase}_sites_with_saved_HTML"] += bool(saved)
        metrics[f"{phase}_sites_with_type_strict"] += bool(score["strict_success"])
        metrics["scroll_revealed_saved_HTML"] += len(true_scroll)
        assert row["external_llm_calls"] == row["local_inference"]["calls"] == 0
    entry["code_sha256"] = hashlib.sha256((ROOT / "sites" / sid / "crawl.js").read_bytes()).hexdigest()
    for selected in (adopted[sid, "cold"], adopted[sid, "warm"]):
        assert selected["raw"]["crawl_sha256"] == entry["code_sha256"]
    assert entry["phases"]["cold"]["process_pid"] != entry["phases"]["warm"]["process_pid"]
    summary_rows.append(entry)
cost_totals = {field: sum(row.get(field) or 0 for row in costs) for field in ("document_reserved", "ui_operations", "http_requests", "http_responses", "xhr_fetch", "response_bytes", "browser_ms")}
site_costs = defaultdict(lambda: {"documents": 0, "operations": 0})
for row in costs:
    site_costs[row["id"]]["documents"] += row["document_reserved"]
    site_costs[row["id"]]["operations"] += row["ui_operations"]
for target in rows(ROOT / "targets.jsonl"):
    assert site_costs[target["id"]]["documents"] <= (40 if target["practice"] else 15)
    assert site_costs[target["id"]]["operations"] <= (100 if target["practice"] else 30)
out = {"sites": summary_rows, "metrics": dict(metrics), "all_trial_rows": len(all_trials), "adopted_phase_rows": len(adopted),
       "costs_including_initial_selection_and_all_repair_trials": cost_totals, "per_site_costs": dict(site_costs),
       "model_usage": read(ROOT / "model-usage.json"), "fixed_target_candidates": read(ROOT / "fixed-target-ref.json")["target_counts"],
       "fixed_target_candidates_are_not_independently_verified_gold": True, "fixed_url_semantic_recall": None,
       "G_graph_path_search": {"status": "not_measured", "denominator": 0},
       "R_detail": "HTML body unseen by code generator, same trained listing allowed", "R_listing": "Previously unseen listing body; same generated code and no additional model judgment"}
write(ROOT / "summary.json", out)
write(ROOT / "final-evaluation.json", {"trials": [{"source": item["source"], "score": item["score"]} for item in all_trials]})
(ROOT / "operational-results.jsonl").write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in summary_rows), encoding="utf-8")
print(json.dumps({"metrics": dict(metrics), "costs": cost_totals, "per_site_costs": dict(site_costs)}, ensure_ascii=False))
