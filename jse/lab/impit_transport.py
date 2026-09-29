import ipaddress
import select
import socket
import threading
import time
import urllib.request
from contextlib import nullcontext
from urllib.parse import urlsplit

from .fetch import public_addresses
from .state import LabError, canonical


class PinnedTunnel:
    def __init__(self, host, address, deadline):
        self.host = host
        self.address = address
        self.deadline = deadline
        self.stop = threading.Event()
        self.sockets = []
        self.error = None

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        return remaining

    def __enter__(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(0.1)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.listener.getsockname()[1]}"

    def serve(self):
        try:
            while not self.stop.is_set():
                self.remaining()
                try:
                    peer, _ = self.listener.accept()
                    break
                except socket.timeout:
                    continue
            else:
                return
            with peer:
                self.sockets.append(peer)
                peer.settimeout(self.remaining())
                header = bytearray()
                while b"\r\n\r\n" not in header and len(header) < 8192:
                    peer.settimeout(self.remaining())
                    part = peer.recv(1)
                    if not part:
                        return
                    header.extend(part)
                expected = f"CONNECT {self.host}:443 HTTP/1.1".encode()
                if not header.endswith(b"\r\n\r\n") or header.split(b"\r\n", 1)[0] != expected:
                    self.error = "impit_tunnel_target_mismatch"
                    peer.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                    return
                family, kind, protocol, _, address = self.address
                with socket.socket(family, kind, protocol) as upstream:
                    self.sockets.append(upstream)
                    upstream.settimeout(self.remaining())
                    upstream.connect(address)
                    peer.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    while not self.stop.is_set():
                        readable, _, _ = select.select([peer, upstream], [], [], min(0.1, self.remaining()))
                        for source in readable:
                            source.settimeout(self.remaining())
                            block = source.recv(16384)
                            if not block:
                                return
                            target = upstream if source is peer else peer
                            target.settimeout(self.remaining())
                            target.sendall(block)
        except OSError as error:
            if not self.stop.is_set():
                self.error = type(error).__name__

    def __exit__(self, *args):
        self.stop.set()
        self.listener.close()
        for connection in self.sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self.thread.join()


class ImpitTransport:
    client_name = "impit"

    def __init__(self):
        self.proxy_url = urllib.request.getproxies().get("https")
        self.proxy = bool(self.proxy_url)
        self.last_http_version = None

    def get(self, url, cap, timeout, user_agent):
        try:
            from impit import Client, HTTPError, InvalidURL, StreamError
        except ImportError as error:
            raise LabError("impitにはrequirements/crawl-tools.txtの導入が必要です") from error
        url = canonical(url)
        host = urlsplit(url).hostname
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None and not literal.is_global:
            raise LabError("private_address")
        deadline = time.monotonic() + timeout
        # Impit has no DNS pinning API; CONNECT preserves the origin's TLS/SNI and certificate checks.
        tunnel = nullcontext(self.proxy_url) if self.proxy else PinnedTunnel(host, public_addresses(host)[0], deadline)
        try:
            with tunnel as proxy:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LabError("body_deadline")
                with Client(browser="chrome", http3=False, proxy=proxy, timeout=remaining, verify=True, follow_redirects=False) as client:
                    headers = {"User-Agent": user_agent, "Accept-Encoding": "identity"}
                    with client.stream("GET", url, headers=headers, timeout=remaining) as response:
                        self.last_http_version = response.http_version
                        headers = dict(response.headers)
                        if headers.get("content-encoding", "identity").lower() != "identity":
                            raise LabError("compressed_response_not_supported")
                        body = bytearray()
                        for block in response.iter_bytes():
                            if time.monotonic() >= deadline:
                                raise LabError("body_deadline")
                            body.extend(block[:cap - len(body)])
                            if len(body) >= cap:
                                break
                        return response.status_code, headers, bytes(body), len(body) >= cap
        except (HTTPError, InvalidURL, StreamError) as error:
            reason = tunnel.error if isinstance(tunnel, PinnedTunnel) and tunnel.error else type(error).__name__
            raise LabError(reason) from None
