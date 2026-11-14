#!/usr/bin/env python3
"""Render Stage C independent reach and acquisition costs from its score JSON."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("score", nargs="?", type=Path)
    parser.add_argument("--revision", default="v1", help="score revision to report, such as v2")
    args = parser.parse_args()
    suffix = "" if args.revision == "v1" else f"-{args.revision}"
    score_path = args.score or ROOT / f"lab-runs/structure-routing-20261115/stage-c-score{suffix}.json"
    data = json.loads(score_path.read_text(encoding="utf-8"))
    print(f"Stage C independent target evaluation ({data.get('revision', args.revision)})")
    print(f"Frozen manifest SHA256: {data['manifest_sha256']}")
    print(f"Network layer: {data.get('network_layer', 'unknown')} (proxy context enabled: {data.get('proxy_context_enabled', False)})")
    for attempt in data.get("prior_selection_attempts", []):
        request_text = 'unknown' if attempt.get('requests') is None else str(attempt['requests'])
        print(f"Prior selection {attempt['revision']}: {attempt['documents']} charged documents, {request_text} requests")
        for outcome in attempt.get('site_outcomes', []):
            print(f"  Site {outcome['id']}: {outcome['stopped']}")
        if attempt.get('record_type') == 'interrupted_reservation':
            print(f"  Exact documents/requests: {attempt.get('exact_documents')} / {attempt.get('exact_requests')}; reason: {attempt.get('reason')}")
            print(f"  Partial evidence retained at: {attempt.get('partial_evidence_preserved')}")
    print(f"Documents: prior selection {data.get('prior_selection_documents', 0)}, current selection {data['selection_documents']} charged "
          f"({data.get('observed_list_documents', 0)} list documents observed), "
          f"bulk {data['bulk_documents']}, total {data['total_documents']} / 400")
    if data.get('total_selection_requests') is None:
        print(f"Selection requests: unknown exact total; known lower bound {data.get('total_selection_requests_known_lower_bound', 0)}; "
              f"{data.get('total_selection_request_unknown_attempts', 0)} attempt(s) with unknown count")
        print(f"Current selection request lower bound: {data.get('current_selection_requests_known_lower_bound', 0)}")
    else:
        print(f"Selection requests: {data['total_selection_requests']} total")
    print(f"Bulk document budget remaining after selection: {data.get('bulk_document_budget', 'unknown')} total; up to {data.get('bulk_document_budget_per_site', 'unknown')} per site")
    for site in data["sites"]:
        print(f"\nSite {site['id']}: {site['independent_targets_reached']} / {site['frozen_target_count']} independent targets reached")
        print(f"Bulk document attempts: {site['bulk_document_attempts']}; useful saved HTML targets: {site['useful_html_documents']}")
        print("Independent-target milestones:")
        for item in site["success_milestones"]:
            if item.get("unmet"):
                print(f"  {item['independent_targets']} targets: unmet")
            else:
                print(f"  {item['independent_targets']} targets: docs {item['cumulative_document_cost']} (+{item['incremental_document_cost']}), "
                      f"requests {item['cumulative_request_cost']} (+{item['incremental_request_cost']}), "
                      f"time {item['cumulative_time_ms']}ms (+{item['incremental_time_ms']}ms)")
        print("Attempt-count checkpoints:")
        for item in site["attempt_milestones"]:
            if item.get("unmet"):
                print(f"  {item['attempts']} attempts: unmet after {item['observed_attempts']} observations")
            else:
                print(f"  {item['attempts']} attempts: {item['cumulative_independent_reached']} reached, "
                      f"docs {item['cumulative_document_cost']} (+{item['incremental_document_cost']}), "
                      f"requests {item['cumulative_request_cost']} (+{item['incremental_request_cost']}), "
                      f"time {item['cumulative_time_ms']}ms (+{item['incremental_time_ms']}ms)")
        if site["unreached_urls"]:
            print(f"Unreached frozen URLs: {len(site['unreached_urls'])}")
    print("\nSelection labels are unverified title/path proxies.")


if __name__ == "__main__":
    main()
