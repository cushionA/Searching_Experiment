import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urljoin, urlsplit

from lxml import html


ROOT = Path(__file__).resolve().parents[2]
NAVIGATION_TOKENS = {"nav", "navigation", "breadcrumb", "breadcrumbs", "pagination", "sidebar"}


def region(anchor):
    ancestors = list(anchor.iterancestors())
    for node in ancestors:
        tokens = set(re.split(r"[\s_-]+", f"{node.get('class', '')} {node.get('id', '')}".lower()))
        if node.tag in {"nav", "header", "footer", "aside"} or node.get("role", "").lower() == "navigation" or tokens & NAVIGATION_TOKENS:
            return "nav"
    return next((node.tag for node in ancestors if node.tag in {"main", "article", "section"}), "body")


def link_observations(markup, page_url):
    tree = html.fromstring(markup)
    links = []
    for anchor in tree.xpath("//a[@href]"):
        url = urljoin(page_url, anchor.get("href"))
        if urlsplit(url).scheme not in {"http", "https"}:
            continue
        parent = anchor.getparent()
        links.append({"url": url, "text": " ".join(anchor.text_content().split()),
                      "parent": {"tag": parent.tag, "classes": parent.get("class", "").split()}, "region": region(anchor)})
    return links


def digest(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--id", required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    started = time.perf_counter()
    health = [json.loads(line) for line in (run / "health-results.jsonl").read_text(encoding="utf-8").splitlines()]
    sample = next(row for row in health if row["id"] == args.id and row["status"] == "healthy")
    observation = sample["observation"]
    sources = list((run / "health" / args.id).glob(f"attempt-*/{observation['rendered_dom']}"))
    if len(sources) != 1:
        raise ValueError("Expected one preserved representative DOM")
    source = sources[0]
    links = link_observations(source.read_bytes(), observation["url"])
    pages = [{"url": observation["url"], "links": links}]
    script = "import fs from 'node:fs'; import {learnRules} from './experiments/structure-routing/runner.mjs'; process.stdout.write(JSON.stringify(learnRules(JSON.parse(fs.readFileSync(0,'utf8')))));"
    result = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps(pages),
                            text=True, encoding="utf-8", capture_output=True, cwd=ROOT, check=True)
    rules = json.loads(result.stdout)
    directory = run / "refined-rules" / args.id
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "rules.json"
    with output.open("x", encoding="utf-8") as stream:
        json.dump(rules, stream, ensure_ascii=False, indent=2)
    metadata = {"variant": "C1.1_navigation_region_fix", "id": args.id,
                "source_dom": str(source.relative_to(run)), "source_dom_sha256": digest(source),
                "representative_pages": 1, "new_document_loads": 0, "html_cache_counted_as_acquisition": False,
                "rule_learning_ms": round((time.perf_counter() - started) * 1000, 3),
                "rule_sha256": digest(output), "candidate_links": len(links),
                "navigation_links": sum(link["region"] == "nav" for link in links),
                "external_llm_calls": 0, "local_model_inference_calls": 0,
                "site_specific_selectors": 0, "common_rule_repairs": 1,
                "gold_input": False, "source_url": observation["url"]}
    (directory / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()
