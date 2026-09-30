"""Safe, per-URL network diagnostics; never change the environment's proxy policy."""
import os
import re
import urllib.request
from urllib.parse import unquote, urlsplit, urlunsplit


def environment_proxy(url, proxies=None):
    proxies = urllib.request.getproxies() if proxies is None else proxies
    parts = urlsplit(url)
    if urllib.request.proxy_bypass_environment(parts.netloc, proxies):
        return None
    return proxies.get(parts.scheme)


def proxy_metadata(proxy):
    if not proxy:
        return {}
    try:
        parts = urlsplit(proxy)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError()
        return {"proxy_scheme": parts.scheme, "proxy_host": parts.hostname,
                "proxy_port": port, "proxy_authenticated": bool(parts.username or parts.password)}
    except ValueError:
        return {"proxy_configuration_invalid": True}


def sanitize_detail(value, proxies=(), secrets=(), limit=1500):
    text = str(value)
    private = set(secrets)
    for proxy in proxies:
        if not proxy:
            continue
        try:
            parts = urlsplit(proxy)
            private.update(v for v in (parts.username, parts.password) if v)
            if parts.username or parts.password:
                text = text.replace(proxy, urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], "", "", "")))
        except ValueError:
            text = text.replace(proxy, "[redacted-proxy]")
    for key, value in os.environ.items():
        if value and any(word in key.upper() for word in ("PASSWORD", "TOKEN", "SECRET", "API_KEY")):
            private.add(value)
    private.update(unquote(v) for v in tuple(private) if v)
    for value in sorted((v for v in private if v), key=len, reverse=True):
        text = text.replace(value, "[redacted]")
    text = re.sub(r"(?i)(https?://)[^\s/@]+(?::[^\s/@]*)?@", r"\1[redacted]@", text)
    text = re.sub(r"(?i)((?:proxy-)?authorization\s*[:=]\s*)(?:basic|bearer)\s+[^\s,;]+", r"\1[redacted]", text)
    text = re.sub(r"(?i)(https?://[^\s?#]+)\?[^\s]+", r"\1?[redacted-query]", text)
    text = re.sub(r"(?i)\b(password|token|api[_-]?key|secret)\s*[:=]\s*[^\s,;]+", r"\1=[redacted]", text)
    return " ".join(text.split())[:limit]


def error_diagnostics(error, proxy=None, secrets=()):
    detail = sanitize_detail(error, (proxy,), secrets)
    stage = "unknown"
    lowered = detail.lower()
    # These are hints from an exception, not evidence of an origin-side rejection.
    if isinstance(error, PermissionError):
        stage = "network_permission"
    elif "407" in lowered or "proxy authentication" in lowered:
        stage = "proxy_authentication"
    elif "tunnel connection failed" in lowered or "connect refused" in lowered:
        stage = "proxy_connect"
    elif "certificate" in lowered or "tls" in lowered or "ssl" in lowered:
        stage = "tls"
    elif "name resolution" in lowered or "getaddrinfo" in lowered or "dns" in lowered:
        stage = "dns"
    elif "connection refused" in lowered or "couldn't connect" in lowered:
        stage = "proxy_connection" if proxy else "connection"
    elif isinstance(error, TimeoutError) or "timeout" in lowered or "timed out" in lowered:
        stage = "timeout"
    return {"error_type": type(error).__name__, "error_detail": detail,
            "failure_stage_hint": stage, **proxy_metadata(proxy)}
