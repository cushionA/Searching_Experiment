import argparse
import hashlib
import json
import re
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


class PageProbe(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_depth = 0
        self.h1_depth = 0
        self.main_depth = 0
        self.article_depth = 0
        self.ignored_depth = 0
        self.form_depth = 0
        self.title = []
        self.h1 = []
        self.text = []
        self.main_text = []
        self.login_password_form = False
        self.challenge_form = False
        self.has_main = False
        self.has_article = False
        self.ignored_tags = {"head", "script", "style", "noscript", "svg", "template", "nav", "header", "footer", "aside"}
        self.challenge_words = re.compile(r"captcha|access denied|verify (?:that )?you are human|checking your browser|security check", re.I)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in self.ignored_tags:
            self.ignored_depth += 1
        if tag == "title":
            self.title_depth += 1
        if tag == "h1":
            self.h1_depth += 1
        if tag == "main":
            self.main_depth += 1
            self.has_main = True
        if tag == "article":
            self.article_depth += 1
            self.has_article = True
        if tag == "form":
            self.form_depth += 1
        values = " ".join([attrs.get("id", ""), attrs.get("class", ""), attrs.get("name", ""), attrs.get("action", "")]).lower()
        if self.form_depth and any(x in values for x in ("captcha", "challenge", "cf-chl", "verify-human")):
            self.challenge_form = True
        if tag == "input" and self.form_depth and attrs.get("type", "").lower() == "password":
            self.login_password_form = True

    def handle_endtag(self, tag):
        if tag == "title" and self.title_depth:
            self.title_depth -= 1
        if tag == "h1" and self.h1_depth:
            self.h1_depth -= 1
        if tag == "main" and self.main_depth:
            self.main_depth -= 1
        if tag == "article" and self.article_depth:
            self.article_depth -= 1
        if tag == "form" and self.form_depth:
            self.form_depth -= 1
        if tag in self.ignored_tags and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data):
        value = data.strip()
        if not value:
            return
        if self.title_depth:
            self.title.append(value)
        if self.h1_depth:
            self.h1.append(value)
        if self.ignored_depth:
            return
        self.text.append(value)
        if self.main_depth or self.article_depth:
            self.main_text.append(value)


def norm(value):
    return re.sub(r"[^\w]+", "", str(value or "").casefold(), flags=re.UNICODE)


def url_key(value):
    if not value:
        return None
    return str(value).strip()


def iter_urls(value):
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from iter_urls(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_urls(child)


def load_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def source_path(ref, run_root):
    if not ref:
        return None
    path = Path(ref)
    return path if path.is_absolute() else run_root / path


def valid_html(observation, run_root, target):
    if str(observation.get("status", "")) != "200":
        return False, "http_status_not_200"
    path = source_path(observation.get("source_html"), run_root)
    if not path or not path.is_file():
        return False, "missing_html"
    raw = path.read_bytes()
    if not raw.strip():
        return False, "empty_html"
    expected_hash = observation.get("source_sha256")
    if expected_hash and hashlib.sha256(raw).hexdigest() != expected_hash:
        return False, "html_hash_mismatch"
    source = raw.decode("utf-8", errors="replace")
    if not re.search(r"<!doctype\s+html|<html\b", source, re.I):
        return False, "invalid_source_html"
    source_probe = PageProbe()
    try:
        source_probe.feed(source)
    except Exception:
        return False, "html_parse_error"
    rendered_ref = source_path(observation.get("rendered_dom"), run_root)
    rendered = ""
    if rendered_ref and rendered_ref.is_file():
        rendered = rendered_ref.read_bytes().decode("utf-8", errors="replace")
    rendered_probe = PageProbe()
    try:
        rendered_probe.feed(rendered or source)
    except Exception:
        return False, "rendered_dom_parse_error"
    probe = rendered_probe
    plain = norm(" ".join(probe.main_text if probe.has_main or probe.has_article else probe.text))
    combined_headings = " ".join(source_probe.title + source_probe.h1 + probe.title + probe.h1)
    title = norm(" ".join(source_probe.title + probe.title))
    h1 = norm(" ".join(source_probe.h1 + probe.h1))
    title_expected = norm(target.get("title"))
    observed_title = norm(observation.get("title"))
    heading_text = " ".join(source_probe.title + source_probe.h1 + probe.title + probe.h1)
    if re.search(r"\b(?:404|page not found|not found|under maintenance|maintenance mode|temporarily unavailable|service unavailable)\b", heading_text, re.I):
        return False, "maintenance_or_not_found_heading"
    challenge_heading = bool(probe.challenge_words.search(heading_text))
    challenge_short_form = probe.challenge_form and len(plain) < 2500
    if challenge_heading or challenge_short_form:
        return False, "challenge"
    login_heading = bool(re.search(r"\b(log ?in|sign ?in|account access)\b", heading_text, re.I))
    if (probe.login_password_form or source_probe.login_password_form) and login_heading:
        return False, "login"
    if not plain or len(plain) < 80:
        return False, "empty_or_shell"
    normalized_content = title + h1 + plain
    if title_expected and title_expected not in normalized_content:
        return False, "independent_title_mismatch"
    if observed_title and observed_title not in normalized_content:
        return False, "reported_title_mismatch"
    if not (h1 or title):
        return False, "missing_heading"
    source_shell = not norm(" ".join(source_probe.main_text if source_probe.has_main or source_probe.has_article else source_probe.text))
    return True, "js_rendered_html" if source_shell and rendered else "valid_html"


def collect_map_urls(row, run_root, site_id, method):
    found = set()
    inventory = run_root / "artifacts" / site_id / method / "inventory.json"
    if inventory.is_file():
        try:
            found.update(url_key(x) for x in iter_urls(json.loads(inventory.read_text(encoding="utf-8"))) if url_key(x))
        except (json.JSONDecodeError, OSError):
            pass
    inventory_lines = run_root / "artifacts" / site_id / method / "url-inventory.jsonl"
    if inventory_lines.is_file():
        try:
            for line in inventory_lines.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    found.update(url_key(x) for x in iter_urls(json.loads(line)) if url_key(x))
        except (json.JSONDecodeError, OSError):
            pass
    if method == "B2":
        return found
    if found:
        return found
    for observation in row.get("observations", []) or []:
        for key in ("requested_url", "final_url", "canonical_href"):
            value = url_key(observation.get(key))
            if value:
                found.add(value)
        found.update(url_key(x) for x in iter_urls(observation.get("links", [])) if url_key(x))
    return found


def add_costs(totals, present, cost):
    for key in ("document_requests", "document_responses", "http_requests", "xhr_fetch", "response_bytes", "browser_ms"):
        value = cost.get(key)
        if isinstance(value, (int, float)):
            totals[key] += value
            present[key] = True
    for section in ("sitemap_discovery", "robots"):
        values = cost.get(section)
        if isinstance(values, dict):
            for key in ("requests", "responses", "bytes", "elapsed_ms"):
                value = values.get(key)
                if isinstance(value, (int, float)):
                    name = f"{section}_{key}"
                    totals[name] += value
                    present[name] = True


def number_or_none(value):
    return value if isinstance(value, (int, float)) else None


def generic_url_shape(value):
    parts = urlsplit(value)
    segments = parts.path.split("/")
    normalized = []
    for index, segment in enumerate(segments):
        if not segment:
            normalized.append("")
        elif re.fullmatch(r"\d+", segment):
            normalized.append(":number")
        elif re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}", segment):
            normalized.append(":uuid")
        elif re.fullmatch(r"[0-9a-fA-F]{8,}", segment):
            normalized.append(":hex")
        elif index == len(segments) - 1 and "." in segment:
            normalized.append(":slug" + segment[segment.rfind("."):])
        elif index == len(segments) - 1:
            normalized.append(":slug")
        else:
            normalized.append(segment)
    query_keys = sorted(set(key for key, _ in parse_qsl(parts.query, keep_blank_values=True)))
    query_shape = ",".join(query_keys)
    return f"{parts.hostname or ''}{'/'.join(normalized)}?{query_shape}#{'hash' if parts.fragment else ''}"


def load_shape_results(run_root, site_id, method):
    path = run_root / "artifacts" / site_id / method / "cold" / "result.json"
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value.get("shape_results", []) if isinstance(value.get("shape_results"), list) else []
    except (json.JSONDecodeError, OSError):
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--evaluation-private", type=Path, default=Path("lab-runs/structure-routing-b2-20261115/evaluation-private"))
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--sites", nargs="*")
    parser.add_argument("--methods", nargs="*")
    args = parser.parse_args()
    run_root = (args.run_root or args.results.parent).resolve()
    out_dir = args.out_dir or args.results.parent
    private = args.evaluation_private
    frozen_path = private / "frozen-gold.json"
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    frozen_hash = hashlib.sha256(frozen_path.read_bytes()).hexdigest()
    rows = load_rows(args.results)
    sites = args.sites or list(frozen["sites"])
    available_methods = list(dict.fromkeys(row.get("method") for row in rows if row.get("method")))
    methods = args.methods or [name for name in ("B1", "C1", "B2") if name in available_methods] + [name for name in available_methods if name not in ("B1", "C1", "B2")]
    buckets = defaultdict(list)
    for row in rows:
        buckets[(str(row.get("id")), row.get("method"), row.get("phase"))].append(row)
    per_site = []
    cohort = {}
    for method in methods:
        method_rows = []
        for site_id in sites:
            site_gold = frozen["sites"].get(site_id, {})
            gold = site_gold.get("targets", [])
            cohort_name = site_gold.get("cohort", "unestablished")
            verified = cohort_name == "body_verified"
            proxy = cohort_name == "proxy"
            eligible_gold = [t for t in gold if proxy or (verified and t.get("candidate_valid") is True)]
            phases = {}
            for phase in ("cold", "warm"):
                site_rows = buckets.get((site_id, method, phase), [])
                if not site_rows:
                    phases[phase] = {"status": "unrun", "urlmap_fixed_hits": 0, "fixed_target_denominator": len(eligible_gold), "urlmap_strict_hits": 0, "strict_denominator": sum(t.get("candidate_valid") is True for t in gold) if verified else 0, "urlmap_proxy_hits": 0, "proxy_denominator": len(gold) if proxy else 0, "urlmap_unestablished_observed": 0, "unestablished_target_records": len(gold) if cohort_name == "unestablished" else 0, "valid_html": 0, "strict_targetreach_valid_html": 0, "proxy_targetreach_valid_html": 0, "html_attempts": 0, "actual_html": 0, "html_document_requests": None, "html_document_responses": None, "html_http_requests": None, "html_xhr_fetch": None, "html_response_bytes": None, "browser_ms": None, "sitemap_discovery": {k: None for k in ("requests", "responses", "bytes", "elapsed_ms")}, "robots": {k: None for k in ("requests", "responses", "bytes")}, "all_http_requests": None, "external_llm_calls": None, "local_inference": {k: None for k in ("calls", "input_tokens", "runtime_ms")}, "cpu_time": None, "monetary_cost": None}
                    continue
                urls = set()
                observations = []
                costs = defaultdict(float)
                has_cost = defaultdict(bool)
                local_cost = defaultdict(float)
                local_present = defaultdict(bool)
                for row in site_rows:
                    urls |= collect_map_urls(row, run_root, site_id, method)
                    observations.extend(row.get("observations", []) or [])
                    add_costs(costs, has_cost, row.get("cost") or {})
                    inference = row.get("local_inference")
                    if isinstance(inference, dict):
                        for key in ("calls", "input_tokens", "runtime_ms"):
                            value = inference.get(key)
                            if isinstance(value, (int, float)):
                                local_cost[key] += value
                                local_present[key] = True
                gold_urls = {url_key(item.get("url")) for item in eligible_gold if item.get("url")}
                strict_urls = {url_key(item.get("url")) for item in gold if item.get("url") and item.get("candidate_valid") is True} if verified else set()
                proxy_urls = {url_key(item.get("url")) for item in gold if item.get("url")} if proxy else set()
                unestablished_urls = {url_key(item.get("url")) for item in gold if item.get("url")} if cohort_name == "unestablished" else set()
                hits = len(gold_urls & urls)
                valid_count = 0
                html_count = 0
                reasons = defaultdict(int)
                for obs in observations:
                    p = source_path(obs.get("source_html"), run_root)
                    if p and p.is_file() and p.stat().st_size > 0:
                        html_count += 1
                    target = next((g for g in gold if url_key(g.get("url")) in (url_key(obs.get("requested_url")), url_key(obs.get("final_url")))), {})
                    ok, reason = valid_html(obs, run_root, target)
                    if ok:
                        valid_count += 1
                    elif obs.get("source_html"):
                        reasons[reason] += 1
                reached_strict = set()
                reached_proxy = set()
                for obs in observations:
                    matched = url_key(obs.get("requested_url")) or url_key(obs.get("final_url"))
                    if matched not in strict_urls | proxy_urls:
                        continue
                    is_valid, _ = valid_html(obs, run_root, next((g for g in gold if url_key(g.get("url")) == matched), {}))
                    if is_valid:
                        if matched in strict_urls:
                            reached_strict.add(matched)
                        if matched in proxy_urls:
                            reached_proxy.add(matched)
                raw_status = site_rows[-1].get("status")
                if reached_strict or reached_proxy:
                    row_status = "targetreach"
                elif valid_count:
                    row_status = "valid"
                elif site_rows[-1].get("access_stop"):
                    row_status = "blocked"
                elif "truncat" in str(site_rows[-1].get("stop_reason") or raw_status or "").lower():
                    row_status = "truncated"
                else:
                    row_status = "unavailable"
                page_classes = defaultdict(int)
                for ob in observations:
                    page_classes[str(ob.get("page_classification") or "unclassified")] += 1
                required_counts = ["http_requests", "robots_requests"] + (["sitemap_discovery_requests"] if method == "B2" else [])
                all_http = sum(costs[k] for k in required_counts) if all(has_cost[k] for k in required_counts) else None
                phases[phase] = {"status": row_status, "raw_status": raw_status, "urlmap_fixed_hits": hits, "fixed_target_denominator": len(gold_urls), "urlmap_fixed_rate": hits / len(gold_urls) if gold_urls else None, "urlmap_strict_hits": len(strict_urls & urls), "strict_denominator": len(strict_urls), "urlmap_proxy_hits": len(proxy_urls & urls), "proxy_denominator": len(proxy_urls), "urlmap_unestablished_observed": len(unestablished_urls & urls), "unestablished_target_records": len(unestablished_urls), "valid_html": valid_count, "strict_targetreach_valid_html": len(reached_strict), "proxy_targetreach_valid_html": len(reached_proxy), "html_attempts": len(observations), "actual_html": html_count, "invalid_html_reasons": dict(reasons), "candidate_page_classifications": dict(page_classes), "html_document_requests": costs.get("document_requests") if has_cost["document_requests"] else None, "html_document_responses": costs.get("document_responses") if has_cost["document_responses"] else None, "html_http_requests": costs.get("http_requests") if has_cost["http_requests"] else None, "html_xhr_fetch": costs.get("xhr_fetch") if has_cost["xhr_fetch"] else None, "html_response_bytes": costs.get("response_bytes") if has_cost["response_bytes"] else None, "browser_ms": costs.get("browser_ms") if has_cost["browser_ms"] else None, "sitemap_discovery": {k: costs.get(f"sitemap_discovery_{k}") if has_cost[f"sitemap_discovery_{k}"] else None for k in ("requests", "responses", "bytes", "elapsed_ms")}, "robots": {k: costs.get(f"robots_{k}") if has_cost[f"robots_{k}"] else None for k in ("requests", "responses", "bytes")}, "all_http_requests": all_http, "external_llm_calls": sum(r["external_llm_calls"] for r in site_rows if isinstance(r.get("external_llm_calls"), (int, float))) if any(isinstance(r.get("external_llm_calls"), (int, float)) for r in site_rows) else None, "local_inference": {k: local_cost.get(k) if local_present[k] else None for k in ("calls", "input_tokens", "runtime_ms")}, "cpu_time": None, "monetary_cost": None}
            cold_rows = buckets.get((site_id, method, "cold"), [])
            warm_rows = buckets.get((site_id, method, "warm"), [])
            def obs_url(ob):
                return url_key(ob.get("final_url")) or url_key(ob.get("requested_url"))
            cold_obs = [ob for r in cold_rows for ob in (r.get("observations") or [])]
            warm_obs = [ob for r in warm_rows for ob in (r.get("observations") or [])]
            cold_urls = {obs_url(ob) for ob in cold_obs if obs_url(ob)}
            warm_urls = {obs_url(ob) for ob in warm_obs if obs_url(ob)}
            same_warm_obs = [ob for ob in warm_obs if obs_url(ob) in cold_urls]
            fresh_same = sum(1 for ob in same_warm_obs if source_path(ob.get("source_html"), run_root) is not None and source_path(ob.get("source_html"), run_root).is_file() and source_path(ob.get("source_html"), run_root).stat().st_size > 0)
            cold_classes = {ob.get("page_classification") for ob in cold_obs if ob.get("page_classification")}
            new_same_shape = sum(1 for ob in warm_obs if obs_url(ob) not in cold_urls and ob.get("page_classification") in cold_classes)
            shape_results = load_shape_results(run_root, site_id, method) if method == "B2" else []
            valid_shapes = {str(item.get("shape")) for item in shape_results if item.get("valid") is True and item.get("shape")}
            warm_shape_obs = [ob for ob in warm_obs if obs_url(ob) not in cold_urls and generic_url_shape(obs_url(ob)) in valid_shapes]
            warm_shape_new_html = sum(1 for ob in warm_shape_obs if (source_path(ob.get("source_html"), run_root) or Path("__missing__")).is_file())
            warm_shape_new_valid = sum(1 for ob in warm_shape_obs if valid_html(ob, run_root, {} )[0])
            warm_comparison = {"warm_fresh_process_run": bool(warm_rows), "cold_unique_observed_urls": len(cold_urls), "warm_unique_observed_urls": len(warm_urls), "warm_new_urls": len(warm_urls - cold_urls), "warm_same_urls_as_cold": len(warm_urls & cold_urls), "warm_new_urls_with_cold_observed_page_class": new_same_shape, "page_classification_match_is_urlshape": False, "warm_same_url_with_saved_html": fresh_same, "fresh_html_cache_reuse_claim": False, "warm_valid_shape_match_new_urls": len(warm_shape_obs) if method == "B2" else None, "warm_valid_shape_match_new_html": warm_shape_new_html if method == "B2" else None, "warm_valid_shape_match_new_valid_html": warm_shape_new_valid if method == "B2" else None, "cold_shape_results": len(shape_results) if method == "B2" else None, "cold_valid_shapes": len(valid_shapes) if method == "B2" else None, "shape_match_interpretation": "weak URL-shape proxy only; not a success metric" if method == "B2" else None}
            entry = {"id": site_id, "method": method, "gold_cohort": cohort_name, "expected_type": site_gold.get("expected_type"), "observed_target_kind": site_gold.get("observed_target_kind"), "kind_evidence": site_gold.get("kind_evidence"), "gold_count": len(eligible_gold), "raw_target_record_count": len(gold), "strict_gold_count": len(strict_urls), "proxy_gold_count": len(proxy_urls), "unestablished_target_record_count": len(unestablished_urls) if cohort_name == "unestablished" else 0, "warm_comparison": warm_comparison, "phases": phases}
            per_site.append(entry)
            method_rows.append(entry)
        strict_den = sum(x["strict_gold_count"] for x in method_rows)
        proxy_den = sum(x["proxy_gold_count"] for x in method_rows)
        fixed_den = sum(x["gold_count"] for x in method_rows)
        phase_summary = {}
        numeric_fields = ("html_document_requests", "html_document_responses", "html_http_requests", "html_xhr_fetch", "html_response_bytes", "browser_ms", "all_http_requests")
        sitemap_fields = ("requests", "responses", "bytes", "elapsed_ms")
        robots_fields = ("requests", "responses", "bytes")
        for phase in ("cold", "warm"):
            phase_rows = [x["phases"][phase] for x in method_rows]
            metrics = {"status_counts": {status: sum(r["status"] == status for r in phase_rows) for status in ("unavailable", "unrun", "truncated", "blocked", "valid", "targetreach")}, "fixed_hits": sum(r["urlmap_fixed_hits"] for r in phase_rows), "fixed_denominator": fixed_den, "strict_hits": sum(r["urlmap_strict_hits"] for r in phase_rows), "strict_denominator": strict_den, "proxy_hits": sum(r["urlmap_proxy_hits"] for r in phase_rows), "proxy_denominator": proxy_den, "valid_html": sum(r["valid_html"] for r in phase_rows), "strict_targetreach_valid_html": sum(r["strict_targetreach_valid_html"] for r in phase_rows), "proxy_targetreach_valid_html": sum(r["proxy_targetreach_valid_html"] for r in phase_rows), "actual_html": sum(r["actual_html"] for r in phase_rows), "html_attempts": sum(r["html_attempts"] for r in phase_rows)}
            for field in numeric_fields:
                vals = [r[field] for r in phase_rows if isinstance(r.get(field), (int, float))]
                metrics[field] = sum(vals) if vals else None
                metrics[f"{field}_reported_sites"] = len(vals)
            metrics["sitemap_discovery"] = {key: sum(r["sitemap_discovery"][key] for r in phase_rows if isinstance(r["sitemap_discovery"].get(key), (int, float))) if any(isinstance(r["sitemap_discovery"].get(key), (int, float)) for r in phase_rows) else None for key in sitemap_fields}
            metrics["robots"] = {key: sum(r["robots"][key] for r in phase_rows if isinstance(r["robots"].get(key), (int, float))) if any(isinstance(r["robots"].get(key), (int, float)) for r in phase_rows) else None for key in robots_fields}
            external_values = [r["external_llm_calls"] for r in phase_rows if isinstance(r.get("external_llm_calls"), (int, float))]
            metrics["external_llm_calls"] = sum(external_values) if external_values else None
            metrics["external_llm_calls_reported_sites"] = len(external_values)
            metrics["local_inference"] = {key: sum(r["local_inference"][key] for r in phase_rows if isinstance(r["local_inference"].get(key), (int, float))) if any(isinstance(r["local_inference"].get(key), (int, float)) for r in phase_rows) else None for key in ("calls", "input_tokens", "runtime_ms")}
            phase_summary[phase] = metrics
        cohort[method] = {"site_count": len(sites), "verified_gold_site_count": sum(x["gold_cohort"] == "body_verified" for x in method_rows), "proxy_site_count": sum(x["gold_cohort"] == "proxy" for x in method_rows), "unestablished_site_count": sum(x["gold_cohort"] == "unestablished" for x in method_rows), "fixed_target_count": fixed_den, "strict_body_verified_target_count": strict_den, "proxy_target_count": proxy_den, "unestablished_target_records": sum(x["unestablished_target_record_count"] for x in method_rows), "unestablished_target_map_hits": {phase: sum(x["phases"][phase]["urlmap_unestablished_observed"] for x in method_rows) for phase in ("cold", "warm")}, "warm_comparison": {"warm_new_urls": sum(x["warm_comparison"]["warm_new_urls"] for x in method_rows), "warm_same_urls_as_cold": sum(x["warm_comparison"]["warm_same_urls_as_cold"] for x in method_rows), "warm_new_urls_with_cold_observed_page_class": sum(x["warm_comparison"]["warm_new_urls_with_cold_observed_page_class"] for x in method_rows), "page_classification_match_is_urlshape": False, "warm_same_url_with_saved_html": sum(x["warm_comparison"]["warm_same_url_with_saved_html"] for x in method_rows), "warm_valid_shape_match_new_urls": sum(x["warm_comparison"]["warm_valid_shape_match_new_urls"] or 0 for x in method_rows if x["warm_comparison"]["warm_valid_shape_match_new_urls"] is not None) if method == "B2" else None, "warm_valid_shape_match_new_html": sum(x["warm_comparison"]["warm_valid_shape_match_new_html"] or 0 for x in method_rows if x["warm_comparison"]["warm_valid_shape_match_new_html"] is not None) if method == "B2" else None, "warm_valid_shape_match_new_valid_html": sum(x["warm_comparison"]["warm_valid_shape_match_new_valid_html"] or 0 for x in method_rows if x["warm_comparison"]["warm_valid_shape_match_new_valid_html"] is not None) if method == "B2" else None, "cold_shape_results": sum(x["warm_comparison"]["cold_shape_results"] or 0 for x in method_rows if x["warm_comparison"]["cold_shape_results"] is not None) if method == "B2" else None, "cold_valid_shapes": sum(x["warm_comparison"]["cold_valid_shapes"] or 0 for x in method_rows if x["warm_comparison"]["cold_valid_shapes"] is not None) if method == "B2" else None, "shape_match_interpretation": "weak URL-shape proxy only; not a success metric" if method == "B2" else None, "fresh_html_cache_reuse_claim": False}, "phases": phase_summary}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "per-site.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in per_site), encoding="utf-8")
    (out_dir / "cohort-summary.json").write_text(json.dumps(cohort, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    score = {"schema": "structure-routing-b2-score-v1", "results_sha256": hashlib.sha256(args.results.read_bytes()).hexdigest(), "frozen_gold_sha256": frozen_hash, "methods": cohort, "unknown_cost_is_null": True, "cpu_time": None, "monetary_cost": None}
    (out_dir / "score.json").write_text(json.dumps(score, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
