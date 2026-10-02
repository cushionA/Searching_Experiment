import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from email.message import Message
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from .state import LabError, atomic, canonical, origin
from .network import environment_proxy, error_diagnostics, proxy_metadata, sanitize_detail
from .discovery import in_scope, parse_crossref, unwrap_search_link


FIXTURES = {
    "/robots.txt": "User-agent: *\nDisallow: /private\n",
    "/": '<title>架空大学</title><a href="/news">お知らせ</a><a href="/access">交通案内</a><a href="/research">研究者一覧</a><a href="/private">非公開</a><a href="https://outside.example/">外部</a>',
    "/news": '<title>お知らせ</title>学園祭を開催します。<a href="/old">過去のお知らせ</a>',
    "/access": '<title>交通案内</title>駅から徒歩10分。',
    "/old": '<title>過去のお知らせ</title>過去のイベント。',
    "/research": '<title>研究者一覧</title><a href="/people/a">青木 花子 教員プロフィール</a><a href="/people/b">林 太郎 教員プロフィール</a>',
    "/people/a": '<title>青木 花子</title>青木 花子 教授。専門は情報検索。',
    "/people/b": '<title>林 太郎</title>林 太郎 准教授。専門は自然言語処理。',
}


def public_addresses(host):
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise LabError("private_address")
    return addresses


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        address = public_addresses(self.host)[0]
        sock = socket.socket(address[0], address[1], address[2])
        try:
            sock.settimeout(self.timeout)
            sock.connect(address[4])
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(PublicHTTPSConnection, request, context=ssl.create_default_context())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        return None


class LiveTransport:
    client_name = "urllib"
    def __init__(self):
        self.proxies = urllib.request.getproxies()
        self.proxy = bool(self.proxies.get("https"))
        handlers = [NoRedirect()]
        if self.proxy:
            handlers.append(urllib.request.ProxyHandler({"https": self.proxies["https"]}))
        else:
            handlers.extend([urllib.request.ProxyHandler({}), PublicHTTPSHandler()])
        self.opener = urllib.request.build_opener(*handlers)
        self.direct_opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}), PublicHTTPSHandler()) if self.proxy else self.opener

    def route_metadata(self, url):
        proxy = environment_proxy(url, self.proxies)
        return {"network_route": "environment_proxy" if proxy else "direct_public_ip", **proxy_metadata(proxy)}

    def get(self, url, cap, timeout, user_agent):
        self.proxy_url = environment_proxy(url, self.proxies)
        self.proxy = bool(self.proxy_url)
        opener = self.opener if self.proxy else self.direct_opener
        host = urlsplit(url).hostname
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise LabError("private_address")
        accept = "application/json" if origin(url) == "https://api.crossref.org" and urlsplit(url).path == "/works" else "text/html,text/plain;q=0.8"
        request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": accept, "Accept-Encoding": "identity"})
        deadline = time.monotonic() + timeout
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            headers = {key.lower(): value for key, value in response.headers.items()}
            if headers.get("content-encoding", "identity").lower() != "identity":
                raise LabError("compressed_response_not_supported")
            body = bytearray()
            while len(body) < cap:
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    raise LabError("body_deadline")
                raw_response = response.fp if isinstance(response, urllib.error.HTTPError) else response
                if raw_response.fp is None:
                    break
                raw_response.fp.raw._sock.settimeout(remaining_time)
                chunk = response.read1(min(16384, cap - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            return response.status, headers, bytes(body), len(body) == cap


class FixtureTransport:
    proxy = False
    client_name = "fixture"

    def get(self, url, cap, timeout, user_agent):
        if origin(url) != "https://lab.example":
            raise LabError("fixture_origin")
        path = urlsplit(url).path
        body = FIXTURES.get(path, "not found").encode("utf-8")
        return (200 if path in FIXTURES else 404), {"content-type": "text/plain; charset=utf-8" if path == "/robots.txt" else "text/html; charset=utf-8"}, body[:cap], len(body) >= cap


class HTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.title = []
        self.links = []
        self.skip = 0
        self.in_title = False
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag == "title":
            self.in_title = True
        if tag == "a":
            self.anchor = [dict(attrs).get("href", ""), []]
        if tag in ("p", "div", "br", "li", "h1", "h2", "tr"):
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        if tag == "title":
            self.in_title = False
        if tag == "a" and self.anchor:
            self.links.append({"href": self.anchor[0], "anchor": "".join(self.anchor[1])[:300]})
            self.anchor = None
        if tag in ("p", "div", "li", "h1", "h2", "tr"):
            self.text.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)
            if self.in_title:
                self.title.append(data)
            if self.anchor:
                self.anchor[1].append(data)


def parse_page(body, headers, url, discovery=False):
    content_type = headers.get("content-type", "")
    if discovery and origin(url) == "https://api.crossref.org" and urlsplit(url).path == "/works" and "application/json" in content_type.lower():
        return parse_crossref(body, url)
    if not any(kind in content_type.lower() for kind in ("text/html", "application/xhtml+xml")):
        raise LabError("not_html")
    message = Message()
    message["content-type"] = content_type
    meta = re.search(br'<meta\b[^>]*charset\s*=\s*["\x27\s]*([a-zA-Z0-9_-]+)', body[:4096], re.IGNORECASE)
    charset = message.get_content_charset() or (meta.group(1).decode("ascii") if meta else "utf-8")
    try:
        html = body.decode(charset, errors="replace")
    except LookupError:
        raise LabError("unknown_charset") from None
    parser = HTML()
    parser.feed(html)
    text = "\n".join(" ".join(line.split()) for line in "".join(parser.text).splitlines() if line.strip())
    links = []
    for link in parser.links:
        try:
            target = urljoin(url, link["href"])
            links.append({"url": unwrap_search_link(target) if discovery else canonical(target), "anchor": link["anchor"]})
        except LabError:
            continue
    return "".join(parser.title).strip(), text, links


class Fetcher:
    def __init__(self, store, transport=None):
        self.store = store
        self.config = store.state["config"]
        self.limits = self.config["limits"]
        if transport is not None:
            self.transport = transport
        elif self.config["transport"] in ("impit", "adaptive"):
            from .impit_transport import ImpitTransport
            self.transport = ImpitTransport()
        else:
            self.transport = FixtureTransport() if self.config["transport"] == "fixture" else LiveTransport()

    def http(self, name, url, kind, delay=None, deadline=None):
        url = canonical(url)
        if not in_scope(self.config, url):
            raise LabError("outside_scope")
        arm = self.store.state["arms"][name]
        if "discovery" in self.config:
            hosts = {origin(r["url"]) for r in arm["http"]}
            if origin(url) not in hosts and len(hosts) >= self.config["discovery"]["max_origins_per_arm"]:
                raise LabError("origin_budget")
        if len(arm["http"]) >= self.limits["requests_per_arm"]:
            raise LabError("request_budget")
        remaining = self.limits["bytes_per_arm"] - arm["bytes_charged"]
        if remaining <= 0:
            raise LabError("byte_budget")
        cap = min(remaining, self.limits["bytes_per_response"])
        delay = max(self.config["delay_seconds"], delay or 0)
        host = origin(url)
        wait = self.store.state["last_request_at"].get(host, 0) + delay - time.time()
        if deadline is not None and max(0, wait) >= deadline - time.monotonic():
            raise LabError("adaptive_deadline")
        if wait > 0 and not isinstance(self.transport, FixtureTransport):
            time.sleep(wait)
        timeout = self.config["timeout_seconds"] if deadline is None else min(self.config["timeout_seconds"], deadline - time.monotonic())
        if timeout <= 0:
            raise LabError("adaptive_deadline")
        route = "fixture" if isinstance(self.transport, FixtureTransport) else ("environment_proxy" if self.transport.proxy else "direct_public_ip")
        if self.transport.client_name == "impit" and not self.transport.proxy:
            route = "direct_public_ip_tunnel"
        record = {"url": url, "kind": kind, "time": time.time(), "status": "interrupted", "bytes_charged": cap, "network_route": route}
        if hasattr(self.transport, "route_metadata"):
            record.update(self.transport.route_metadata(url))
        record["http_client"] = self.transport.client_name
        arm["http"].append(record)
        arm["bytes_charged"] += cap
        self.store.state["last_request_at"][host] = time.time()
        self.store.state["inflight"] = {"kind": "http", "arm": name, "index": len(arm["http"]) - 1}
        self.store.save()
        try:
            status, headers, body, truncated = self.transport.get(url, cap, timeout, self.config["user_agent"])
            sha = hashlib.sha256(body).hexdigest()
            atomic(self.store.artifact("blobs/" + sha), body)
            arm["bytes_charged"] -= cap - len(body)
            retained_headers = ("content-type", "location", "content-security-policy", "content-security-policy-report-only", "access-control-allow-origin", "access-control-allow-credentials", "access-control-expose-headers", "cross-origin-resource-policy", "cross-origin-embedder-policy", "cross-origin-opener-policy", "x-content-type-options", "referrer-policy")
            record.update(status=status, bytes_charged=len(body), body_sha256=sha, headers={key: headers[key] for key in retained_headers if key in headers}, truncated=truncated)
            if self.transport.client_name == "impit":
                record["http_version"] = self.transport.last_http_version
        except (OSError, ValueError, http.client.HTTPException, LabError) as error:
            record["status"] = "error"
            record["error"] = sanitize_detail(error) if isinstance(error, LabError) else type(error).__name__
            if isinstance(error, urllib.error.URLError):
                if isinstance(error.reason, TimeoutError):
                    record["error"] = "TimeoutError"
                elif record["network_route"] == "environment_proxy":
                    record["error"] = "ProxyError"
            record.update(getattr(error, "diagnostics", error_diagnostics(error, getattr(self.transport, "proxy_url", None))))
            failure = LabError(record["error"])
            failure.diagnostics = {key: record[key] for key in ("error_type", "error_detail", "failure_stage_hint")}
            raise failure from None
        finally:
            self.store.state["inflight"] = None
            self.store.state["last_request_at"][host] = time.time()
            self.store.save()
        return record, body

    def robots(self, name, url, deadline=None):
        arm = self.store.state["arms"][name]
        host = origin(url)
        if host not in arm["robots"]:
            record, body = self.http(name, host + "/robots.txt", "robots", deadline=deadline)
            if record["status"] in (404, 410):
                rules = "User-agent: *\nAllow: /"
            elif record["status"] == 200 and not record["truncated"]:
                rules = body.decode("utf-8", errors="replace")
            else:
                raise LabError("robots_unavailable")
            arm["robots"][host] = rules
            self.store.save()
        parser = RobotFileParser()
        parser.parse(arm["robots"][host].splitlines())
        if not parser.can_fetch(self.config["user_agent"], url):
            raise LabError("robots_denied")
        delay = parser.crawl_delay(self.config["user_agent"]) or 0
        rate = parser.request_rate(self.config["user_agent"])
        if rate:
            delay = max(delay, rate.seconds / rate.requests)
        if delay > 60:
            raise LabError("robots_delay_requires_review")
        return delay

    def page(self, name, candidate):
        arm = self.store.state["arms"][name]
        attempt = dict(candidate, status="interrupted", number=len(arm["attempts"]) + 1)
        arm["attempts"].append(attempt)
        self.store.save()
        url = candidate["url"]
        try:
            for hop in range(4):
                if not in_scope(self.config, url):
                    raise LabError("redirect_outside_scope")
                extra = {}
                if self.config["transport"] == "adaptive":
                    from .adaptive import acquire
                    record, body, url, extra = acquire(self, name, url)
                else:
                    delay = self.robots(name, url)
                    record, body = self.http(name, url, "page", delay)
                if record["status"] in (301, 302, 303, 307, 308):
                    location = record["headers"].get("location")
                    if not location:
                        raise LabError("redirect_without_location")
                    url = canonical(urljoin(url, location))
                    continue
                if record["status"] != 200 or record["truncated"]:
                    raise LabError("truncated" if record["truncated"] else f"http_{record['status']}")
                headers = {"content-type": "text/html; charset=utf-8"} if extra.get("dom_sha256") else record["headers"]
                title, text, links = parse_page(body, headers, url, discovery="discovery" in self.config)
                text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                atomic(self.store.artifact("blobs/" + text_hash), text.encode("utf-8"))
                page = dict(candidate, url=url, requested_url=candidate["url"], title=title, text_sha256=text_hash, body_sha256=record["body_sha256"], attempt=attempt["number"], acquired_at=record["time"], links=links, **extra)
                arm["pages"].append(page)
                attempt.update(status="ok", final_url=url)
                for link in links:
                    if not in_scope(self.config, link["url"]) or link["url"] in arm["seen"]:
                        continue
                    if candidate["depth"] >= self.limits["max_depth"] or len(arm["seen"]) >= self.limits["frontier_size"]:
                        arm["dropped_links"] += 1
                        continue
                    arm["seen"].append(link["url"])
                    arm["frontier"].append(dict(link, parent=url, depth=candidate["depth"] + 1))
                self.store.save()
                return
            raise LabError("redirect_limit")
        except LabError as error:
            attempt.update(status="failed", error=str(error))
            attempt.update(getattr(error, "diagnostics", {}))
            self.store.save()
