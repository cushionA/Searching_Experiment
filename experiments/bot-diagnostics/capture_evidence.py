"""Save selector candidates and challenge screens from existing evidence; no network/actions."""
import hashlib
import json
import shutil
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUN = REPO / "lab-runs/bot-diagnostics-004-sites"
OUTPUT = REPO / "lab-runs/bot-diagnostics-evidence"


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag in ("a", "button", "input", "select", "iframe"):
            kept = {k: v for k, v in data.items() if k in (
                "id", "name", "type", "role", "href", "src", "aria-label", "aria-controls",
                "title", "disabled", "data-open-menu", "placeholder", "tabindex",
            )}
            item = {"tag": tag, "attrs": kept, "text": ""}
            self.items.append(item)
            if tag not in ("input",):
                self.stack.append((tag, item))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        for _, item in self.stack:
            item["text"] += " " + data.strip()


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    summary = json.loads((RUN / "summary.json").read_text())
    candidates = []
    choices = {
        "amazon": ("rebrowser-lightpanda", "#twotabsearchtextbox", "id", "twotabsearchtextbox", "search input"),
        "joshin": ("patchright", "#suggest_input", "id", "suggest_input", "search input"),
        "homes": ("patchright", 'button[data-open-menu][aria-controls="Menu"]', "aria-controls", "Menu", "menu button"),
        "indeed": ("patchright", "#returnHome", "id", "returnHome", "navigation link on denied page; not a CAPTCHA button"),
    }
    for site, (tool, selector, attribute, value, purpose) in choices.items():
        result = next(r for r in summary["sites"] if r["client"] == tool and r["target"] == site)
        source = RUN / "blobs" / result["evidence_sha256"]
        html = source.read_text()
        parsed = Elements()
        parsed.feed(html)
        matches = [e for e in parsed.items if e["attrs"].get(attribute) == value]
        if site == "homes":
            matches = [e for e in matches if "data-open-menu" in e["attrs"]]
        destination = OUTPUT / f"{site}-source-dom.html"
        shutil.copyfile(source, destination)
        candidates.append({
            "site": site, "selector": selector, "purpose": purpose,
            "source_tool": tool, "source_sha256": result["evidence_sha256"],
            "source_dom": str(destination.relative_to(REPO)), "saved_dom_match_count": len(matches),
            "status": "observed_in_saved_dom; interaction not executed",
            "elements": matches, "operation_steps": "deferred by user",
        })
    (OUTPUT / "selector-candidates.json").write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + "\n")
    (REPO / "experiments/bot-diagnostics/selector-candidates.json").write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + "\n")
    challenges = []
    for result in summary["sites"]:
        if result["outcome"] not in ("challenge_observed", "access_denied_observed"):
            continue
        stem = f"{result['client']}-{result['target']}"
        source = RUN / "blobs" / result["evidence_sha256"]
        html = OUTPUT / f"{stem}-challenge.html"
        shutil.copyfile(source, html)
        parsed = Elements()
        parsed.feed(source.read_text(errors="replace"))
        controls = [e for e in parsed.items if e["tag"] in ("button", "input", "iframe") or e["attrs"].get("id") == "returnHome"]
        screenshot = RUN / (result.get("screenshot") or f"{stem}.png")
        saved_image = None
        if screenshot.exists():
            saved_image = OUTPUT / f"{stem}-challenge.png"
            shutil.copyfile(screenshot, saved_image)
        challenges.append({
            "site": result["target"], "tool": result["client"], "outcome": result["outcome"],
            "http_status": result.get("http_status"), "html": str(html.relative_to(REPO)),
            "source_sha256": result["evidence_sha256"],
            "screenshot": str(saved_image.relative_to(REPO)) if saved_image else None,
            "screenshot_status": "native browser screenshot" if saved_image else "not available; HTTP client or no rendering engine",
            "controls": controls, "simple_button_recovery": "not confirmed; no recovery action executed",
        })
    (OUTPUT / "challenge-index.json").write_text(json.dumps(challenges, ensure_ascii=False, indent=2) + "\n")
    files = {str(p.relative_to(OUTPUT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in OUTPUT.iterdir() if p.is_file() and p.name != "SHA256.json"}
    (OUTPUT / "SHA256.json").write_text(json.dumps(files, indent=2) + "\n")
    lines = ["# セレクタ候補とチャレンジ画面", "", "初回実サイト調査では操作は未実施。保存済みDOM内の一致確認まで実施した。共通フレームワークの操作オプションは別途fixtureで検証する。ボタンだけで突破できるCAPTCHAは今回確認できていない。IndeedのReturn homeは通常の遷移リンクで、CAPTCHA突破ボタンではない。", "", "| サイト | 候補 | DOM一致数 |", "|---|---|---|"]
    lines += [f"| {c['site']} | `{c['selector']}` | {c['saved_dom_match_count']} |" for c in candidates]
    lines += ["", "## チャレンジ・拒否画面", ""]
    for c in challenges:
        lines += [f"### {c['tool']} / {c['site']} ({c['http_status']})", "", f"[保存HTML]({REPO / c['html']})", ""]
        if c["screenshot"]:
            lines += [f"![保存画面]({REPO / c['screenshot']})", ""]
        else:
            lines += ["実画面の画像なし。保存HTMLを保持。", ""]
    (REPO / "docs/bot-diagnostics-evidence.md").write_text("\n".join(lines))
    print(json.dumps({"selectors": len(candidates), "challenges": len(challenges), "directory": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
