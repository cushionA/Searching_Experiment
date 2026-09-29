import argparse
import asyncio
import importlib.metadata
import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FixtureHandler(BaseHTTPRequestHandler):
    hits = []

    def do_GET(self):
        self.hits.append(self.path)
        body = "<title>取得確認</title><p>local fixture</p>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


async def check_http(url):
    from crawlee import ConcurrencySettings
    from crawlee.crawlers import BeautifulSoupCrawler
    from crawlee.http_clients import ImpitHttpClient
    from crawlee.sessions import SessionPool
    from crawlee.storage_clients import MemoryStorageClient

    observed = []
    crawler = BeautifulSoupCrawler(
        http_client=ImpitHttpClient(follow_redirects=False, timeout=10),
        session_pool=SessionPool(max_pool_size=1),
        storage_client=MemoryStorageClient(),
        concurrency_settings=ConcurrencySettings(min_concurrency=1, max_concurrency=1, desired_concurrency=1),
        max_requests_per_crawl=1,
        max_request_retries=0,
        max_session_rotations=0,
        request_handler_timeout=timedelta(seconds=15),
    )

    @crawler.router.default_handler
    async def handle(context):
        observed.append({"title": context.soup.title.get_text(), "session": context.session is not None})

    await crawler.run([url])
    if observed != [{"title": "取得確認", "session": True}] or FixtureHandler.hits != ["/fixture"]:
        raise RuntimeError("Crawlee/Impit/SessionPool smoke check failed")
    return {"local_http": "passed", "session_pool": "passed"}


async def check_browser():
    from patchright.async_api import async_playwright

    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        try:
            context = await browser.new_context(offline=True)
            page = await context.new_page()
            await page.set_content("<title>browser fixture</title><p id='result'></p><script>document.getElementById('result').textContent = 'JS ready'</script>")
            if await page.locator("#result").inner_text() != "JS ready":
                raise RuntimeError("Patchright JavaScript smoke check failed")
            return {"browser_launch": "passed", "javascript": "passed", "browser_version": browser.version}
        finally:
            await browser.close()


async def run(with_browser):
    FixtureHandler.hits.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = await check_http(f"http://127.0.0.1:{server.server_port}/fixture")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    if with_browser:
        result.update(await check_browser())
    packages = ["crawlee", "impit"] + (["patchright"] if with_browser else [])
    result["versions"] = {name: importlib.metadata.version(name) for name in packages}
    result["scope"] = "local installation check; no external sites or blocking tests"
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="取得ツールをローカルfixtureだけで起動確認する")
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.browser))
