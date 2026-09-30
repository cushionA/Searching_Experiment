"""Playwright Chromiumを外部通信なしで起動確認する。"""
import json

from playwright.sync_api import sync_playwright


def check_browser(playwright_factory=sync_playwright):
    with playwright_factory() as driver:
        browser = driver.chromium.launch(headless=True, chromium_sandbox=False)
        try:
            context = browser.new_context(offline=True, service_workers="block")
            page = context.new_page()
            page.set_content("<title>playwright fixture</title><p id='result'></p><script>document.getElementById('result').textContent='JS ready'</script>")
            if page.locator("#result").inner_text() != "JS ready":
                raise RuntimeError("Playwright ChromiumのJavaScript確認に失敗しました")
            return {"browser_launch": "passed", "javascript": "passed", "browser_version": browser.version,
                    "scope": "offline local fixture; no external requests"}
        finally:
            browser.close()


if __name__ == "__main__":
    print(json.dumps(check_browser(), ensure_ascii=False))
