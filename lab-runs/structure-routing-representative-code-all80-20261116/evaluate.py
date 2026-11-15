import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlsplit


RUN = Path(__file__).resolve().parent
PRIVATE = Path.home() / ".codex/evaluations/structure-routing-representative-code-all80-20261116"
SEED = "representative-code-all80-20261116-v1"
TERMS = re.compile(r"category|catalog(?:ue)?|browse|scroll|load[-_ ]?more|pagination|handbook|documentation|guides?|products?|data[-_ ]?table|一覧|目次|カテゴリ|製品", re.I)
DETAIL = re.compile(r"/(?:products?|items?|detail|catalogue|catalog|docs?|guides?)/.+", re.I)
BAD = re.compile(r"/(?:login|logout|account|cart|checkout|register|signup|purchase|search)(?:/|$)", re.I)
DROP = re.compile(r"\.(?:js|css|map|png|jpe?g|gif|svg|pdf|zip|woff2?|mp[34])(?:[?#].*)?$", re.I)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def targets():
    return rows(RUN / "targets.jsonl")


def allowed(target, url):
    parsed = urlsplit(url)
    hosts = {str(x).casefold().rstrip(".") for x in target.get("allowed_hosts", [])}
    return parsed.scheme in ("http", "https") and (parsed.hostname or "").casefold().rstrip(".") in hosts


def links(dom, base):
    result = []
    for link in dom.get("links", []) if isinstance(dom, dict) else []:
        href = link.get("url") or link.get("href")
        if href:
            item = dict(link)
            item["url"] = urljoin(base, href)
            result.append(item)
    return result


def ids_arg(value):
    return {x.strip() for x in value.split(",") if x.strip()} if value else None


def select_lists(only):
    selection = read(PRIVATE / "list-selection.json") if (PRIVATE / "list-selection.json").exists() else {"seed": SEED, "sites": []}
    known = {str(x["id"]) for x in selection["sites"]}
    audit = {str(x["id"]): x for x in selection["sites"]}
    selected = []
    for target in targets():
        sid = str(target["id"])
        if only and sid not in only or sid in known:
            continue
        site = RUN / "sites" / sid
        rep = read(site / "representative.json")
        reps = read(site / "representatives.json") if (site / "representatives.json").exists() else {}
        initial = set(reps.get("initial_document_urls", []))
        pool = []
        for link in links(rep.get("dom"), rep.get("url") or target["start_url"]):
            url = link["url"]
            path = urlsplit(url).path
            if url in initial or url == rep.get("url") or not allowed(target, url) or urlsplit(url).fragment or DROP.search(path) or BAD.search(path):
                continue
            if str(link.get("region", "")).casefold() in {"nav", "header", "footer"}:
                continue
            pool.append(link)
        groups = defaultdict(list)
        for link in pool:
            path = urlsplit(link["url"]).path.rstrip("/")
            groups[(path.rsplit("/", 1)[0].casefold(), str(link.get("parent", {}).get("tag", "")).casefold(), " ".join(link.get("parent", {}).get("classes", [])).casefold())].append(link)
        ranked = []
        for index, link in enumerate(pool):
            path = urlsplit(link["url"]).path
            score = 5 * bool(TERMS.search(str(link.get("text", "")) + " " + path))
            group_key = (path.rstrip("/").rsplit("/", 1)[0].casefold(), str(link.get("parent", {}).get("tag", "")).casefold(), " ".join(link.get("parent", {}).get("classes", [])).casefold())
            score += 2 * (len(groups[group_key]) > 1)
            score -= 5 * bool(DETAIL.search(path) and re.search(r"\.html?$|/\d+/?$", path, re.I))
            if score > 0:
                tie = hashlib.sha256(f"{SEED}|{sid}|{link['url']}".encode()).hexdigest()
                ranked.append((-score, tie, index, link))
        if ranked:
            _, tie, _, chosen = min(ranked)
            value = {"id": sid, "status": "selected", "url": chosen["url"], "choice_sha256": tie}
            write(site / "holdout-start.json", {"url": chosen["url"], "source": "independent_seed_link_selection"})
            selected.append(value)
        else:
            value = {"id": sid, "status": "unavailable", "url": None}
        audit[sid] = value
        write(PRIVATE / "list-selection.json", {"seed": SEED, "procedure": "generic terms and repeated parent, deterministic SHA-256 tie break", "sites": list(audit.values())})
    return {"action": "select-lists", "selected": selected}


def capture_links(site, name, fallback):
    path = site / "evaluation" / f"{name}.json"
    if not path.exists():
        return []
    obj = read(path)
    return links(obj.get("dom"), obj.get("url") or fallback)


def normalized_shape(link):
    p = urlsplit(link["url"])
    segments = [re.sub(r"\d+", "{n}", x) for x in p.path.rstrip("/").split("/")]
    return (p.hostname, "/".join(segments[:-1]).casefold(), str(link.get("parent", {}).get("tag", "")).casefold(), " ".join(link.get("parent", {}).get("classes", [])).casefold())


def select_targets(only):
    path = PRIVATE / "targets.json"
    fixed = read(path) if path.exists() else {"seed": SEED, "targets": {}, "sites": []}
    existing = fixed.setdefault("targets", {})
    audit = {str(x["id"]): x for x in fixed.setdefault("sites", [])}
    for target in targets():
        sid = str(target["id"])
        if (only and sid not in only) or sid in audit:
            continue
        site = RUN / "sites" / sid
        rep = read(site / "representative.json")
        initial = set(read(site / "representatives.json").get("initial_document_urls", [])) if (site / "representatives.json").exists() else set()
        start = read(site / "holdout-start.json").get("url") if (site / "holdout-start.json").exists() else None
        fallback = start or rep.get("url") or target["start_url"]
        before = capture_links(site, "listing-before", fallback)
        after = capture_links(site, "listing-after", fallback)
        before_urls = {x["url"] for x in before}
        fresh = [x for x in after if x["url"] not in before_urls]
        base = fresh or after or before
        pool = [x for x in base if allowed(target, x["url"]) and x["url"] not in initial and x["url"] != start and not urlsplit(x["url"]).fragment and not DROP.search(urlsplit(x["url"]).path) and not BAD.search(urlsplit(x["url"]).path) and str(x.get("region", "")).casefold() not in {"nav", "header", "footer"}]
        grouped = defaultdict(list)
        for link in pool:
            grouped[normalized_shape(link)].append(link)
        ordered = sorted(grouped.values(), key=lambda g: (-len(g), min(pool.index(x) for x in g)))
        chosen = []
        for group in ordered:
            for link in sorted(group, key=lambda x: hashlib.sha256(f"{SEED}|target|{sid}|{x['url']}".encode()).hexdigest()):
                if link["url"] not in {x["url"] for x in chosen}:
                    chosen.append(link)
                if len(chosen) >= 3:
                    break
            if len(chosen) >= 3:
                break
        urls = [x["url"] for x in chosen]
        if urls:
            existing[sid] = urls
        audit[sid] = {"id": sid, "status": "selected" if urls else "unavailable", "count": len(urls), "preferred_scroll_fresh": bool(fresh)}
        fixed["sites"] = list(audit.values())
        write(path, fixed)
    return {"action": "select-targets", "sites": list(audit.values())}


def score():
    import importlib.util
    ref = read(RUN / "evaluation-policy-ref.json")
    policy = read(ref["policy_file"])
    spec = importlib.util.spec_from_file_location("frozen_sitemap_score", RUN.parent.parent / "experiments/structure-routing/sitemap-agent-score.py")
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    result_rows = rows(RUN / "results.jsonl") if (RUN / "results.jsonl").exists() else []
    grouped = defaultdict(list)
    for row in result_rows:
        grouped[(str(row.get("id")), row.get("phase"))].append(row)
    fixed = read(PRIVATE / "targets.json") if (PRIVATE / "targets.json").exists() else {"targets": {}}
    output = []
    for target in targets():
        sid = str(target["id"])
        reps = RUN / "sites" / sid / "representatives.json"
        initial = set(read(reps).get("initial_document_urls", [])) if reps.exists() else set()
        phase_results = {}
        for phase in ("cold", "warm"):
            attempts = grouped.get((sid, phase), [])
            rows_scored = []
            for row in attempts:
                items = [scorer.evaluate_item(item, policy.get("sites", {}).get(sid, {}), RUN) for item in row.get("selected_items", [])]
                rows_scored.append({"status": row.get("status"), "result": row, "items": items})
            phase_results[phase] = rows_scored
        targets_fixed = set(fixed.get("targets", {}).get(sid, []))
        all_rows = [x["result"] for values in phase_results.values() for x in values]
        reached = {u for row in all_rows for item in row.get("selected_items", []) for u in (item.get("url"), item.get("final_url")) if u}
        output.append({"id": sid, "status": "observed" if all_rows else "not_run", "phases": phase_results,
                       "fixed_candidate": {"count": len(targets_fixed), "reached_count": len(targets_fixed & reached), "reached_urls": sorted(targets_fixed & reached)},
                       "initial_urls": sorted(initial)})
    out = {"evaluation": "representative_code_all80", "policy_sha256": ref.get("sha256"), "sites": output,
           "candidate_reach_is_not_semantic_validation": True, "frozen_classifier": ref.get("classifier_file")}
    write(RUN / "evaluation.json", out)
    return {"action": "score", "sites": len(output), "output": str(RUN / "evaluation.json")}


def main():
    global RUN, PRIVATE
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", required=True, choices=("select-lists", "select-targets", "score"))
    parser.add_argument("--run", type=Path, default=RUN)
    parser.add_argument("--ids", default="")
    args = parser.parse_args()
    RUN = args.run.resolve()
    PRIVATE = Path.home() / ".codex/evaluations/structure-routing-representative-code-all80-20261116"
    fn = {"select-lists": select_lists, "select-targets": select_targets, "score": lambda _: score()}[args.action]
    value = fn(ids_arg(args.ids))
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
