import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from lxml import html


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def resolve_ref(ref, run_root):
    if not isinstance(ref, str) or not ref:
        return None
    path = Path(ref)
    return path if path.is_absolute() else run_root / path


def read_html_ref(ref, run_root):
    path = resolve_ref(ref, run_root)
    if not path or not path.is_file():
        return None, None, "missing_html"
    raw = path.read_bytes()
    if not raw.strip():
        return path, None, "empty_html"
    try:
        tree = html.fromstring(raw)
    except (ValueError, TypeError, html.ParserError):
        return path, None, "invalid_html"
    return path, (raw, tree), None


def text_content(tree, xpath):
    return " ".join(" ".join(node.itertext()).strip() for node in tree.xpath(xpath) if hasattr(node, "itertext")).strip()


def has_nonempty_main(tree):
    nodes = tree.xpath("//main | //article")
    return any(len(" ".join(node.itertext()).strip()) >= 80 for node in nodes)


def visible_body_text(tree):
    values = tree.xpath("//body//text()[not(ancestor::script or ancestor::style or ancestor::noscript or ancestor::template or ancestor::nav or ancestor::header or ancestor::footer or ancestor::aside or ancestor::svg)]")
    return re.sub(r"\s+", " ", " ".join(values)).strip()


def page_gate(item, run_root):
    source_path, source_data, source_error = read_html_ref(item.get("source_html"), run_root)
    rendered_path, rendered_data, rendered_error = read_html_ref(item.get("rendered_dom"), run_root)
    if source_error:
        return None, "source_" + source_error
    if rendered_error:
        return None, "rendered_" + rendered_error
    source_raw, source_tree = source_data
    rendered_raw, rendered_tree = rendered_data
    hashes = item.get("hashes") if isinstance(item.get("hashes"), dict) else {}
    expected_hashes = {
        "source_html": item.get("source_sha256") or item.get("source_html_sha256") or hashes.get("source_html") or hashes.get("source_sha256") or hashes.get("source_html_sha256"),
        "rendered_dom": item.get("rendered_dom_sha256") or item.get("rendered_sha256") or hashes.get("rendered_dom") or hashes.get("rendered_dom_sha256") or hashes.get("rendered_sha256"),
    }
    for name, raw in (("source_html", source_raw), ("rendered_dom", rendered_raw)):
        expected = expected_hashes[name]
        if expected and hashlib.sha256(raw).hexdigest().lower() != str(expected).lower():
            return None, name + "_hash_mismatch"
    tree = rendered_tree
    body_text = visible_body_text(tree)
    title = text_content(tree, "//title").casefold()
    headings = " ".join(tree.xpath("//h1//text() | //h2//text()")).casefold()
    if re.search(r"\b(?:404|page not found|not found|under maintenance|maintenance mode|temporarily unavailable|service unavailable)\b", title + " " + headings):
        return None, "not_found_or_maintenance"
    if re.search(r"captcha|access denied|verify (?:that )?you are human|checking your browser|security check", title + " " + headings):
        return None, "challenge"
    if re.search(r"log ?in|sign ?in|account access", title + " " + headings) and tree.xpath("//input[@type='password']"):
        return None, "login"
    if len(body_text) < 80:
        return None, "empty_or_spa_shell"
    return {"source_path": str(source_path), "rendered_path": str(rendered_path), "tree": tree, "source_tree": source_tree, "status": item.get("status")}, "saved_html_valid"


def normalized_url(item):
    return str(item.get("final_url") or item.get("url") or "")


def matches_case(url, case):
    return any(re.search(pattern, url, re.I) for pattern in case.get("url_patterns", []))


NEW_EXPECTED_TYPES = {
    "documentation_page", "scholarly_article", "bibliographic_record", "public_dataset", "report",
    "book_or_museum_item", "news_article", "travel_accommodation_property",
}


def typed_scope(item, case):
    original_url = str(item.get("url") or "")
    final_url = str(item.get("final_url") or original_url)
    allowed_hosts = case.get("allowed_hosts", [])
    allowed_hosts = {str(value).casefold().rstrip(".") for value in allowed_hosts} if isinstance(allowed_hosts, list) else set()
    original_host = (urlsplit(original_url).hostname or "").casefold().rstrip(".")
    if allowed_hosts:
        original_allowed = original_host in allowed_hosts
    else:
        original_allowed = matches_case(original_url, case)
    if not original_allowed:
        return False, "url_outside_evaluator_scope"
    if final_url == original_url:
        return True, None
    if any(re.search(pattern, final_url, re.I) for pattern in case.get("allowed_final_url_patterns", [])):
        return True, None
    allowed_canonicals = case.get("canonical_urls", [])
    if isinstance(allowed_canonicals, list) and final_url in {str(value) for value in allowed_canonicals}:
        return True, None
    if any(re.search(pattern, final_url, re.I) for pattern in case.get("canonical_url_patterns", [])):
        return True, None
    final_host = (urlsplit(final_url).hostname or "").casefold().rstrip(".")
    if allowed_hosts and final_host in allowed_hosts:
        return True, None
    return False, "final_url_outside_evaluator_scope"


def schema_types(tree):
    found = set()
    for node in tree.xpath("//*[@itemtype]"):
        for value in (node.get("itemtype") or "").split():
            found.add(value.rstrip("/").rsplit("/", 1)[-1].casefold())
    for script in tree.xpath("//script[@type='application/ld+json']/text()"):
        try:
            data = json.loads(script)
        except (json.JSONDecodeError, TypeError):
            continue
        stack = [data]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                kind = value.get("@type")
                for item in kind if isinstance(kind, list) else [kind]:
                    if isinstance(item, str):
                        found.add(item.rsplit("/", 1)[-1].casefold())
                stack.extend(child for key, child in value.items() if key != "@type")
            elif isinstance(value, list):
                stack.extend(value)
    return found


def meta_values(tree):
    values = []
    for node in tree.xpath("//meta[@name or @property or @itemprop]"):
        key = " ".join(str(node.get(attr) or "") for attr in ("name", "property", "itemprop")).casefold()
        value = str(node.get("content") or "").strip()
        if key and value:
            values.append((key, value))
    return values


def typed_page_context(tree):
    body = visible_body_text(tree)
    headings = [re.sub(r"\s+", " ", " ".join(node.itertext())).strip() for node in tree.xpath("//h1")]
    title = text_content(tree, "//title")
    generic_heading = re.compile(r"^(?:search(?: results)?|results|all (?:items|products|articles|books)|browse|catalog|category|home|latest news)$", re.I)
    has_detail_heading = any(value and not generic_heading.fullmatch(value) for value in headings)
    main_text = text_content(tree, "//main | //article")
    has_detail_region = len(main_text) >= 120 or (has_detail_heading and len(body) >= 160)
    listing_shell = not has_detail_heading or not has_detail_region
    return body, headings, title, main_text, listing_shell


def typed_schema_match(expected, types):
    families = {
        "scholarly_article": {"scholarlyarticle", "medicalscholarlyarticle"},
        "bibliographic_record": {"book", "periodical", "chapter"},
        "public_dataset": {"dataset"},
        "report": {"report"},
        "book_or_museum_item": {"book", "visualartwork", "painting", "sculpture"},
        "news_article": {"newsarticle", "analysisnewsarticle", "reportagenewsarticle"},
        "travel_accommodation_property": {"lodgingbusiness", "hotel", "hostel", "resort", "apartment", "residence", "realestatelisting", "vacationrental", "touristattraction"},
    }
    return bool(types & families.get(expected, set()))


def has_expected_entity_type(expected, types):
    families = {
        "documentation_page": {"techarticle", "apireference", "technicalarticle"},
        "scholarly_article": {"scholarlyarticle", "medicalscholarlyarticle"},
        "bibliographic_record": {"book", "periodical", "chapter"},
        "public_dataset": {"dataset"},
        "report": {"report"},
        "book_or_museum_item": {"book", "visualartwork", "painting", "sculpture"},
        "news_article": {"newsarticle", "analysisnewsarticle", "reportagenewsarticle"},
        "travel_accommodation_property": {"lodgingbusiness", "hotel", "hostel", "resort", "apartment", "residence", "realestatelisting", "vacationrental", "touristattraction", "place"},
    }
    return bool(types & families.get(expected, set()))


def has_citation_metadata(tree):
    return any(
        re.search(r"(?:^|\s)(?:citation_(?:title|doi|publication_date|journal_title|isbn)|dc\.?\s*(?:title|type|identifier)|dcterms\.title)(?:$|\s)", key)
        for key, _ in meta_values(tree)
    )


def typed_classification(expected, tree):
    types = schema_types(tree)
    body, headings, title, main_text, listing_shell = typed_page_context(tree)
    if types & {"collectionpage", "searchresultspage", "itemlist"} and not has_expected_entity_type(expected, types):
        return "unverified", "listing_schema_without_expected_entity_type"
    if listing_shell:
        return "unverified", "detail_page_or_entity_heading_missing"
    if expected == "documentation_page":
        if tree.xpath("//h1") and tree.xpath("//main | //article") and has_nonempty_main(tree):
            return "strict_success", "documentation_heading_and_article_content"
        return "unverified", "documentation_heading_or_article_content_missing"
    if expected == "scholarly_article":
        if typed_schema_match(expected, types) and len(main_text) >= 120:
            return "strict_success", "scholarly_article_schema_and_article_content"
        if has_citation_metadata(tree) and len(main_text) >= 120:
            return "strict_success", "scholarly_citation_metadata_and_article_content"
        return "unverified", "scholarly_article_evidence_missing"
    if expected == "bibliographic_record":
        if (typed_schema_match(expected, types) or has_citation_metadata(tree)) and len(body) >= 160:
            return "strict_success", "bibliographic_schema_or_citation_metadata_and_record_content"
        return "unverified", "bibliographic_record_evidence_missing"
    if expected == "public_dataset":
        download_links = tree.xpath("//a[@href]/@href")
        download_text = " ".join(tree.xpath("//a[@href]//text()"))
        download_evidence = bool(re.search(r"download|\.csv(?:$|[?#])|\.json(?:$|[?#])|\.zip(?:$|[?#])|\.parquet(?:$|[?#])", " ".join(download_links) + " " + download_text, re.I))
        if "dataset" in types and len(body) >= 160 and download_evidence:
            return "strict_success", "dataset_schema_and_downloadable_resource"
        return "unverified", "public_dataset_evidence_missing"
    if expected == "report":
        if "report" in types and len(main_text) >= 120:
            return "strict_success", "report_schema_and_article_content"
        return "unverified", "report_evidence_missing"
    if expected == "book_or_museum_item":
        if typed_schema_match(expected, types) and len(body) >= 160:
            return "strict_success", "book_or_museum_schema_and_item_content"
        if has_citation_metadata(tree) and len(body) >= 160:
            return "strict_success", "bibliographic_metadata_and_item_content"
        return "unverified", "book_or_museum_item_evidence_missing"
    if expected == "news_article":
        metadata = meta_values(tree)
        article_meta = any(key in ("og:type", "property og:type") and value.casefold() == "article" for key, value in metadata)
        published = any(re.search(r"(?:published_time|datepublished|article:published_time)", key) for key, _ in metadata) or bool(tree.xpath("//time[@datetime]"))
        if typed_schema_match(expected, types) and len(main_text) >= 120:
            return "strict_success", "news_article_schema_and_article_content"
        if article_meta and published and len(main_text) >= 120:
            return "strict_success", "article_metadata_and_publication_date"
        return "unverified", "news_article_evidence_missing"
    if expected == "travel_accommodation_property":
        strong_types = {"lodgingbusiness", "hotel", "hostel", "resort", "apartment", "residence", "realestatelisting", "vacationrental", "touristattraction", "place"}
        if (types & strong_types) and len(body) >= 160:
            return "strict_success", "accommodation_or_property_schema_and_item_content"
        return "unverified", "accommodation_or_property_evidence_missing"
    return "unverified", "unsupported_expected_type"


def has_product_action(tree):
    for node in tree.xpath("//form | //button | //a | //*[@role='button']"):
        attrs = " ".join(str(node.get(key) or "") for key in ("id", "class", "name", "value", "aria-label", "data-testid"))
        text = " ".join(node.itertext())
        if re.search(r"add\s*to\s*cart|add\s*to\s*bag|buy\s*now|purchase|addtocart|add-to-cart", attrs + " " + text, re.I):
            return True
    return False


def independent_type(item, case, tree):
    expected = case.get("expected_type")
    if expected in NEW_EXPECTED_TYPES:
        in_scope, reason = typed_scope(item, case)
        if not in_scope:
            return "unverified", reason
        return typed_classification(expected, tree)
    url = normalized_url(item)
    if not matches_case(url, case):
        return "unverified", "url_outside_evaluator_scope"
    types = schema_types(tree)
    if expected == "product":
        if len(visible_body_text(tree)) < 80:
            return "unverified", "product_main_content_missing"
        if "product" in types:
            return "strict_success", "schema_product_evidence"
        if has_product_action(tree):
            return "strict_success", "standalone_purchase_action_evidence"
        return "unverified", "product_type_evidence_missing"
    if expected == "documentation":
        if tree.xpath("//h1") and tree.xpath("//main | //article") and has_nonempty_main(tree):
            return "strict_success", "documentation_heading_and_article_content"
        return "unverified", "documentation_heading_or_article_content_missing"
    if expected == "mission":
        heading = " ".join(tree.xpath("//h1//text() | //nav//text() | //ol[contains(@class,'breadcrumb')]//text()")).casefold()
        if {"mission", "spacecraft"} & types or ("mission" in heading and tree.xpath("//h1") and len(visible_body_text(tree)) >= 80):
            return "strict_success", "mission_schema_or_heading_evidence"
        return "unverified", "mission_type_evidence_insufficient"
    return "unverified", "unsupported_expected_type"


def get_snapshots(row):
    value = row.get("snapshots")
    if isinstance(value, dict):
        result = {
            "before": row.get("before_html") or row.get("beforeDOM") or row.get("before_dom") or value.get("before_html") or value.get("before"),
            "after": row.get("after_html") or row.get("afterDOM") or row.get("after_dom") or value.get("after_html") or value.get("after"),
        }
        return result
    if isinstance(value, list):
        result = {}
        for snapshot in value:
            if isinstance(snapshot, dict) and snapshot.get("phase") in ("before", "after"):
                result[snapshot["phase"]] = snapshot
        return result
    return {
        "before": row.get("before_html") or row.get("beforeDOM") or row.get("before_dom"),
        "after": row.get("after_html") or row.get("afterDOM") or row.get("after_dom"),
    }


def snapshot_info(ref, run_root, fallback_url=None):
    base_url = fallback_url
    if isinstance(ref, dict):
        base_url = ref.get("url") or ref.get("page_url") or base_url
        ref = ref.get("html") or ref.get("path") or ref.get("dom")
    path = resolve_ref(ref, run_root)
    if not path or not path.is_file():
        return {"path": str(path) if path else None, "sha256": None, "urls": []}
    raw = path.read_bytes()
    urls = []
    try:
        tree = html.fromstring(raw)
        urls = [urljoin(base_url or "", href) for href in tree.xpath("//a[@href]/@href")]
    except (ValueError, TypeError, html.ParserError):
        pass
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "urls": urls}


def snapshot_url(ref, run_root):
    if isinstance(ref, dict):
        return ref.get("url") or ref.get("page_url")
    path = resolve_ref(ref, run_root)
    if not path or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    if isinstance(value, dict):
        return value.get("url") or value.get("page_url") or value.get("current_url")
    return None


def model_calls(row):
    local = row.get("local_inference")
    local_calls = local.get("calls") if isinstance(local, dict) else None
    external_calls = row.get("external_llm_calls")
    if isinstance(local_calls, (int, float)) or isinstance(external_calls, (int, float)):
        return (local_calls if isinstance(local_calls, (int, float)) else 0) + (external_calls if isinstance(external_calls, (int, float)) else 0)
    for key in ("model_calls", "llm_calls", "agent_model_calls"):
        value = row.get(key)
        if isinstance(value, (int, float)):
            return value
    for key in ("model", "usage", "cost", "metrics"):
        section = row.get(key)
        if isinstance(section, dict):
            for subkey in ("model_calls", "llm_calls", "agent_model_calls"):
                value = section.get(subkey)
                if isinstance(value, (int, float)):
                    return value
    return None


def evaluate_item(item, case, run_root):
    result = {
        "url": item.get("url"), "final_url": item.get("final_url"),
        "candidate_valid": item.get("valid"), "candidate_saved": item.get("saved"),
        "classification": "unverified",
        "rationale": None,
        "saved_html_valid": False,
    }
    evidence, reason = page_gate(item, run_root)
    if evidence is None:
        result["rationale"] = reason
        return result
    result["saved_html_valid"] = True
    result["http_status"] = evidence.get("status")
    result["saved_files"] = {"source_html": evidence["source_path"], "rendered_dom": evidence["rendered_path"]}
    if str(evidence.get("status", "")) != "200":
        result["rationale"] = "http_status_not_200"
        return result
    classification, rationale = independent_type(item, case, evidence["tree"])
    result["classification"] = classification
    result["rationale"] = rationale
    return result


def evaluate_row(row, policy, run_root):
    raw_case = (policy.get("sites") or {}).get(str(row.get("id")))
    if raw_case is None:
        raw_case = next((case for case in policy.get("cases", []) if str(case.get("id")) == str(row.get("id"))), None)
    if isinstance(raw_case, dict):
        case = dict(raw_case)
        case.setdefault("id", str(row.get("id")))
        if "url_regex" in case and "url_patterns" not in case:
            case["url_patterns"] = [case["url_regex"]]
    else:
        case = None
    selected = row.get("selected_items") if isinstance(row.get("selected_items"), list) else []
    items = [evaluate_item(item, case or {}, run_root) for item in selected if isinstance(item, dict)]
    counts = Counter(item["classification"] for item in items)
    false_flags = sum(item.get("candidate_valid") is True and item["classification"] != "strict_success" for item in items)
    saved_without_evidence = sum(item.get("candidate_saved") is True and item["classification"] != "strict_success" for item in items)
    snapshots = get_snapshots(row)
    raw_snapshots = row.get("snapshots") if isinstance(row.get("snapshots"), dict) else {}
    snap_info = {}
    for key, value in snapshots.items():
        if value:
            base_url = row.get(key + "_url") or row.get("seed_url") or snapshot_url(raw_snapshots.get(key), run_root) or row.get("page_url")
            snap_info[key] = snapshot_info(value, run_root, base_url)
    growth_urls = []
    before_urls = set(snap_info.get("before", {}).get("urls", []))
    after_urls = set(snap_info.get("after", {}).get("urls", []))
    for url in sorted(after_urls - before_urls):
        if case and matches_case(url, case):
            growth_urls.append(url)
    reported_fresh_links = row.get("fresh_item_urls_after_scroll")
    if reported_fresh_links is None and isinstance(row.get("fresh_links_after_scroll"), list):
        reported_fresh_links = row.get("fresh_links_after_scroll")
    if isinstance(reported_fresh_links, list):
        reported_urls = {value.get("url") if isinstance(value, dict) else value for value in reported_fresh_links}
        growth_urls = [url for url in growth_urls if url in reported_urls]
    scroll_successes = [
        item for item in items
        if item["classification"] == "strict_success"
        and (item.get("url") in growth_urls or item.get("final_url") in growth_urls)
    ]
    return {
        "id": row.get("id"), "phase": row.get("phase"), "stateid": row.get("stateid"),
        "attempted_items": len(items), "saved_html_valid_count": sum(item["saved_html_valid"] for item in items), "strict_success": counts["strict_success"],
        "unverified": counts["unverified"], "false_candidate_flags": false_flags,
        "candidate_saved_without_strict_evidence": saved_without_evidence,
        "scroll_growth_successes": len(scroll_successes),
        "scroll_growth": {"fresh_matching_urls": growth_urls, "strict_saved_detail_successes": len(scroll_successes)},
        "snapshots": {key: {"path": val["path"], "sha256": val["sha256"]} for key, val in snap_info.items()},
        "model_calls_from_result": model_calls(row), "items": items,
    }


def evaluate(run_dir, policy_path):
    run_root = Path(run_dir).resolve()
    policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    rows = read_jsonl(run_root / "results.jsonl")
    cases = policy.get("sites") or {str(case.get("id")): case for case in policy.get("cases", [])}
    reports = [evaluate_row(row, policy, run_root) for row in rows]
    total = Counter()
    for report in reports:
        for key in ("attempted_items", "saved_html_valid_count", "strict_success", "unverified", "false_candidate_flags", "candidate_saved_without_strict_evidence", "scroll_growth_successes"):
            total[key] += report[key]
    return {
        "evaluation": "sitemap_url_group_targeted_pilot",
        "policy_version": policy.get("version"),
        "scope": {"case_ids": sorted(cases), "fixed_url_recall": None, "benchmark_80_site_score": None},
        "metrics": dict(total), "rows": reports,
        "interpretation": "Task/type-based strict saved-detail successes; not fixed-gold URL recall or the 80-site benchmark.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    args = parser.parse_args()
    json.dump(evaluate(args.run, args.policy), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
