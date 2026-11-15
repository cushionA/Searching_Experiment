import argparse
import hashlib
import importlib.util
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlsplit


RUN = Path(__file__).resolve().parent
PRIVATE = Path.home() / ".codex/evaluations/structure-routing-representative-code-20261115"
SEED = "representative-code-evaluation-20261115-v1"
LIST_TERMS = re.compile(r"category|catalog(?:ue)?|browse|scroll|load[-_ ]?more|pagination|handbook|documentation|guides?|products?|data[-_ ]?table|一覧|目次|カテゴリ|製品", re.I)
BAD_PATH = re.compile(r"/(?:login|logout|account|cart|checkout|search|product|item|detail)/[^/]+/?$", re.I)
DETAIL_PATH = re.compile(r"/(?:products?|items?|detail|catalogue|catalog|docs?|guides?)/.+", re.I)
UNSAFE_PATH = re.compile(r"/(?:login|logout|account|cart|checkout|register|signup|purchase)(?:/|$)", re.I)
DROP_SUFFIX = re.compile(r"\.(?:js|css|map|png|jpe?g|gif|svg|pdf|zip|woff2?|mp[34])(?:[?#].*)?$", re.I)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def targets():
    return rows(RUN / "targets.jsonl")


def allowed(target, url):
    parsed = urlsplit(url)
    hosts = {str(x).casefold().rstrip(".") for x in target.get("allowed_hosts", [])}
    return parsed.scheme in ("http", "https") and (parsed.hostname or "").casefold().rstrip(".") in hosts


def link_rows(dom, base):
    out = []
    for link in dom.get("links", []) if isinstance(dom, dict) else []:
        value = link.get("url") or link.get("href")
        if not value:
            continue
        item = dict(link)
        item["url"] = urljoin(base, value)
        out.append(item)
    return out


def seed_links(site, target):
    rep = read(site / "representative.json")
    initial = set((read(site / "representatives.json").get("initial_document_urls", [])) if (site / "representatives.json").exists() else [])
    links = link_rows(rep.get("dom"), rep.get("url") or target["start_url"])
    valid = []
    for link in links:
        url = link["url"]
        path = urlsplit(url).path
        if url in initial or not allowed(target, url) or url == rep.get("url"):
            continue
        if urlsplit(url).fragment or DROP_SUFFIX.search(path) or BAD_PATH.search(path):
            continue
        valid.append(link)
    return rep, valid


def select_lists():
    selected, audit = [], []
    for target in targets():
        sid = str(target["id"])
        site = RUN / "sites" / sid
        rep, links = seed_links(site, target)
        counts = Counter((str(x.get("text", "")).strip().casefold(), urlsplit(x["url"]).path.rstrip("/").rsplit("/", 1)[0]) for x in links)
        scored = []
        for index, link in enumerate(links):
            text = str(link.get("text", ""))
            path = urlsplit(link["url"]).path
            score = 5 * bool(LIST_TERMS.search(text + " " + path))
            score += 5 * bool(re.search(r"/category/|catalogue/category|カテゴリ", path + " " + text, re.I))
            score += 2 * (counts[(text.strip().casefold(), path.rstrip("/").rsplit("/", 1)[0])] > 1)
            score -= 5 * bool(DETAIL_PATH.search(path) and re.search(r"\.html?$|/\d+/?$", path, re.I))
            if score > 0:
                tie = hashlib.sha256(f"{SEED}|{sid}|{link['url']}".encode()).hexdigest()
                scored.append((-score, tie, index, link))
        if scored:
            _, _, _, choice = min(scored)
            value = {"url": choice["url"], "source": "independent_seed_link_selection", "reason": "generic_listing_terms_and_repetition", "choice_sha256": hashlib.sha256(f"{SEED}|{sid}|{choice['url']}".encode()).hexdigest()}
            write(site / "holdout-start.json", value)
            selected.append({"id": sid, **value})
            audit.append({"id": sid, "url": value["url"], "score": -min(scored)[0], "candidate_count": len(scored)})
        else:
            audit.append({"id": sid, "status": "not_available", "candidate_count": 0})
    write(PRIVATE / "list-selection.json", {"seed": SEED, "procedure": "generic path/text listing terms, repetition tie-break, fixed SHA-256", "sites": audit})
    return {"action": "select-lists", "selected": selected, "not_available": [x["id"] for x in audit if x.get("status")]}


def capture_links(site, name, fallback):
    meta = site / "evaluation" / f"{name}.json"
    if meta.exists():
        obj = read(meta)
        return link_rows(obj.get("dom"), obj.get("url") or fallback)
    htmlpath = site / "evaluation" / f"{name}.dom.html"
    if not htmlpath.exists():
        return []
    from lxml import html
    tree = html.fromstring(htmlpath.read_bytes())
    return [{"url": urljoin(fallback, href), "text": " ".join(a.itertext()).strip(), "parent": {"tag": a.getparent().tag, "classes": a.getparent().get("class", "")}} for a, href in zip(tree.xpath("//a[@href]"), tree.xpath("//a[@href]/@href"))]


def target_shape(link):
    path = urlsplit(link["url"]).path
    parent = path.rstrip("/").rsplit("/", 1)[0]
    node = link.get("parent") or {}
    return (parent.casefold(), str(node.get("tag", "")).casefold(), str(node.get("classes", "")).casefold())


def select_targets():
    audit, private_targets = [], {}
    for target in targets():
        sid = str(target["id"])
        site = RUN / "sites" / sid
        rep = read(site / "representative.json")
        reps_path = site / "representatives.json"
        initial = set(read(reps_path).get("initial_document_urls", [])) if reps_path.exists() else set()
        start = read(site / "holdout-start.json").get("url") if (site / "holdout-start.json").exists() else None
        before = capture_links(site, "listing-before", start or rep.get("url") or target["start_url"])
        after = capture_links(site, "listing-after", start or rep.get("url") or target["start_url"])
        before_urls = {x["url"] for x in before}
        fresh = [x for x in after if x["url"] not in before_urls]
        base = fresh or after or before
        pool = [x for x in base if allowed(target, x["url"]) and x["url"] not in initial and x["url"] != start and not urlsplit(x["url"]).fragment and not DROP_SUFFIX.search(urlsplit(x["url"]).path) and not UNSAFE_PATH.search(urlsplit(x["url"]).path)]
        grouped = defaultdict(list)
        for link in pool:
            grouped[target_shape(link)].append(link)
        groups = sorted(grouped.values(), key=lambda group: (-len(group), min(pool.index(x) for x in group)))
        chosen = []
        for group in groups:
            for link in group:
                if (DETAIL_PATH.search(urlsplit(link["url"]).path) or "documentation" in " ".join(target.get("tasks", [])).casefold()) and link["url"] not in {x["url"] for x in chosen}:
                    chosen.append(link)
                if len(chosen) == 3:
                    break
            if chosen:
                break
        urls = [x["url"] for x in chosen]
        if urls:
            private_targets[sid] = urls
        audit.append({"id": sid, "status": "selected" if urls else "unavailable", "count": len(urls), "after_preferred": bool(after)})
    write(PRIVATE / "targets.json", {"seed": SEED, "procedure": "largest repeated generic parent/path group; after-scroll links preferred; exclude initial body and listing URL", "targets": private_targets, "sites": audit})
    return {"action": "select-targets", "sites": audit}


def score():
    ref = read(RUN / "evaluation-policy-ref.json")
    policy_path = Path(ref["policy_file"])
    policy = read(policy_path)
    gold = read(PRIVATE / "targets.json") if (PRIVATE / "targets.json").exists() else {"targets": {}}
    spec = importlib.util.spec_from_file_location("frozen_sitemap_score", RUN.parent.parent / "experiments/structure-routing/sitemap-agent-score.py")
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    summaries = []
    result_rows = rows(RUN / "results.jsonl") if (RUN / "results.jsonl").exists() else []
    ids = [str(t["id"]) for t in targets()]
    for sid in ids:
        found = [x for x in result_rows if str(x.get("id")) == sid]
        observed = []
        for row in found:
            row = dict(row)
            phase = row.get("phase")
            for item in row.get("selected_items", []):
                result = scorer.evaluate_item(item, policy.get("sites", {}).get(sid, {}), RUN)
                result["phase"] = phase
                result["provenance"] = item.get("provenance")
                observed.append(result)
        fixed = set(gold.get("targets", {}).get(sid, []))
        strict_urls = {u for x in observed if x.get("classification") == "strict_success" for u in (x.get("url"), x.get("final_url")) if u}
        rep_path = RUN / "sites" / sid / "representatives.json"
        initial_urls = set(read(rep_path).get("initial_document_urls", [])) if rep_path.exists() else set()
        cold_urls = {u for x in observed if x.get("phase") == "cold" for u in (x.get("url"), x.get("final_url")) if u}
        novel = [x for x in observed if x.get("phase") == "warm" and x.get("classification") == "strict_success" and x.get("url") not in initial_urls | cold_urls]
        eligible = fixed - initial_urls - cold_urls
        summaries.append({"id": sid, "status": "observed" if found else "not_run", "holdout_status": "available" if (RUN / "sites" / sid / "holdout-start.json").exists() else "unavailable_or_pending", "items": observed, "fixed_target_recall": {"selected": len(fixed), "strict_reached": len(fixed & strict_urls), "recall": (len(fixed & strict_urls) / len(fixed)) if fixed else None}, "unobserved_target_reach": {"eligible_targets": len(eligible), "strict_reached": len(eligible & {x.get("url") for x in novel}), "recall": (len(eligible & {x.get("url") for x in novel}) / len(eligible)) if eligible else None}, "novel_warm_strict_successes": len(novel), "fixed_target_urls": sorted(fixed)})
    cost_rows = rows(RUN / "cost-ledger.jsonl") if (RUN / "cost-ledger.jsonl").exists() else []
    grouped_costs = defaultdict(lambda: {"document_reserved": 0, "ui_operations": 0, "http_requests": 0, "xhr_fetch": 0, "browser_ms": 0})
    for row in cost_rows:
        phase = str(row.get("phase", "unknown"))
        group = "initial_representative" if phase == "discover" else "fresh_process" if phase in ("cold", "warm") else "pre_codegen_preparation"
        for field in grouped_costs[group]:
            grouped_costs[group][field] += row.get(field) or 0
    out = {"evaluation": "representative_code_offline_evaluation", "policy_sha256": ref.get("sha256"), "sites": summaries, "costs_by_phase": dict(grouped_costs), "generation_cost": {"native_tokens": read(RUN / "protocol.json").get("native_tokens"), "status": "unknown" if read(RUN / "protocol.json").get("native_tokens") is None else "reported"}, "cost_rows": cost_rows, "evaluation_cost_rows": rows(RUN / "evaluation-cost.jsonl") if (RUN / "evaluation-cost.jsonl").exists() else []}
    write(RUN / "evaluation.json", out)
    return {"action": "score", "sites": len(summaries), "output": str(RUN / "evaluation.json")}


def main():
    global RUN
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", required=True, choices=("select-lists", "select-targets", "score"))
    parser.add_argument("--run", type=Path, default=RUN)
    args = parser.parse_args()
    RUN = args.run.resolve()
    result = {"select-lists": select_lists, "select-targets": select_targets, "score": score}[args.action]()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
