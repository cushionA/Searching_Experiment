import hashlib
import importlib.util
import json
from collections import Counter, defaultdict
from pathlib import Path


RUN = Path(__file__).resolve().parent


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def rows(path):
    try:
        return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def phase_metadata(root, sid, phase, chosen, entry_meta):
    base = root / "sites" / sid
    result = read(base / phase / "result.json", {})
    result = chosen or result
    prepare = read(base / f"prepare-{phase}-status.json", {})
    holdout = read(base / "holdout-start.json", {})
    code = read(base / "code-manifest.json", {})
    status = result.get("status")
    error = result.get("error") or prepare.get("error")
    if chosen:
        reason = status if status not in {None, "html_saved"} else None
        stage = "execute" if reason or error else None
    elif entry_meta.get("blocked"):
        reason, stage = "entry_blocked", "entry"
    elif not (base / "representatives.json").is_file():
        reason, stage = "representatives_missing", "sampling"
    elif prepare.get("error") or prepare and prepare.get("status") not in {"prepared", "ready", "ok", None}:
        reason, stage = prepare.get("status"), "prepare"
    elif phase == "warm" and not holdout:
        reason, stage = "holdout_missing", "holdout"
    elif not code.get("sha256"):
        reason, stage = "code_missing", "code_generation"
    else:
        reason, stage = "not_executed", "execute"
    if error:
        reason = reason or str(error)
        stage = "prepare" if prepare.get("error") else "execute"
    return {"status": status or (prepare.get("status") if prepare else None) or "not_run",
            "reason": reason, "failure_stage": stage, "error": error,
            "code_sha256": result.get("crawl_sha256") or code.get("sha256")}


def snapshot_urls(ref, root, fallback):
    if not ref:
        return set()
    path = Path(str(ref).replace("\\", "/"))
    if not path.is_absolute():
        path = root / path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        base = data.get("url") or data.get("page_url") or fallback
        from urllib.parse import urljoin
        return {urljoin(base, x.get("url") or x.get("href")) for x in data.get("links", []) if x.get("url") or x.get("href")}
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        return set()


def normalize_item_paths(item):
    normalized = dict(item)
    for key in ("source_html", "rendered_dom"):
        if normalized.get(key):
            normalized[key] = str(normalized[key]).replace("\\", "/")
    return normalized


def execute_outcomes(root, phase):
    found = {}
    for path in sorted(root.glob(f"execute-{phase}-*.log")):
        data = read(path, {})
        outcomes = data.get("outcomes", []) if isinstance(data, dict) else []
        for outcome in outcomes:
            if isinstance(outcome, dict) and outcome.get("id"):
                found[str(outcome["id"])] = outcome
    return found


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, default=RUN)
    args = parser.parse_args()
    root = args.run.resolve()
    ref = read(root / "evaluation-policy-ref.json")
    local_policy = root / "evaluation-policy-frozen.json"
    policy_path = local_policy if local_policy.exists() else Path(ref["policy_file"])
    assert hashlib.sha256(policy_path.read_bytes()).hexdigest() == ref["sha256"]
    classifier = root.parent.parent / ref["classifier_file"]
    assert hashlib.sha256(classifier.read_bytes()).hexdigest() == ref["classifier_sha256"]
    policy = read(policy_path)
    spec = importlib.util.spec_from_file_location("frozen_sitemap_score", classifier)
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    actual = rows(root / "results.jsonl")
    bootstrap_rows = rows(root / "initialresults.jsonl")
    bootstrap = {str(x.get("id")): x for x in bootstrap_rows}
    by_phase = defaultdict(list)
    for row in actual:
        by_phase[(str(row.get("id")), str(row.get("phase")))].append(row)
    target_rows = rows(root / "targets.jsonl")
    portable_targets = root / "fixed-targets-frozen.json"
    private_targets = Path.home() / ".codex/evaluations/structure-routing-representative-code-all80-20261116/targets.json"
    fixed_path = portable_targets if portable_targets.is_file() else private_targets
    fixed = read(fixed_path, {"targets": {}})
    cost_rows = rows(root / "cost-ledger.jsonl")
    eval_cost_rows = rows(root / "evaluation-cost.jsonl")
    model_usage = read(root / "model-usage.json", {})
    protocol = read(root / "protocol.json", {})
    warm_outcomes = execute_outcomes(root, "warm")
    metrics = Counter()
    out_rows = []
    canonical = []
    item_evaluations = []
    site_summary = []
    for target in target_rows:
        sid = str(target["id"])
        source = "adopted_pilot" if sid in protocol.get("same_method_pilot_reuse_ids", []) else "new_all80"
        repfile = root / "sites" / sid / "representatives.json"
        rep = read(repfile, {})
        initial = set(rep.get("initial_document_urls", []))
        phase_summaries = {}
        phase_saved_urls = {}
        scored_by_phase = {}
        meta_by_phase = {}
        for phase in ("cold", "warm"):
            trials = by_phase.get((sid, phase), [])
            chosen = trials[-1] if trials else None
            status_path = root / "sites" / sid / phase / "status.json"
            external_status = read(status_path, {})
            evaluated = []
            if chosen:
                evaluated = [scorer.evaluate_item(normalize_item_paths(item), policy.get("sites", {}).get(sid, {}), root)
                             for item in chosen.get("selected_items", []) if isinstance(item, dict)]
            saved = []
            strict = 0
            unverified = 0
            type_unverified_saved = 0
            invalid_saved_evidence = 0
            for item, result in zip(chosen.get("selected_items", []) if chosen else [], evaluated):
                item_paths = normalize_item_paths(item)
                source_path = Path(item_paths.get("source_html") or "")
                rendered_path = Path(item_paths.get("rendered_dom") or "")
                if not source_path.is_absolute():
                    source_path = root / source_path
                if not rendered_path.is_absolute():
                    rendered_path = root / rendered_path
                usable = result.get("saved_html_valid") and item.get("status") == 200 and source_path.is_file() and rendered_path.is_file()
                if usable:
                    saved.append(item)
                type_unverified_saved += bool(usable and result.get("classification") == "unverified")
                invalid_saved_evidence += not usable
                item_evaluations.append({"id": sid, "phase": phase, "usable_HTML_saved": bool(usable), **result})
                strict += result.get("classification") == "strict_success"
                unverified += result.get("classification") == "unverified"
            saved_urls = {str(x.get("url")) for x in saved if x.get("url")}
            selected_urls = [str(x.get("url")) for x in (chosen.get("selected_items", []) if chosen else []) if x.get("url")]
            duplicate_count = len(selected_urls) - len(set(selected_urls))
            metrics[f"{phase}_duplicate_selected_urls"] += duplicate_count
            metrics[f"{phase}_duplicate_result_rows"] += max(0, len(trials) - 1)
            phase_saved_urls[phase] = saved_urls
            scored_by_phase[phase] = evaluated
            status = chosen.get("status") if chosen else external_status.get("status", "not_run")
            phase_meta = phase_metadata(root, sid, phase, chosen, read(root / "sites" / sid / "representative.json", {}))
            if not chosen and phase == "warm":
                outcome = warm_outcomes.get(sid, {})
                cold_meta = read(root / "sites" / sid / "cold" / "result.json", {})
                if outcome.get("status") == "cold_phase_ineligible" or cold_meta.get("status") in {"access_stopped", "entry_access_stopped", "blocked", "stopped"}:
                    phase_meta.update(reason="cold_phase_ineligible", failure_stage="cold_eligibility", error=outcome.get("error"))
                elif outcome and phase_meta["failure_stage"] not in {"entry", "sampling", "prepare", "code_generation"}:
                    phase_meta.update(reason=outcome.get("status") or outcome.get("error"), failure_stage="execute", error=outcome.get("error"))
            meta_by_phase[phase] = phase_meta
            metrics[f"{phase}_rows"] += 1
            metrics[f"{phase}_observed"] += bool(chosen)
            metrics[f"{phase}_raw_dom_saved"] += len(saved)
            metrics[f"{phase}_expected_type_strict"] += strict
            metrics[f"{phase}_unverified"] += unverified
            metrics[f"{phase}_type_unverified_saved"] += type_unverified_saved
            metrics[f"{phase}_invalid_saved_evidence"] += invalid_saved_evidence
            metrics[f"{phase}_not_run"] += chosen is None
            metrics[f"{phase}_observed_items"] += len(chosen.get("selected_items", [])) if chosen else 0
            metrics[f"{phase}_evaluable_items"] += len(evaluated)
            metrics[f"{phase}_usable_html_sites"] += bool(saved)
            metrics[f"{phase}_strict_sites"] += strict > 0
            metrics[f"{phase}_unverified_sites"] += unverified > 0
            metrics[f"{phase}_body_unseen_sites"] += bool(saved_urls - initial - (phase_saved_urls.get("cold", set()) if phase == "warm" else set()))
            metrics[f"{phase}_same_url_sites"] += bool(saved_urls & (initial | (phase_saved_urls.get("cold", set()) if phase == "warm" else set())))
            metrics[f"{phase}_failure_stage_{phase_meta['failure_stage'] or 'none'}"] += bool(phase_meta["reason"])
            metrics[f"{phase}_reason_{phase_meta['reason'] or 'none'}"] += bool(phase_meta["reason"])
            snap = chosen.get("snapshots", {}) if chosen else {}
            before_urls = snapshot_urls(snap.get("before"), root, chosen.get("seed_url") if chosen else None)
            after_urls = snapshot_urls(snap.get("after"), root, chosen.get("seed_url") if chosen else None)
            scroll_states = {(x.get("url"), x.get("state_id")) for x in (chosen.get("scroll_states", []) if chosen else [])}
            edge_saved = [item for item in saved if item.get("url") not in before_urls and isinstance(item.get("observed_edge"), dict) and (item["observed_edge"].get("from"), item["observed_edge"].get("state_id")) in scroll_states]
            metrics[f"{phase}_scroll_fresh_edge_saved"] += len(edge_saved)
            phase_summaries[phase] = {"status": status, "reason": phase_meta["reason"], "failure_stage": phase_meta["failure_stage"], "error": phase_meta["error"], "raw_dom_saved": len(saved), "usable_html_sites": bool(saved), "usable_html_items": len(saved), "expected_type_strict": strict,
                                      "verification_status": "expected_type_confirmed" if strict else "HTML_saved_type_unverified" if saved else "HTML_invalid_or_missing" if evaluated else "no_target_saved" if chosen else "not_run",
                                      "unverified_sites": bool(unverified),
                                      "duplicate_selected_urls": duplicate_count,
                                      "duplicate_result_rows": max(0, len(trials) - 1),
                                      "candidate_saved_flags": sum(x.get("saved") is True for x in chosen.get("selected_items", [])) if chosen else 0,
                                      "HTML_files_written": sum(bool(x.get("source_html") and x.get("rendered_dom")) for x in chosen.get("selected_items", [])) if chosen else 0,
                                      "unverified": unverified, "type_unverified_saved": type_unverified_saved,
                                      "invalid_saved_evidence": invalid_saved_evidence, "not_run": chosen is None,
                                      "scroll_fresh_edge_saved": len(edge_saved),
                                      "same_url_as_initial_or_cold": len(saved_urls & (initial | (phase_saved_urls.get("cold", set()) if phase == "warm" else set()))),
                                      "body_unseen_url_count": len(saved_urls - initial - (phase_saved_urls.get("cold", set()) if phase == "warm" else set())),
                                      "model_calls": chosen.get("external_llm_calls") if chosen else None,
                                      "process_pid": chosen.get("separate_process_pid") if chosen else None,
                                      "status_evidence": str(status_path.relative_to(root)) if status_path.is_file() else None}
            canonical.append({"id": sid, "phase": phase, **phase_summaries[phase]})
        for phase in ("cold", "warm"):
            metrics[f"{phase}_observed_sites"] += bool(by_phase.get((sid, phase)))
            metrics[f"source_{source}_{phase}_rows"] += bool(by_phase.get((sid, phase)))
        candidates = set(fixed.get("targets", {}).get(sid, []))
        urls_any = phase_saved_urls["cold"] | phase_saved_urls["warm"]
        reached = candidates & urls_any
        metrics["fixed_candidate_urls"] += len(candidates)
        metrics["fixed_candidate_urls_reached"] += len(reached)
        type_matched = {str(url) for values in scored_by_phase.values() for x in values if x.get("classification") == "strict_success" for url in (x.get("url"), x.get("final_url")) if url}
        metrics["candidate_type_strict"] += len(candidates & type_matched)
        metrics["site_rows"] += 1
        entry_meta = read(root / "sites" / sid / "representative.json", {})
        entry_status = "entry_access_stopped" if entry_meta.get("blocked") else "entry_HTML_saved" if entry_meta.get("status") == 200 else "entry_recorded" if entry_meta else "not_recorded"
        code_sha = {p: (by_phase.get((sid, p), [{}])[-1].get("crawl_sha256") if by_phase.get((sid, p)) else None) for p in ("cold", "warm")}
        same_code = bool(code_sha["cold"] and code_sha["warm"] and code_sha["cold"] == code_sha["warm"])
        metrics["same_code_sha_sites"] += same_code
        metrics["different_code_sha_sites"] += bool(code_sha["cold"] and code_sha["warm"] and not same_code)
        pids = {p: (by_phase.get((sid, p), [{}])[-1].get("separate_process_pid") if by_phase.get((sid, p)) else None) for p in ("cold", "warm")}
        pid_diff = bool(pids["cold"] and pids["warm"] and pids["cold"] != pids["warm"])
        metrics["different_process_pid_sites"] += pid_diff
        metrics[f"source_{source}_sites"] += 1
        site_summary.append({"id": sid, "status": "observed" if any(by_phase.get((sid, p)) for p in ("cold", "warm")) else entry_status,
                             "bootstrap_status": entry_status,
                             "source": source,
                             "code_sha256": code_sha, "same_code_sha256_cold_warm": same_code,
                             "process_pid": pids, "different_process_pid_cold_warm": pid_diff,
                             "phases": phase_summaries,
                             "fixed_candidate": {"count": len(candidates), "url_reached": len(reached), "strict_type_validated": len(candidates & type_matched)},
                             "candidate_reach_is_not_type_validation": True})
    cost_totals = defaultdict(lambda: Counter())
    unknown_costs = Counter()
    for row in cost_rows:
        if row.get("attribution_only"):
            continue
        phase = str(row.get("phase", "unknown"))
        group = "initial_or_reused_pilot" if phase in {"discover", "representative", "sampling", "code_generation"} else "selection_and_preparation" if phase in {"evaluation", "prepare", "holdout"} else phase
        for key in ("document_reserved", "ui_operations", "http_requests", "http_responses", "xhr_fetch", "response_bytes", "browser_ms"):
            unknown_costs[key] += row.get(key) is None
            cost_totals[group][key] += row.get(key) or 0
    generation_tokens = protocol.get("native_tokens")
    run_status = read(root / "STATUS.json", {})
    out = {"evaluation": "representative_code_all80", "run_status": run_status, "policy_sha256": ref.get("sha256"), "classifier_file": ref.get("classifier_file"),
           "fixed_targets_source": str(fixed_path) if fixed_path.is_file() else None,
           "classifier_sha256": ref.get("classifier_sha256"), "sites": site_summary, "rows": canonical,
           "metrics": dict(metrics), "costs_by_ledger_phase": {k: dict(v) for k, v in cost_totals.items()},
           "evaluation_cost_attribution_only": eval_cost_rows,
           "model_usage": model_usage, "generation_native_tokens": generation_tokens,
           "denominators": {"target_sites": len(target_rows), "operational_phase_rows_expected": len(target_rows) * 2,
                            "structural_inputs_acquired_sites": sum((root / "sites" / str(t["id"]) / "representatives.json").is_file() and (root / "sites" / str(t["id"]) / "code-request.json").is_file() for t in target_rows),
                            "phase_observed_sites": {p: metrics[f"{p}_observed_sites"] for p in ("cold", "warm")},
                            "phase_observed_items": {p: metrics[f"{p}_observed_items"] for p in ("cold", "warm")},
                            "phase_evaluable_items": {p: metrics[f"{p}_evaluable_items"] for p in ("cold", "warm")}},
           "unknown_cost_record_counts": dict(unknown_costs),
           "HTML_saved_definition": "HTTP200 plus source+rendered files pass frozen saved_evidence checks; HTTP200 alone or a candidate flag is insufficient. Expected type is a separate result.",
           "model_usage_unknown_not_zero": generation_tokens is None or model_usage.get("actual_native_tokens") is None,
           "G_graph_path_search": {"status": "not_measured", "denominator": 0},
           "R_definition": "same generated code in separate cold/warm process; saved HTML plus page-type classification reported independently",
           "scroll_fresh_edge_definition": "saved URL first appears after scroll and observed edge references an actual scroll state",
           "fixed_candidate_reach_is_not_semantic_validation": True}
    out["semantic_recall"] = None
    write(root / "summary.json", out)
    write(root / "evaluation.json", out)
    (root / "operational-results.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in canonical), encoding="utf-8")
    (root / "item-evaluation.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in item_evaluations), encoding="utf-8")
    print(json.dumps({"sites": len(target_rows), "rows": len(canonical), "metrics": dict(metrics)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
