import asyncio
import hashlib
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import urljoin

from .fetch import parse_page
from .state import LabError, atomic, canonical, origin


def acquire(fetcher, name, url):
    try:
        return asyncio.run(AdaptiveFetch(fetcher, name).run(url))
    except ImportError as error:
        raise LabError("adaptiveにはrequirements/adaptive-tools.txtとPlaywright Chromiumの導入が必要です") from error


class AdaptiveFetch:
    def __init__(self, fetcher, name):
        self.fetcher = fetcher
        self.store = fetcher.store
        self.name = name
        self.arm = self.store.state["arms"][name]
        self.config = fetcher.config
        self.error = None
        self.error_diagnostics = {}
        self.lock = asyncio.Lock()
        self.sources = {}
        self.initial = []
        self.deadline = None
        self.accepting = True

    def remaining(self):
        seconds = self.deadline - time.monotonic()
        if seconds <= 0:
            raise LabError("adaptive_deadline")
        return seconds

    async def blocking(self, call, *args):
        task = asyncio.create_task(asyncio.to_thread(call, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self.error = "adaptive_cancelled"
            try:
                await task
            finally:
                raise

    async def request(self, url, kind, method="GET"):
        async with self.lock:
            if not self.accepting:
                raise LabError("adaptive_page_closed")
            if self.error:
                raise LabError(self.error)
            try:
                self.remaining()
                if method != "GET":
                    raise LabError("adaptive_only_get")
                url = canonical(url)
                if origin(url) not in self.config["allowed_origins"]:
                    raise LabError("outside_scope")
                delay = await self.blocking(self.fetcher.robots, self.name, url, self.deadline)
                self.remaining()
                record, body = await self.blocking(self.fetcher.http, self.name, url, kind, delay, self.deadline)
                if record["truncated"]:
                    raise LabError("truncated")
                if record["status"] >= 400:
                    raise LabError(f"http_{record['status']}")
                return record, body, len(self.arm["http"]) - 1
            except LabError as error:
                self.error = str(error)
                self.error_diagnostics = getattr(error, "diagnostics", {})
                raise

    async def document(self, url):
        for _ in range(4):
            record, body, index = await self.request(url, "page")
            if record["status"] in (301, 302, 303, 307, 308):
                location = record["headers"].get("location")
                if not location:
                    raise LabError("redirect_without_location")
                url = canonical(urljoin(url, location))
                continue
            if record["status"] != 200:
                raise LabError(f"http_{record['status']}")
            parse_page(body, record["headers"], url)
            self.sources[("http", url)] = index
            return record, body, index, url
        raise LabError("redirect_limit")

    async def run(self, url):
        from crawlee import ConcurrencySettings, Request
        from crawlee.crawlers import AdaptivePlaywrightCrawler, RenderingTypePrediction
        from crawlee.crawlers._adaptive_playwright._rendering_type_predictor import DefaultRenderingTypePredictor
        from crawlee.crawlers._adaptive_playwright._adaptive_playwright_crawling_context import AdaptiveContextError
        from crawlee.http_clients import HttpClient, HttpCrawlingResult
        from crawlee.storage_clients import MemoryStorageClient

        owner = self
        history = self.arm.setdefault("adaptive_history", [])
        decision = {"url": url, "attempt": len(self.arm["attempts"])}
        self.arm.setdefault("adaptive_decisions", []).append(decision)
        self.store.save()
        self.deadline = time.monotonic() + self.config["timeout_seconds"] * 3

        class Predictor(DefaultRenderingTypePredictor):
            async def initialize(self):
                await super().initialize()
                for item in history:
                    super().store_result(Request.from_url(item["url"]), item["rendering_type"])

            def predict(self, request):
                prediction = super().predict(request)
                sample = int(hashlib.sha256(f"{request.url}:{decision['attempt']}".encode()).hexdigest()[:8], 16) / 2**32
                detect = sample < prediction.detection_probability_recommendation
                decision.update(predicted=prediction.rendering_type, compare=detect)
                owner.store.save()
                return RenderingTypePrediction(prediction.rendering_type, float(detect))

            def store_result(self, request, rendering_type):
                if owner.error:
                    return
                super().store_result(request, rendering_type)
                history.append({"url": request.url, "rendering_type": rendering_type})
                owner.store.save()

        class Response:
            http_version = "HTTP/1.1"

            def __init__(self, record, body):
                self.status_code = record["status"]
                self.headers = record["headers"]
                self.body = body

            async def read(self):
                return self.body

            async def read_stream(self):
                yield self.body

        class CountedClient(HttpClient):
            def __init__(self):
                super().__init__(persist_cookies_per_session=False)

            async def crawl(self, request, **kwargs):
                record, body, _, loaded_url = owner.initial.pop() if owner.initial else await owner.document(decision["url"])
                request.loaded_url = loaded_url
                return HttpCrawlingResult(http_response=Response(record, body))

            async def send_request(self, url, *, method="GET", **kwargs):
                record, body, _ = await owner.request(url, "resource", method)
                return Response(record, body)

            @asynccontextmanager
            async def stream(self, url, **kwargs):
                yield await self.send_request(url, **kwargs)

            async def cleanup(self):
                pass

        class PageStorage(MemoryStorageClient):
            def get_storage_client_cache_key(self, configuration):
                # Crawlee caches storage handles across instances; each page needs isolated buffers.
                return self

        def payload(result):
            calls = result.push_data_calls
            return calls[0]["data"] if len(calls) == 1 else None

        def valid(result):
            data = payload(result)
            return bool(data and (data["text"].strip() or data["links"]))

        def equivalent(first, second):
            if not valid(first) or not valid(second):
                return False
            left, right = payload(first), payload(second)
            return all(left[key] == right[key] for key in ("title", "text", "comparison_links"))

        crawler = AdaptivePlaywrightCrawler.with_beautifulsoup_static_parser(
            rendering_type_predictor=Predictor(),
            result_checker=valid,
            result_comparator=equivalent,
            http_client=CountedClient(),
            storage_client=PageStorage(),
            use_session_pool=False,
            max_request_retries=0,
            max_session_rotations=0,
            max_requests_per_crawl=1,
            concurrency_settings=ConcurrencySettings(min_concurrency=1, max_concurrency=1, desired_concurrency=1),
            request_handler_timeout=timedelta(seconds=self.config["timeout_seconds"] * 3),
            playwright_crawler_specific_kwargs={
                "headless": True,
                "fingerprint_generator": None,
                "use_incognito_pages": True,
                "browser_new_context_options": {"offline": True, "service_workers": "block", "accept_downloads": False, "user_agent": self.config["user_agent"]},
                "navigation_timeout": timedelta(seconds=self.config["timeout_seconds"]),
                "goto_options": {"wait_until": "domcontentloaded"},
            },
        )

        @crawler.pre_navigation_hook(playwright_only=True)
        async def intercept(context):
            async def route_request(route):
                request = route.request
                main = request.is_navigation_request() and request.frame == context.page.main_frame
                try:
                    if self.initial and main and canonical(request.url) == self.initial[0][3]:
                        record, body, index, _ = self.initial.pop()
                    else:
                        record, body, index = await self.request(request.url, "page" if main else "resource", request.method)
                    if record["status"] in (301, 302, 303, 307, 308):
                        self.error = "adaptive_resource_redirect_not_supported"
                        raise LabError(self.error)
                    if main and record["status"] == 200:
                        self.sources[("dom", canonical(request.url))] = index
                    await route.fulfill(status=record["status"], headers=record["headers"], body=body)
                except LabError:
                    await route.abort()

            async def block_socket(socket):
                self.error = "adaptive_websocket_not_supported"
                await socket.close()

            await context.page.context.route("**/*", route_request)
            await context.page.context.route_web_socket("**/*", block_socket)

        @crawler.router.default_handler
        async def extract(context):
            try:
                page = context.page
            except AdaptiveContextError:
                page = None
            kind = "dom" if page else "http"
            if page:
                await page.wait_for_load_state("networkidle", timeout=min(self.remaining(), self.config["timeout_seconds"]) * 1000)
                await page.wait_for_timeout(500)
                loaded_url = canonical(page.url)
                body = (await page.content()).encode("utf-8")
                headers = {"content-type": "text/html; charset=utf-8"}
            else:
                loaded_url = canonical(context.request.loaded_url or context.request.url)
                body = await context.http_response.read()
                headers = dict(context.http_response.headers)
            if self.error:
                raise LabError(self.error)
            if len(body) > self.fetcher.limits["bytes_per_response"]:
                self.error = "dom_size_budget"
                raise LabError("dom_size_budget")
            index = self.sources[(kind, loaded_url)]
            title, text, links = parse_page(body, headers, loaded_url)
            if page and not (text.strip() or links):
                raise LabError("adaptive_empty_result")
            data = {"url": loaded_url, "title": title, "text": text, "links": links, "comparison_links": sorted({link["url"] for link in links}), "source_kind": kind, "http_record_index": index}
            if page:
                sha = hashlib.sha256(body).hexdigest()
                atomic(self.store.artifact("blobs/" + sha), body)
                data["dom_sha256"] = sha
                self.arm.setdefault("renderings", []).append({"url": loaded_url, "dom_sha256": sha, "http_record_index": index, "attempt": decision["attempt"]})
                self.store.save()
            await context.push_data(data)

        @crawler.failed_request_handler
        async def failed(context, error):
            cause = getattr(error, "wrapped_exception", error)
            self.error = self.error or (str(cause) if isinstance(cause, LabError) else type(cause).__name__)

        try:
            # Playwright routing only intercepts the first redirect hop, so resolve before navigation.
            self.initial.append(await self.document(url))
            decision["resolved_url"] = self.initial[0][3]
            await crawler.run([decision["resolved_url"]])
            if self.error:
                raise LabError(self.error)
            items = (await crawler.get_data()).items
            if len(items) != 1:
                raise LabError("adaptive_no_result")
            selected = items[0]
            decision["selected"] = selected["source_kind"]
            decision["http_record_index"] = selected["http_record_index"]
            self.store.save()
            record = self.arm["http"][selected["http_record_index"]]
            sha = selected.get("dom_sha256", record["body_sha256"])
            body = self.store.artifact("blobs/" + sha).read_bytes()
            extra = {key: selected[key] for key in ("source_kind", "http_record_index")}
            if "dom_sha256" in selected:
                extra["dom_sha256"] = selected["dom_sha256"]
            return record, body, selected["url"], extra
        except Exception as error:
            decision["error"] = self.error or (str(error) if isinstance(error, LabError) else type(error).__name__)
            self.store.save()
            failure = LabError(decision["error"])
            failure.diagnostics = self.error_diagnostics or getattr(error, "diagnostics", {})
            raise failure from error
        finally:
            self.accepting = False
            await (await crawler.get_request_manager()).drop()
            await (await crawler.get_dataset()).drop()
            await (await crawler.get_key_value_store()).drop()
