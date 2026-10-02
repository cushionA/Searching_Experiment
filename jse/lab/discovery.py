"""Topic-first entry points; every request still goes through Fetcher."""
import ipaddress
import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from .state import LabError, canonical, origin


PROVIDERS = {
    "duckduckgo": ("https://html.duckduckgo.com/html/", "q"),
    "mojeek": ("https://www.mojeek.com/search", "q"),
    "crossref": ("https://api.crossref.org/works", "query"),
    "mwmbl": ("https://mwmbl.org/search", "q"),
    "wiby": ("https://wiby.me/", "q"),
}


def query_text(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 300 or any(ord(c) < 32 for c in value):
        raise LabError("検索語は制御文字のない1〜300文字にしてください")
    return value.strip()


def search_urls(query, providers):
    query = query_text(query)
    result = []
    for provider in providers:
        base, parameter = PROVIDERS[provider]
        params = {parameter: query}
        if provider == "crossref":
            params["rows"] = "5"
        result.append(base + "?" + urlencode(params))
    return result


def topic_config(topic, providers, queries=None, max_origins=8):
    queries = queries or [topic]
    if not providers or any(p not in PROVIDERS for p in providers) or len(set(providers)) != len(providers):
        raise LabError("providerは" + "、".join(PROVIDERS) + "から重複なしで選んでください")
    queries = [query_text(q) for q in queries]
    return {
        "objective": topic,
        "seeds": [url for query in queries for url in search_urls(query, providers)],
        "allowed_origins": list(dict.fromkeys(origin(PROVIDERS[p][0]) for p in providers)),
        "transport": "live",
        "user_agent": "DiscoveryLab/0.1 (+https://github.com/cushionA/Searching_Experiment)",
        "delay_seconds": 2, "timeout_seconds": 15,
        "limits": {"pages_per_arm": 12, "attempts_per_arm": 24, "requests_per_arm": 40,
                   "bytes_per_arm": 8000000, "bytes_per_response": 500000,
                   "max_depth": 4, "agent_requests": 10, "frontier_size": 1000,
                   "candidates_per_request": 60},
        "discovery": {"scope": "public_web", "providers": providers, "queries": queries,
                      "max_queries_per_arm": 6, "max_origins_per_arm": max_origins},
    }


def validate_discovery(config):
    d = config["discovery"]
    if not isinstance(d, dict) or set(d) != {"scope", "providers", "queries", "max_queries_per_arm", "max_origins_per_arm"}:
        raise LabError("discoveryのキーが一致しません")
    if d["scope"] != "public_web" or config["transport"] not in ("live", "impit"):
        raise LabError("public_web探索はliveまたはimpitで使用してください")
    providers = d["providers"]
    if not isinstance(providers, list) or not providers or any(p not in PROVIDERS for p in providers) or len(set(providers)) != len(providers):
        raise LabError("未対応または重複した検索providerです")
    if not isinstance(d["queries"], list) or not 1 <= len(d["queries"]) <= 3:
        raise LabError("初期検索語は1〜3件です")
    d["queries"] = [query_text(q) for q in d["queries"]]
    if len(set(d["queries"])) != len(d["queries"]):
        raise LabError("初期検索語が重複しています")
    for key, low, high in (("max_queries_per_arm", len(d["queries"]), 20),
                           ("max_origins_per_arm", len(providers), 20)):
        if type(d[key]) is not int or not low <= d[key] <= high:
            raise LabError(f"{key}は整数{low}〜{high}です")
    expected = [u for q in d["queries"] for u in search_urls(q, providers)]
    if config["seeds"] != expected or set(config["allowed_origins"]) != {origin(PROVIDERS[p][0]) for p in providers}:
        raise LabError("テーマ探索のseed・originは検索語とproviderから生成してください")


def in_scope(config, url):
    url = canonical(url)
    if "discovery" not in config:
        return origin(url) in config["allowed_origins"]
    host = urlsplit(url).hostname
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True  # DNS/public-IP enforcement remains in the transport/proxy.


def unwrap_search_link(url):
    parts = urlsplit(url)
    if parts.hostname in ("duckduckgo.com", "html.duckduckgo.com") and parts.path in ("/l/", "/l"):
        target = parse_qs(parts.query).get("uddg", [])
        if target:
            return canonical(target[0])
    return canonical(url)


def parse_crossref(body, url):
    """Keep metadata explicitly labelled; this is not full-text evidence."""
    try:
        data = json.loads(body)
        items = data["message"]["items"]
        if not isinstance(items, list):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise LabError("invalid_crossref_response") from None
    text, links = ["Crossref search results (bibliographic metadata; not full text)"], []
    for item in items:
        title = " ".join(item.get("title", []))
        doi = item.get("DOI", "")
        text.append(title)
        if doi:
            text.append("DOI: " + doi)
        date = item.get("published", {}).get("date-parts", [])
        if date:
            text.append("Published: " + "-".join(str(p) for p in date[0]))
        urls = [item.get("URL", "")] + [link.get("URL", "") for link in item.get("link", [])]
        for target in urls:
            try:
                target = canonical(target)
            except LabError:
                continue
            text.append(target)
            links.append({"url": target, "anchor": title[:300]})
        if item.get("abstract"):
            text.append("Abstract (metadata): " + re.sub(r"<[^>]*>", " ", item["abstract"]))
    return "Crossref search", "\n".join(text), links
