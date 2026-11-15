import argparse
import json
import os
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urljoin, urlparse


ROOT = Path(__file__).resolve().parents[2]
INPUTS = ROOT / "lab-runs/structure-routing-all-candidates-20261115/common-inputs"
RUNS = ROOT / "lab-runs/structure-routing-all-candidates-20261115/others"


def load_seed_routes(port):
    manifest = json.loads((INPUTS / "manifest.json").read_text(encoding="utf-8"))
    routes = {}
    for case in manifest["cases"]:
        case_id = case["id"]
        host = urlparse(case["start_url"]).netloc
        for index, seed in enumerate(case["seeds"], 1):
            source = (ROOT / seed["dom"]).read_text(encoding="utf-8")
            source_url = urlparse(seed["url"])
            routes[f"/{case_id}{source_url.path}"] = source
            if index == 1:
                routes[f"/{case_id}/"] = source
            routes[f"/{case_id}/seed-{index}"] = source

    def response(path):
        source = routes.get(path)
        if source is None:
            return None
        case_id = path.split("/")[1]
        base = f"http://127.0.0.1:{port}/{case_id}"

        def rewrite(match):
            quote, href = match.group(1), match.group(2)
            parsed = urlparse(href)
            if parsed.scheme in ("http", "https"):
                return f"href={quote}{base}{parsed.path or '/'}{('?' + parsed.query) if parsed.query else ''}{quote}"
            if href.startswith("/"):
                return f"href={quote}/{case_id}{href}{quote}"
            return match.group(0)

        rewritten = re.sub(r"href=(['\"])(.*?)\1", rewrite, source, flags=re.I)
        rewritten = re.sub(r"<base\b[^>]*>", "", rewritten, flags=re.I)
        if re.search(r"<head\b[^>]*>", rewritten, re.I):
            rewritten = re.sub(
                r"(<head\b[^>]*>)",
                lambda match: match.group(1) + f'<base href="http://127.0.0.1:{port}/{case_id}/">',
                rewritten,
                count=1,
                flags=re.I,
            )
        return rewritten.encode("utf-8")

    return response


def serve(host, port):
    resolve = load_seed_routes(port)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            body = resolve(path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"fixture route not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", "default-src 'self' data: blob:; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            print("fixture-server", self.address_string(), self.command, urlparse(self.path).path, args[1] if len(args) > 1 else "")

    HTTPServer((host, port), Handler).serve_forever()


def autopager_module():
    sys.path.insert(0, str(ROOT / ".deps/structure-routing-all-others/autopager-pypi"))
    from autopager import extract
    from parsel import Selector
    return extract, Selector


def anchors(html, Selector):
    return Selector(text=html).css("a[href]")


def next_items(html, page_url, extract):
    rows = []
    for kind, element in extract(html):
        if kind != "NEXT":
            continue
        node = element.root
        text = " ".join("".join(node.itertext()).split())
        href = urljoin(page_url, node.attrib.get("href", ""))
        attrs = node.attrib
        classes = sorted(set((attrs.get("class") or "").split()))
        rel = sorted(set((attrs.get("rel") or "").lower().split()))
        alternatives = []
        css_selector = "a"
        if "next" in rel:
            alternatives.append({"rel_token": "next", "text_equals": text} if text else {"rel_token": "next"})
            css_selector = 'a[rel~="next"]'
        elif "next" in [value.lower() for value in classes]:
            alternatives.append({"class_token": "next", "text_equals": text} if text else {"class_token": "next"})
            css_selector = "a.next"
        elif "next" in (attrs.get("aria-label") or "").lower():
            alternatives.append({"aria_label_contains": "next", "text_equals": text} if text else {"aria_label_contains": "next"})
            css_selector = 'a[aria-label*="next"]'
        elif "next" in (attrs.get("title") or "").lower():
            alternatives.append({"title_contains": "next", "text_equals": text} if text else {"title_contains": "next"})
            css_selector = 'a[title*="next"]'
        elif text and "next" in text.lower():
            alternatives.append({"text_contains": "next", "text_equals": text})
        else:
            template = re.sub(r"\d+", "{n}", href)
            if template != href:
                alternatives.append({"href_template": template})
        rows.append({
            "href": href,
            "text": text[:100],
            "classes": classes,
            "rel": rel,
            "aria_label": attrs.get("aria-label", ""),
            "title": attrs.get("title", ""),
            "css_selector": css_selector,
            "alternatives": alternatives,
        })
    return rows


def apply_rule(html, page_url, rule, Selector):
    found = []
    for element in anchors(html, Selector):
        node = element.root
        href = urljoin(page_url, node.attrib.get("href", ""))
        text = " ".join("".join(node.itertext()).split())
        classes = {value.lower() for value in (node.attrib.get("class") or "").split()}
        rel = {value.lower() for value in (node.attrib.get("rel") or "").split()}
        aria_label = (node.attrib.get("aria-label") or "").lower()
        title = (node.attrib.get("title") or "").lower()
        for alt in rule.get("alternatives", []):
            primary = next((key for key in alt if key != "text_equals"), None)
            if primary is None:
                continue
            matches = alt.get("text_equals", text) == text
            if primary == "rel_token":
                matches = matches and alt[primary].lower() in rel
            elif primary == "class_token":
                matches = matches and alt[primary].lower() in classes
            elif primary == "text_contains":
                matches = matches and alt[primary].lower() in text.lower()
            elif primary == "aria_label_contains":
                matches = matches and alt[primary].lower() in aria_label
            elif primary == "title_contains":
                matches = matches and alt[primary].lower() in title
            elif primary == "href_template":
                pattern = re.escape(alt[primary]).replace(r"\{n\}", r"\d+")
                matches = matches and bool(re.fullmatch(pattern, href))
            else:
                matches = False
            if matches:
                found.append({"href": href, "text": text[:100], "matched_by": primary})
                break
    return found


def autopager_seed1():
    t_import = time.perf_counter()
    extract, Selector = autopager_module()
    import_seconds = time.perf_counter() - t_import
    manifest = json.loads((INPUTS / "manifest.json").read_text(encoding="utf-8"))
    result = {"source": "autopager PyPI 0.3.1", "package_import_seconds": round(import_seconds, 6), "model_calls": 0, "cases": []}
    for case in manifest["cases"]:
        seed = case["seeds"][0]
        html = (ROOT / seed["dom"]).read_text(encoding="utf-8")
        t0 = time.perf_counter()
        predictions = next_items(html, seed["url"], extract)
        elapsed = time.perf_counter() - t0
        links = anchors(html, Selector)
        result["model_calls"] += 1
        case_out = {
            "case": case["id"],
            "seed_url": seed["url"],
            "input_html_bytes": len(html.encode("utf-8")),
            "candidate_links": len(links),
            "model_calls": 1,
            "runtime_seconds": round(elapsed, 6),
            "next_predictions": predictions,
            "rule": {"source_case": case["id"], "source_url": seed["url"], "css_selector": predictions[0]["css_selector"] if predictions else None, "alternatives": predictions[0]["alternatives"] if predictions else []},
        }
        result["cases"].append(case_out)
    target = RUNS / "autopager-seed1-rules.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(target), "total_model_calls": result["model_calls"], "package_import_seconds": result["package_import_seconds"], "cases": [{"case": x["case"], "candidate_links": x["candidate_links"], "runtime_seconds": x["runtime_seconds"], "next_count": len(x["next_predictions"])} for x in result["cases"]]}, ensure_ascii=False))


def autopager_apply_all():
    _, Selector = autopager_module()
    manifest = json.loads((INPUTS / "manifest.json").read_text(encoding="utf-8"))
    rules = json.loads((RUNS / "autopager-seed1-rules.json").read_text(encoding="utf-8"))
    by_case = {entry["case"]: entry["rule"] for entry in rules["cases"]}
    result = {"source": str(RUNS / "autopager-seed1-rules.json"), "model_calls": 0, "pages": []}
    for case in manifest["cases"]:
        for seed in case["seeds"][1:]:
            html = (ROOT / seed["dom"]).read_text(encoding="utf-8")
            t0 = time.perf_counter()
            found = apply_rule(html, seed["url"], by_case[case["id"]], Selector)
            elapsed = time.perf_counter() - t0
            result["pages"].append({"case": case["id"], "seed_url": seed["url"], "candidate_links": len(anchors(html, Selector)), "model_calls": 0, "runtime_seconds": round(elapsed, 6), "rule_matches": found})
    target = RUNS / "autopager-seed1-rule-application.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(target), "model_calls": 0, "pages": [{"case": x["case"], "candidate_links": x["candidate_links"], "runtime_seconds": x["runtime_seconds"], "matches": len(x["rule_matches"])} for x in result["pages"]]}, ensure_ascii=False))


def autopager_native_file(path, page_url, output):
    t_import = time.perf_counter()
    extract, Selector = autopager_module()
    import_seconds = time.perf_counter() - t_import
    html = Path(path).read_text(encoding="utf-8")
    t0 = time.perf_counter()
    predicted = next_items(html, page_url, extract)
    elapsed = time.perf_counter() - t0
    result = {"source": "autopager PyPI 0.3.1", "package_import_seconds": round(import_seconds, 6), "input_path": str(Path(path).resolve()), "page_url": page_url, "input_html_bytes": len(html.encode("utf-8")), "candidate_links": len(anchors(html, Selector)), "model_calls": 1, "runtime_seconds": round(elapsed, 6), "next_predictions": predicted}
    Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(Path(output).resolve()), "candidate_links": result["candidate_links"], "model_calls": 1, "package_import_seconds": result["package_import_seconds"], "runtime_seconds": result["runtime_seconds"], "next_count": len(predicted)}, ensure_ascii=False))


def webnavigator_safe_run(mode):
    repo = ROOT / "lab-runs/structure-routing-20261115/candidate-audit/webNavigator"
    deps = ROOT / ".deps/structure-routing-all-others/webnavigator-deps"
    cwd = ROOT / ".deps/structure-routing-all-others/webnavigator-empty-cwd"
    nltk_data = ROOT / ".deps/structure-routing-all-others/nltk-data"
    cwd.mkdir(parents=True, exist_ok=True)
    nltk_data.mkdir(parents=True, exist_ok=True)
    config = RUNS / "webnavigator-minimal-config.yml"
    if mode == "minimal":
        config.write_text("logging: false\nbenchmark:\n  name: null\n  task_name: null\nenv:\n  task_ids: []\n", encoding="utf-8")
        args = [str(repo / "eval.py"), "--config", str(config)]
        suffix = "minimal-entry"
    elif mode == "example":
        source_config = repo / "configs/webarena/webNavigator-all-gpt4o.yml"
        config = RUNS / "webnavigator-example-config.yml"
        example_text = source_config.read_text(encoding="utf-8").replace("logging: True", "logging: False", 1)
        config.write_text(example_text, encoding="utf-8")
        args = [str(repo / "eval.py"), "--config", str(config)]
        suffix = "example-entry"
    else:
        args = [str(repo / "eval.py"), "--help"]
        suffix = "help"
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA")
    env = {name: os.environ[name] for name in keep if os.environ.get(name)}
    env.update({
        "PYTHONPATH": os.pathsep.join((str(deps), str(repo))),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHON_DOTENV_DISABLED": "1",
        "NLTK_DATA": str(nltk_data),
        "AWS_EC2_METADATA_DISABLED": "true",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "http_proxy": "http://127.0.0.1:9",
        "https_proxy": "http://127.0.0.1:9",
        "NO_PROXY": "",
        "no_proxy": "",
    })
    started = time.perf_counter()
    try:
        completed = subprocess.run([sys.executable, "-B", *args], cwd=cwd, env=env,
                                   capture_output=True, text=True, encoding="utf-8", timeout=60)
        stdout, stderr, code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        code = None
    elapsed = time.perf_counter() - started
    out_path = RUNS / f"webnavigator-{suffix}.stdout.log"
    err_path = RUNS / f"webnavigator-{suffix}.stderr.log"
    result_path = RUNS / f"webnavigator-{suffix}.json"
    out_path.write_text(stdout, encoding="utf-8")
    err_path.write_text(stderr, encoding="utf-8")
    last_error = next((line.strip() for line in reversed(stderr.splitlines()) if line.strip()), "")
    result = {
        "argv": ["python", "-B", *args],
        "cwd": str(cwd),
        "cwd_has_env_file": (cwd / ".env").exists(),
        "child_env_names": sorted(env),
        "credential_env_passed": any(name in env for name in ("OPENAI_API_KEY", "JINA_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")),
        "aws_metadata_disabled": env["AWS_EC2_METADATA_DISABLED"],
        "network_egress": "blocked through a refused loopback proxy",
        "dotenv_disabled": env["PYTHON_DOTENV_DISABLED"],
        "exit_code": code,
        "elapsed_seconds": round(elapsed, 3),
        "last_error": last_error,
        "stdout_path": str(out_path),
        "stderr_path": str(err_path),
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result": str(result_path), "exit_code": code, "elapsed_seconds": result["elapsed_seconds"], "last_error": last_error, "credential_env_passed": result["credential_env_passed"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--autopager-seed1", action="store_true")
    parser.add_argument("--autopager-apply-all", action="store_true")
    parser.add_argument("--autopager-native", action="store_true")
    parser.add_argument("--webnavigator-safe-help", action="store_true")
    parser.add_argument("--webnavigator-safe-minimal", action="store_true")
    parser.add_argument("--webnavigator-safe-example", action="store_true")
    parser.add_argument("--html")
    parser.add_argument("--url")
    parser.add_argument("--out")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.serve:
        serve(args.host, args.port)
    elif args.autopager_seed1:
        autopager_seed1()
    elif args.autopager_apply_all:
        autopager_apply_all()
    elif args.autopager_native:
        if not args.html or not args.url or not args.out:
            parser.error("--autopager-native requires --html, --url, and --out")
        autopager_native_file(args.html, args.url, args.out)
    elif args.webnavigator_safe_help:
        webnavigator_safe_run("help")
    elif args.webnavigator_safe_minimal:
        webnavigator_safe_run("minimal")
    elif args.webnavigator_safe_example:
        webnavigator_safe_run("example")
