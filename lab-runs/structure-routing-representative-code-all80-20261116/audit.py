import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urldefrag


RUN = Path(__file__).resolve().parent
FIELDS = ("document_reserved", "ui_operations", "http_requests", "http_responses", "xhr_fetch", "response_bytes", "browser_ms")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.is_file() else []


def local(ref):
    return RUN / str(ref).replace("\\", "/")


def main():
    failures = []
    targets = rows(RUN / "targets.jsonl")
    protocol = read(RUN / "protocol.json")
    ledger = rows(RUN / "cost-ledger.jsonl")
    actual = rows(RUN / "results.jsonl")
    counts = Counter()
    totals = defaultdict(Counter)
    site_costs = defaultdict(Counter)
    seen_sessions = set()
    for row in ledger:
        session = row["session"].replace("\\", "/")
        if session in seen_sessions:
            failures.append("duplicate_cost_session:" + session)
        seen_sessions.add(session)
        directory = local(session)
        reservations = rows(directory / "document-reservations.jsonl")
        metadata = read(directory / "session.json") if (directory / "session.json").is_file() else None
        if metadata is None:
            failures.append("missing_session_metadata:" + session)
        elif metadata.get("cost") != {k: v for k, v in row.items() if k != "source_run"}:
            original = metadata.get("cost", {})
            if any(original.get(k) != row.get(k) for k in FIELDS):
                failures.append("session_cost_mismatch:" + session)
        if len(reservations) != row["document_reserved"]:
            failures.append("document_reservation_mismatch:" + session)
        counts["reservation_events"] += len(reservations)
        counts["main_document_reservations"] += sum(x.get("main_frame") is True for x in reservations)
        group = "adopted_pilot" if row.get("source_run") else "new_all80"
        for key in FIELDS:
            if row.get(key) is None:
                counts["unknown_" + key] += 1
            else:
                totals[group][key] += row[key]
                totals["all"][key] += row[key]
                site_costs[row["id"]][key] += row[key]
    for target in targets:
        sid = target["id"]
        practice = target.get("practice")
        if site_costs[sid]["document_reserved"] > protocol["practice_site_doc_cap" if practice else "real_site_doc_cap"]:
            failures.append("site_document_cap:" + sid)
        if site_costs[sid]["ui_operations"] > protocol["practice_site_ui_cap" if practice else "real_site_ui_cap"]:
            failures.append("site_ui_cap:" + sid)
    if totals["all"]["document_reserved"] > protocol["global_doc_cap"]:
        failures.append("global_document_cap")
    seen_phases = set()
    by_phase = {}
    validity = {(row["id"], row["phase"], row["url"]): row for row in rows(RUN / "item-evaluation.jsonl")}
    novelty = Counter()
    novelty_phases = defaultdict(Counter)
    novelty_sites = defaultdict(set)
    valid_values = []
    for result in actual:
        key = (result["id"], result["phase"])
        if key in seen_phases:
            failures.append("duplicate_result:" + ":".join(key))
        seen_phases.add(key)
        by_phase[key] = result
        sid = result["id"]
        site = RUN / "sites" / sid
        if read(site / result["phase"] / "result.json") != result:
            failures.append("canonical_result_mismatch:" + ":".join(key))
        code_hash = hashlib.sha256((site / "crawl.js").read_bytes()).hexdigest()
        if code_hash != result.get("crawl_sha256") or read(site / "code-manifest.json")["sha256"] != code_hash:
            failures.append("code_hash:" + ":".join(key))
        if result.get("external_llm_calls") != 0 or result.get("local_inference", {}).get("calls") != 0:
            failures.append("runtime_model_call:" + ":".join(key))
        reps = read(site / "representatives.json")
        excluded = set(reps.get("initial_document_urls", []))
        initial_hashes = {meta.get("source_sha256") for name in ("representative.json", "listing-before.json", "listing-after.json", "sample-detail.json")
                          if (site / name).is_file() for meta in [read(site / name)] if meta.get("source_sha256")}
        earlier_hashes = set(initial_hashes)
        if result["phase"] == "warm":
            cold = by_phase.get((sid, "cold")) or read(site / "cold/result.json")
            excluded.update(u for item in cold.get("selected_items", []) for u in (item.get("url"), item.get("final_url")) if u)
            earlier_hashes.update(item.get("source_sha256") for item in cold.get("selected_items", []) if item.get("source_sha256"))
            if result.get("separate_process_pid") == cold.get("separate_process_pid"):
                failures.append("same_process_cold_warm:" + sid)
            counts["separate_process_replays"] += 1
        counts["selected_items"] += len(result.get("selected_items", []))
        for item in result.get("selected_items", []):
            checked = validity.get((sid, result["phase"], item.get("url")), {})
            if checked.get("usable_HTML_saved"):
                valid_values.append(item)
                same_document_url = any(urldefrag(item.get("final_url") or item["url"])[0] == urldefrag(value)[0] for value in excluded)
                fresh_source = item.get("source_sha256") not in earlier_hashes
                novelty["same_document_URL_ignoring_fragment"] += same_document_url
                novelty["source_hash_unseen"] += fresh_source
                novelty["new_document_URL_and_source_hash"] += fresh_source and not same_document_url
                novelty["strict_type_new_document_URL_and_source_hash"] += checked.get("classification") == "strict_success" and fresh_source and not same_document_url
                if fresh_source and not same_document_url:
                    novelty_phases[result["phase"]]["new_HTML"] += 1
                    novelty_sites[result["phase"] + "_new_HTML"].add(sid)
                    if checked.get("classification") == "strict_success":
                        novelty_phases[result["phase"]]["strict_type_new_HTML"] += 1
                        novelty_sites[result["phase"] + "_strict_type_new_HTML"].add(sid)
            if item.get("saved") and item.get("url") in excluded:
                failures.append("excluded_body_saved:" + ":".join(key))
            for field, digest in (("source_html", "source_sha256"), ("rendered_dom", "rendered_sha256")):
                if not item.get(field):
                    continue
                file = local(item[field])
                if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != item.get(digest):
                    failures.append("file_hash:" + ":".join(key) + ":" + field)
                counts["selected_evidence_files_hashed"] += 1
    assert len(targets) == 80 and len({t["domain"] for t in targets}) == 80
    for field in ("url", "final_url", "source_sha256", "rendered_sha256"):
        novelty["duplicate_" + field] = sum(n - 1 for n in Counter(item.get(field) for item in valid_values).values() if n > 1)
    output = {"ok": not failures, "failures": failures, "sites": len(targets), "canonical_result_rows": len(actual),
              "ledger_records": len(ledger), "counts": dict(counts), "totals": {k: dict(v) for k, v in totals.items()},
              "valid_HTML_novelty": dict(novelty), "fragments_removed_for_identity_audit_only_queries_preserved": True,
              "novelty_by_phase": {k: dict(v) for k, v in novelty_phases.items()},
              "novelty_site_ids": {k: sorted(v) for k, v in novelty_sites.items()},
              "site_costs": {k: dict(v) for k, v in site_costs.items()},
              "network_counts_are_browser_events_not_unique_wire_transmissions": True,
              "response_bytes_are_measured_bodies_not_billing_bytes": True,
              "monetary_cost_and_native_tokens": None}
    (RUN / "integrity-audit.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in output.items() if k not in {"site_costs"}}, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
