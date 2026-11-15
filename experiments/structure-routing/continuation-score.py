#!/usr/bin/env python3
"""Score a local Stage C continuation against a frozen independent manifest."""
from __future__ import annotations

import argparse
import hashlib
import html.parser
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

EXPECTED_MANIFEST_SHA256 = "56e0834d4ace84cb3d984ad77f3c72de66ae875b76dc209974a4dc0470aa2a1e"
EXPECTED_SELECTION_COST = 124
MILESTONES = (10, 30, 100)
ROOT = Path(__file__).resolve().parents[2]
RESULT_FILES = ("bulk-results.jsonl", "repair-bulk-results.jsonl")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class HtmlFacts(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: set[str] = set()
        self.title_parts: list[str] = []
        self.heading_parts: list[str] = []
        self._capture: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag.lower())
        if tag.lower() == "title":
            self._capture = "title"
        elif tag.lower() in {"h1", "h2"} and self._capture is None:
            self._capture = "heading"

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title" and self._capture == "title":
            self._capture = None
        elif tag.lower() in {"h1", "h2"} and self._capture == "heading":
            self._capture = None

    def handle_data(self, data: str) -> None:
        if self._capture == "title":
            self.title_parts.append(data)
        elif self._capture == "heading":
            self.heading_parts.append(data)


def normalize_text(value: object) -> str:
    value = str(value or "").casefold()
    return re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def canonical_url(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return None
        # Preserve the exact path and query; only URL's normal authority casing/default port is normalized.
        return parts.geturl()
    except ValueError:
        return None


def validate_html(path: Path, label: str) -> tuple[bool, str | None, str | None, str | None]:
    try:
        data = path.read_bytes()
        text = data.decode("utf-8", errors="replace")
        parser = HtmlFacts()
        parser.feed(text)
        parser.close()
    except (OSError, ValueError) as exc:
        return False, None, None, f"{label}_unreadable:{type(exc).__name__}"
    if len(data) < 100 or not {"html", "body"}.issubset(parser.tags):
        return False, None, None, f"{label}_not_document"
    text_folded = text.casefold()
    if any(term in text_folded for term in ("verify you are human", "checking your browser", "captcha", "access denied", "temporarily unavailable", "maintenance mode")):
        return False, None, None, f"{label}_challenge_or_maintenance"
    title = " ".join(parser.title_parts).strip()
    heading = " ".join(parser.heading_parts).strip()
    if re.search(r"\b(sign in|log in|login|password reset)\b", f"{title} {heading}", re.I):
        return False, title, heading, f"{label}_login_page"
    return True, title, heading, None


def proxy_matches(target: dict, observation: dict, title: str, heading: str) -> bool:
    proxy = normalize_text(target.get("title_proxy"))
    if not proxy:
        return False
    observed = normalize_text(" ".join((str(observation.get("title") or ""), str(observation.get("heading") or ""), title, heading)))
    if proxy in observed:
        return True
    # Some frozen title proxies are generic navigation labels; validate their route class explicitly.
    requested = canonical_url(target.get("url")) or ""
    path = urlsplit(requested).path.casefold()
    if proxy == "home":
        return path in {"/", "/index.html", "/index.htm"} and bool(observed)
    return False


def child_result(row: dict, candidate_root: Path) -> tuple[dict, Path]:
    child_rel = row.get("child_run")
    if not isinstance(child_rel, str):
        raise ValueError(f"child_run missing for site {row.get('id')}")
    child = (candidate_root / child_rel).resolve()
    if not child.is_relative_to(candidate_root.resolve()):
        raise ValueError("child_run escapes candidate run")
    result_path = child / "artifacts" / str(row.get("id")) / "C1" / "bulk" / "result.json"
    if not result_path.is_file():
        embedded = row.get("bulk_result")
        if isinstance(embedded, dict):
            return embedded, child / "artifacts" / str(row.get("id")) / "C1" / "bulk"
        raise ValueError(f"bulk result missing for site {row.get('id')}")
    return read_json(result_path), result_path.parent


def variant_for(row: dict, source: Path) -> str:
    if isinstance(row.get("variant"), str) and row["variant"]:
        return row["variant"]
    return {"bulk-results.jsonl": "C1", "repair-bulk-results.jsonl": "C1.1"}.get(source.name, source.stem)


def read_result_rows(path: Path, fallback_variant: str | None = None) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("category") not in {"stageC_local_bulk", "stageC_repaired_bulk"}:
            raise ValueError(f"unexpected result category in {path} at line {line_no}")
        row["_variant"] = row.get("variant") or fallback_variant or variant_for(row, path)
        row["_source_file"] = str(path)
        rows.append(row)
    return rows


def row_document_costs(row: dict, candidate_root: Path) -> dict:
    result = {}
    try:
        result, _ = child_result(row, candidate_root)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    reserved_values = [int(row[key]) for key in ("reserved_documents",) if isinstance(row.get(key), (int, float)) and row[key] >= 0]
    actual_values = [int(row[key]) for key in ("actual_documents",) if isinstance(row.get(key), (int, float)) and row[key] >= 0]
    actual_values.extend(int(result[key]) for key in ("document_attempts", "document_responses") if isinstance(result.get(key), (int, float)) and result[key] >= 0)
    reserved = max(reserved_values) if reserved_values else None
    actual = max(actual_values) if actual_values else None
    charged = actual if row.get("exit_code") == 0 and actual is not None else (max(reserved, actual) if reserved is not None and actual is not None else reserved if reserved is not None else actual)
    return {"reserved": reserved, "actual": actual, "charged": charged}


def observation_cost(observation: dict, previous: dict[str, float]) -> dict[str, float]:
    aliases = {
        "documents": ("document_attempts", "document_responses"),
        "requests": ("requests",),
        "xhr": ("xhr_fetch", "xhr_requests"),
        "ui": ("ui_actions", "ui"),
        "ms": ("cumulative_ms", "elapsed_ms"),
    }
    result: dict[str, float] = {}
    for key, fields in aliases.items():
        raw = next((observation.get(name) for name in fields if observation.get(name) is not None), None)
        current = float(raw) if isinstance(raw, (int, float)) and raw >= 0 else previous[key]
        if key != "ms" and not any(observation.get(name) is not None for name in fields):
            current = previous[key]
        result[key] = current
    return result


def milestone_rows(observations: list[dict], reached_by_index: list[int]) -> tuple[list[dict], list[dict]]:
    attempt_rows: list[dict] = []
    success_rows: list[dict] = []
    previous_cost = {"documents": 0.0, "requests": 0.0, "xhr": 0.0, "ui": 0.0, "ms": 0.0}
    costs: list[dict[str, float]] = []
    for observation in observations:
        current = observation_cost(observation, previous_cost)
        costs.append(current)
        previous_cost = current
    for milestone in MILESTONES:
        attempt_index = milestone - 1
        if attempt_index >= len(observations):
            attempt_rows.append({"attempts": milestone, "unmet": True, "observed_attempts": len(observations), "cost": None, "incremental_cost": None, "reached": reached_by_index[-1] if reached_by_index else 0})
        else:
            cost = costs[attempt_index]
            prior = costs[MILESTONES[MILESTONES.index(milestone) - 1] - 1] if MILESTONES.index(milestone) and len(costs) >= MILESTONES[MILESTONES.index(milestone) - 1] else {k: 0.0 for k in cost}
            attempt_rows.append({"attempts": milestone, "unmet": False, "reached": reached_by_index[attempt_index], "cost": cost, "incremental_cost": {k: cost[k] - prior[k] for k in cost}})
    for target_count in MILESTONES:
        index = next((i for i, count in enumerate(reached_by_index) if count >= target_count), None)
        if index is None:
            success_rows.append({"targets": target_count, "unmet": True, "cost": None, "incremental_cost": None})
            continue
        cost = costs[index]
        prior = costs[success_rows[-1]["observation_index"]] if success_rows and not success_rows[-1].get("unmet") else {k: 0.0 for k in cost}
        success_rows.append({"targets": target_count, "unmet": False, "observation_index": index, "cost": cost, "incremental_cost": {k: cost[k] - prior[k] for k in cost}})
    for row in success_rows:
        row.pop("observation_index", None)
    return attempt_rows, success_rows


def valid_candidate_milestones(result: dict) -> list[dict]:
    raw = result.get("milestones", [])
    if isinstance(raw, dict):
        raw = [{"valid_candidate_pages": int(key), **(value if isinstance(value, dict) else {})} for key, value in raw.items()]
    by_count = {int(row.get("valid_candidate_pages", row.get("pages", 0))): row for row in raw if isinstance(row, dict)} if isinstance(raw, list) else {}
    output = []
    previous = {"documents": 0, "requests": 0, "xhr": 0, "ui": 0, "ms": 0, "bytes": 0}
    for count in MILESTONES:
        row = by_count.get(count)
        if row is None:
            output.append({"valid_candidate_pages": count, "unmet": True, "cost": None, "incremental_cost": None})
            continue
        cost = {
            "documents": row.get("document_attempts"),
            "requests": row.get("requests"),
            "xhr": row.get("xhr_fetch"),
            "ui": row.get("operations", result.get("operations")),
            "ms": row.get("cumulative_ms", row.get("elapsed_ms")),
            "bytes": row.get("bytes"),
        }
        normalized = {key: (float(value) if isinstance(value, (int, float)) and value >= 0 else None) for key, value in cost.items()}
        delta = {key: (normalized[key] - previous[key] if normalized[key] is not None else None) for key in normalized}
        output.append({"valid_candidate_pages": count, "unmet": False, "cost": normalized, "incremental_cost": delta})
        for key in previous:
            if normalized.get(key) is not None:
                previous[key] = normalized[key]
    return output


def score_site(site: dict, row: dict, candidate_root: Path) -> dict:
    site_id = str(site["id"])
    result, bulk_dir = child_result(row, candidate_root)
    observations = result.get("observations")
    if not isinstance(observations, list):
        observations = []
    targets = site.get("targets", [])
    frozen = {canonical_url(target.get("url")): target for target in targets if canonical_url(target.get("url"))}
    reached: set[str] = set()
    valid_candidates = 0
    errors: list[dict] = []
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict):
            continue
        url = canonical_url(observation.get("url"))
        requested = canonical_url(observation.get("requested_url"))
        if observation.get("valid_html") is True and observation.get("kind") == "rule_selected_target":
            valid_candidates += 1
        if not url or url not in frozen:
            continue
        reason: str | None = None
        if requested != url:
            reason = "requested_url_mismatch"
        elif observation.get("status") != 200 or observation.get("navigation_error"):
            reason = "navigation_not_successful"
        source_rel, dom_rel = observation.get("source_html"), observation.get("rendered_dom")
        if not reason and (not isinstance(source_rel, str) or not isinstance(dom_rel, str)):
            reason = "saved_evidence_paths_missing"
        if not reason:
            source = (bulk_dir / source_rel).resolve()
            dom = (bulk_dir / dom_rel).resolve()
            if not source.is_relative_to(bulk_dir.resolve()) or not dom.is_relative_to(bulk_dir.resolve()):
                reason = "evidence_path_escapes_bulk_dir"
            else:
                raw_ok, raw_title, raw_heading, raw_reason = validate_html(source, "raw_html")
                dom_ok, dom_title, dom_heading, dom_reason = validate_html(dom, "dom")
                if not raw_ok:
                    reason = raw_reason
                elif not dom_ok:
                    reason = dom_reason
                elif not proxy_matches(frozen[url], observation, dom_title or raw_title or "", dom_heading or raw_heading or ""):
                    reason = "title_proxy_mismatch"
        if reason:
            errors.append({"observation_index": index, "url": url, "reason": reason})
        else:
            reached.add(url)
    ordered_reached = [url for url in frozen if url in reached]
    reached_by_index: list[int] = []
    cumulative: set[str] = set()
    for observation in observations:
        url = canonical_url(observation.get("url")) if isinstance(observation, dict) else None
        if url in reached:
            cumulative.add(url)
        reached_by_index.append(len(cumulative))
    attempt_rows, success_rows = milestone_rows(observations, reached_by_index)
    misses = [url for url in frozen if url not in reached]
    reserved_values = [int(row[key]) for key in ("reserved_documents",) if isinstance(row.get(key), (int, float)) and row[key] >= 0]
    actual_values = [int(row[key]) for key in ("actual_documents",) if isinstance(row.get(key), (int, float)) and row[key] >= 0]
    actual_values.extend(int(result[key]) for key in ("document_attempts", "document_responses") if isinstance(result.get(key), (int, float)) and result[key] >= 0)
    reserved_docs = max(reserved_values) if reserved_values else None
    actual_docs = max(actual_values) if actual_values else None
    completed = row.get("exit_code") == 0
    charged_docs = actual_docs if completed and actual_docs is not None else reserved_docs
    return {
        "id": site_id,
        "variant": row.get("_variant", "unknown"),
        "scoring_status": "scored",
        "status": row.get("status"),
        "exit_code": row.get("exit_code"),
        "frozen_target_count": len(frozen),
        "valid_candidate_count": valid_candidates,
        "gold_targets_reached": len(reached),
        "reached_urls": ordered_reached,
        "unreached_urls": misses,
        "evidence_failures": errors,
        "attempt_count": len(observations),
        "bulk_reserved_document_cost": reserved_docs,
        "bulk_actual_document_cost": actual_docs,
        "bulk_charged_document_cost": charged_docs,
        "bulk_document_cost": charged_docs,
        "bulk_result_document_attempts": max((int(result[key]) for key in ("document_attempts", "document_responses") if isinstance(result.get(key), (int, float)) and result[key] >= 0), default=None),
        "cost_milestones_by_attempt": attempt_rows,
        "cost_milestones_by_valid_candidates": valid_candidate_milestones(result),
        "cost_milestones_by_success": success_rows,
        "bulk_ledger_provenance": row.get("child_run"),
        "rules_sha256": row.get("rules_sha256"),
        "fresh_acquisition": "result observations and saved raw/dom files only; no pre-existing HTML cache inputs",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--results-file", type=Path, default=Path("bulk-results.jsonl"))
    parser.add_argument("--ids", nargs="+", help="manifest site IDs to score, for example --ids 01")
    args = parser.parse_args()
    baseline = args.baseline.resolve()
    candidate_root = args.candidate_run.resolve()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite: {output}")
    actual_hash = sha256(baseline)
    if actual_hash != EXPECTED_MANIFEST_SHA256:
        raise ValueError(f"frozen Stage C manifest hash mismatch: {actual_hash}")
    manifest = read_json(baseline)
    if manifest.get("schema") != "stage-c-independent-targets-v1" or manifest.get("revision") != "v4" or not manifest.get("frozen_at"):
        raise ValueError("baseline is not the frozen v4 Stage C manifest")
    results_file = args.results_file if args.results_file.is_absolute() else candidate_root / args.results_file
    selected_rows_list = read_result_rows(results_file)
    selected_rows: dict[tuple[str, str], dict] = {}
    for row in selected_rows_list:
        site_id = str(row.get("id"))
        key = (str(row["_variant"]), site_id)
        if key in selected_rows:
            raise ValueError(f"duplicate selected result row for variant/site {key[0]}/{key[1]}")
        selected_rows[key] = row
    manifest_ids = [str(site["id"]) for site in manifest["independent_targets"]]
    selected_ids = [str(value) for value in args.ids] if args.ids else manifest_ids
    invalid_ids = sorted(set(selected_ids) - set(manifest_ids))
    if invalid_ids:
        raise ValueError(f"site IDs are not present in frozen manifest: {', '.join(invalid_ids)}")
    selected_manifest = [site for site in manifest["independent_targets"] if str(site["id"]) in selected_ids]

    all_rows: list[dict] = []
    paths = [candidate_root / name for name in RESULT_FILES]
    if results_file.resolve() not in {path.resolve() for path in paths}:
        paths.append(results_file)
    for path in paths:
        arm_rows = read_result_rows(path)
        all_rows.extend(arm_rows)
    unique_rows: dict[tuple[str, str], dict] = {}
    for row in all_rows:
        key = (str(row["_variant"]), str(row.get("id")))
        if key in unique_rows:
            raise ValueError(f"duplicate global result row for variant/site {key[0]}/{key[1]}")
        unique_rows[key] = row

    global_costs = []
    for key, row in unique_rows.items():
        costs = row_document_costs(row, candidate_root)
        global_costs.append({"variant": key[0], "id": key[1], **costs, "status": row.get("status"), "exit_code": row.get("exit_code"), "source_file": row.get("_source_file")})
    charge_known = all(isinstance(item["charged"], (int, float)) for item in global_costs)
    total_candidate_charged = sum(item["charged"] for item in global_costs) if charge_known else None
    total_candidate_reserved = sum(item["reserved"] for item in global_costs if isinstance(item["reserved"], (int, float)))
    total_candidate_actual = sum(item["actual"] for item in global_costs) if global_costs and all(isinstance(item["actual"], (int, float)) for item in global_costs) else None
    def exact_sum(items: list[dict], field: str) -> int | float | None:
        if not items or any(not isinstance(item.get(field), (int, float)) for item in items):
            return None
        return sum(item[field] for item in items)

    c1_rows = [item for item in global_costs if item["variant"] == "C1"]
    other_rows = [item for item in global_costs if item["variant"] != "C1"]
    c1_charged = exact_sum(c1_rows, "charged")
    other_arm_charged = exact_sum(other_rows, "charged")
    selected_variants = {str(row["_variant"]) for row in selected_rows_list}
    selected_keys = {(str(row["_variant"]), str(row.get("id"))) for row in selected_rows_list}
    selected_arm_charged = exact_sum([item for item in global_costs if (item["variant"], item["id"]) in selected_keys], "charged")
    sites = []
    for manifest_site in selected_manifest:
        site_id = str(manifest_site["id"])
        rows_for_site = [row for (variant, result_id), row in selected_rows.items() if result_id == site_id]
        if not rows_for_site:
            sites.append({"id": site_id, "variant": None, "status": "not_run_or_missing_result_row", "scoring_status": "unknown", "frozen_target_count": len(manifest_site["targets"]), "valid_candidate_count": None, "gold_targets_reached": None, "reached_urls": [], "unreached_urls": [], "unobserved_urls": [canonical_url(t["url"]) for t in manifest_site["targets"]], "evidence_failures": [], "attempt_count": None, "bulk_document_cost": None, "cost_milestones_by_attempt": [], "cost_milestones_by_valid_candidates": [], "cost_milestones_by_success": []})
        else:
            for row in rows_for_site:
                try:
                    sites.append(score_site(manifest_site, row, candidate_root))
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    costs = row_document_costs(row, candidate_root)
                    sites.append({"id": site_id, "variant": row.get("_variant"), "status": row.get("status"), "scoring_status": "unknown", "evaluation_error": str(exc), "frozen_target_count": len(manifest_site["targets"]), "valid_candidate_count": None, "gold_targets_reached": None, "reached_urls": [], "unreached_urls": [], "unobserved_urls": [canonical_url(t["url"]) for t in manifest_site["targets"]], "evidence_failures": [], "attempt_count": None, "bulk_reserved_document_cost": costs["reserved"], "bulk_actual_document_cost": costs["actual"], "bulk_charged_document_cost": costs["charged"], "bulk_document_cost": costs["charged"], "cost_milestones_by_attempt": [], "cost_milestones_by_valid_candidates": [], "cost_milestones_by_success": []})
    budget = read_json(candidate_root / "budget.json") if (candidate_root / "budget.json").is_file() else None
    stage_budget = (budget or {}).get("stageC", {})
    budget_spent = stage_budget.get("spent")
    measured_spend = EXPECTED_SELECTION_COST + total_candidate_charged if total_candidate_charged is not None else None
    budget_matches = budget_spent == measured_spend if budget_spent is not None and measured_spend is not None else None
    if measured_spend is not None and budget_matches is not True:
        raise ValueError(f"budget stageC.spent mismatch: file={budget_spent!r}, recomputed={measured_spend}")
    reserved_open = stage_budget.get("reserved")
    cap = stage_budget.get("cap", 400)
    remaining = stage_budget.get("remaining")
    for item in sites:
        item.setdefault("unobserved_urls", [])
    selected_variant_label = ", ".join(sorted(selected_variants)) if selected_variants else "no recorded arm"
    output_obj = {
        "schema": "local-continuation-stage-c-score-v3",
        "created_by": Path(__file__).name,
        "baseline_manifest": str(baseline),
        "baseline_manifest_sha256": actual_hash,
        "baseline_selection_and_diagnostics_documents": EXPECTED_SELECTION_COST,
        "candidate_run": str(candidate_root),
        "candidate_bulk_results": str(results_file),
        "selected_variant": selected_variant_label,
        "selected_site_ids": selected_ids,
        "selected_arm_candidate_spend": selected_arm_charged,
        "candidate_spend": c1_charged,
        "c1_candidate_spend": c1_charged,
        "additional_spend": other_arm_charged,
        "other_arms_additional_spend": other_arm_charged,
        "c1_result_row_count": len(c1_rows),
        "other_arm_result_row_count": len(other_rows),
        "unknown_charge_row_count": sum(not isinstance(item["charged"], (int, float)) for item in global_costs),
        "known_candidate_reserved_documents": total_candidate_reserved,
        "known_candidate_actual_documents": total_candidate_actual,
        "known_candidate_charged_documents": total_candidate_charged,
        "accounted_result_rows": global_costs,
        "shared_stage_c_documents_including_baseline": measured_spend,
        "shared_stage_c_spent": measured_spend,
        "shared_stage_c_cap": cap,
        "shared_stage_c_reserved_documents": reserved_open,
        "shared_stage_c_remaining_documents": remaining,
        "within_shared_stage_c_cap": measured_spend <= cap if measured_spend is not None and isinstance(cap, (int, float)) else None,
        "budget_stage_c_spent_before": stage_budget.get("spent_before"),
        "budget_stage_c_spent": budget_spent,
        "budget_stage_c_reserved": reserved_open,
        "budget_stage_c_remaining": remaining,
        "budget_file_spent_matches_recomputed": budget_matches,
        "sites": sites,
        "notes": ["Frozen target URLs and title proxies were used only for post-run scoring.", "No target labels or expected values were written to candidate inputs.", "C1 valid candidate count and independent gold target reach are separate measures.", "Missing or unobserved milestone values remain null; unrun sites are marked unknown and do not count as misses.", "Completed exit_code=0 rows release unused reservations and charge measured actual documents; other rows charge the full reservation when present.", "This score covers only the selected variant/site rows; shared spend includes all known rows across both result files.", "Fresh acquisition is judged from this run's result rows and evidence paths; provenance hashes are retained per child."]
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(output_obj, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"output": str(output), "manifest_sha256": actual_hash, "shared_documents": measured_spend, "selected_variant": selected_variant_label, "sites": [{"id": s["id"], "gold_reached": s["gold_targets_reached"]} for s in sites]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"continuation-score: {exc}", file=sys.stderr)
        raise SystemExit(2)
