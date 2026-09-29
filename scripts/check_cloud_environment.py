import argparse
import importlib
import json
import os
import sys


def inspect_environment(adaptive=False):
    result = {
        "python": ".".join(map(str, sys.version_info[:3])),
        "python_supported": sys.version_info >= (3, 12),
        "model_requested": "gpt-6-luna",
        "model_selection_surface": "codex_execution_ui",
        "model_runtime_verified": False,
        "codex_cli_required": False,
    }
    if adaptive:
        modules = {}
        for name in ("crawlee", "impit", "playwright"):
            try:
                importlib.import_module(name)
                modules[name] = True
            except ImportError:
                modules[name] = False
        try:
            from crawlee.crawlers import AdaptivePlaywrightCrawler  # noqa: F401

            modules["AdaptivePlaywrightCrawler"] = True
        except ImportError:
            modules["AdaptivePlaywrightCrawler"] = False
        result.update(
            adaptive_dependencies=modules,
            browser_sandbox_disabled=os.environ.get("CRAWLEE_DISABLE_BROWSER_SANDBOX", "").lower() == "true",
            https_proxy_configured=bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")),
        )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Codex Cloudコンテナの前提条件を確認する。モデル実体は実行画面で選択する。")
    parser.add_argument("--adaptive", action="store_true")
    args = parser.parse_args(argv)
    result = inspect_environment(args.adaptive)
    ok = result["python_supported"]
    if args.adaptive:
        ok = ok and result["browser_sandbox_disabled"] and all(result["adaptive_dependencies"].values())
    result["ok"] = ok
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
