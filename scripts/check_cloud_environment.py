"""Offline preflight; optional probe connects only to the configured proxy, with no HTTP."""
import argparse
import importlib
import importlib.metadata
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jse.lab import MODEL
from jse.lab.network import environment_proxy, error_diagnostics, proxy_metadata, sanitize_detail
from jse.lab.state import canonical


def codex_binary():
    local = Path(__file__).resolve().parents[1] / ".deps/codex/node_modules/.bin/codex"
    return str(local) if local.is_file() else shutil.which("codex")


def check_environment(adaptive=False, http=False, backend="manual", url="https://www.aozora.gr.jp/robots.txt", probe_proxy=False):
    url = canonical(url)
    result = {"model_requested": MODEL, "model_selection_surface": {
        "manual": "codex_execution_ui", "native": "codex_subagent_configuration",
        "codex": "codex_cli_argument", "responses": "responses_api"}[backend],
        "model_runtime_verified": False, "codex_cli_required": backend == "codex",
        "python_supported": sys.version_info >= (3, 12), "python_version": sys.version.split()[0],
        "browser_sandbox_disabled": os.environ.get("CRAWLEE_DISABLE_BROWSER_SANDBOX", "").lower() == "true",
        "packages": {}, "errors": []}
    if not result["python_supported"]:
        result["errors"].append("Python 3.12以上が必要です")
    packages = ["crawlee", "impit"] if http or adaptive else []
    if adaptive:
        packages.append("playwright")
    for package in packages:
        try:
            importlib.import_module(package)
            result["packages"][package] = importlib.metadata.version(package)
        except (ImportError, importlib.metadata.PackageNotFoundError) as error:
            result["packages"][package] = None
            result["errors"].append(f"{package}: {sanitize_detail(error)}")
    if adaptive:
        try:
            from crawlee.crawlers import AdaptivePlaywrightCrawler
            from playwright.sync_api import sync_playwright
            result["adaptive_crawler_available"] = AdaptivePlaywrightCrawler is not None
            with sync_playwright() as driver:
                result["chromium_installed"] = Path(driver.chromium.executable_path).is_file()
            if not result["chromium_installed"]:
                result["errors"].append("Playwright Chromiumが未導入です")
        except ImportError as error:
            result["adaptive_crawler_available"] = False
            result["chromium_installed"] = False
            result["errors"].append(sanitize_detail(error))
        if not result["browser_sandbox_disabled"]:
            result["errors"].append("制限付きCloudでadaptiveを使うにはCRAWLEE_DISABLE_BROWSER_SANDBOX=trueが必要です")
    proxies = urllib.request.getproxies()
    proxy = environment_proxy(url, proxies)
    result.update(target=url, https_proxy_configured=bool(proxies.get("https")),
                  no_proxy_applies=urllib.request.proxy_bypass_environment(urlsplit_netloc(url), proxies),
                  network_route="environment_proxy" if proxy else "direct_public_ip", **proxy_metadata(proxy))
    if result.get("proxy_configuration_invalid"):
        result["errors"].append("HTTPS proxy設定が不正です")
    if probe_proxy:
        result["proxy_probe"] = {"scope": "proxy DNS/TCP only; no CONNECT, TLS or origin HTTP", "status": "not_applicable"}
        if proxy and not result.get("proxy_configuration_invalid"):
            try:
                with socket.create_connection((result["proxy_host"], result["proxy_port"]), timeout=3):
                    pass
                result["proxy_probe"]["status"] = "tcp_connected"
            except OSError as error:
                result["proxy_probe"].update(status="failed", **error_diagnostics(error, proxy))
                result["errors"].append("環境proxyへのDNS/TCP接続に失敗しました")
    if backend == "codex":
        binary = codex_binary()
        result["codex_installed"] = bool(binary)
        authenticated = bool(os.environ.get("CODEX_API_KEY"))
        if binary and not authenticated:
            try:
                # Do not print authentication output; it can contain account information.
                status = subprocess.run([binary, "login", "status"], capture_output=True, timeout=10)
                authenticated = status.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
        result["model_auth_configured"] = authenticated
        if not binary or not authenticated:
            result["errors"].append("Codex CLIと独立したCLI認証が必要です。親Cloudタスクの認証は継承保証されません")
    elif backend == "responses":
        result["model_auth_configured"] = bool(os.environ.get("OPENAI_API_KEY"))
        if not result["model_auth_configured"]:
            result["errors"].append("実行プロセスにOPENAI_API_KEYが必要です。API利用は別課金です")
    elif backend == "native":
        result["native_tools_runtime_verified"] = False
        result["model_access_runtime_verified"] = False
        result["note"] = "親エージェントでモデル指定spawnが利用できるかは会話側で確認してください"
    result["ok"] = not result["errors"]
    result["python"] = result["python_version"]
    if adaptive:
        result["adaptive_dependencies"] = {name: result["packages"].get(name) is not None for name in ("crawlee", "impit", "playwright")}
        result["adaptive_dependencies"]["AdaptivePlaywrightCrawler"] = result["adaptive_crawler_available"]
    return result


def inspect_environment(adaptive=False):
    """Keep the existing PR's public inspection function and fields."""
    return check_environment(adaptive=adaptive)


def urlsplit_netloc(url):
    from urllib.parse import urlsplit
    return urlsplit(url).netloc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adaptive", action="store_true")
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--agent-backend", choices=("manual", "native", "codex", "responses"), default="manual")
    parser.add_argument("--url", default="https://www.aozora.gr.jp/robots.txt")
    parser.add_argument("--probe-proxy", action="store_true")
    args = parser.parse_args(argv)
    result = check_environment(args.adaptive, args.http, args.agent_backend, args.url, args.probe_proxy)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
