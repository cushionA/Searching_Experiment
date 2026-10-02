"""Local browser extraction benchmark: timing, process-tree RSS, HTTP bytes.

Optional Playwright/Patchright imports occur only in isolated worker processes.
The controller and fixture server require Python's standard library only.
"""
import argparse
import asyncio
import csv
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
import platform
import random
import select
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
ENGINES = ("lightpanda", "playwright", "patchright")
SCENARIOS = ("static", "dom", "fetch", "assets")
MODES = ("warm", "cold")
DISPLAY_MODES = ("headless", "headful")
EXTRACT = """() => {
  const rows = Array.from(document.querySelectorAll('#items li'));
  return {
    title: document.title,
    ready: document.getElementById('done')?.textContent,
    count: rows.length,
    checksum: rows.reduce((total, row) => total + Number(row.dataset.value), 0),
    first: rows[0]?.textContent,
    last: rows[rows.length - 1]?.textContent
  };
}"""


def emit(event, **values):
    print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)


def expected_result(scenario, items):
    return {"title": f"benchmark-{scenario}", "ready": "ready", "count": items,
            "checksum": items * (items - 1) // 2, "first": "Item 0", "last": f"Item {items - 1}"}


def fixture_body(scenario, prefix, items):
    rows = "".join(f'<li data-value="{i}">Item {i}</li>' for i in range(items))
    create_rows = """function populate(values) {
      const list = document.getElementById('items');
      for (const value of values) {
        const row = document.createElement('li');
        row.dataset.value = value; row.textContent = 'Item ' + value;
        list.appendChild(row);
      }
      document.getElementById('done').textContent = 'ready';
    }"""
    content = f'<ul id="items">{rows if scenario in {"static", "assets"} else ""}</ul>'
    content += f'<p id="done">{"ready" if scenario == "static" else ""}</p>'
    if scenario == "dom":
        content += f'<script>{create_rows} populate(Array.from({{length: {items}}}, (_, i) => i));</script>'
    elif scenario == "fetch":
        content += f'<script>{create_rows} fetch("{prefix}/api.json").then(r => r.json()).then(populate);</script>'
    elif scenario == "assets":
        content += (f'<link rel="stylesheet" href="{prefix}/style.css">'
                    f'<img src="{prefix}/image.svg" alt="fixture">'
                    f'<script src="{prefix}/script.js"></script>')
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>benchmark-{scenario}</title>'
            '<link rel="icon" href="data:,"></head>'
            f'<body>{content}</body></html>').encode()


class CountingIO:
    """Count actual plaintext HTTP bytes, including headers, at the server."""
    def __init__(self, stream):
        self.stream = stream
        self.bytes = 0

    def read(self, *args):
        value = self.stream.read(*args)
        self.bytes += len(value)
        return value

    def readline(self, *args):
        value = self.stream.readline(*args)
        self.bytes += len(value)
        return value

    def write(self, value):
        count = self.stream.write(value)
        self.bytes += len(value) if count is None else count
        return count

    def __getattr__(self, name):
        return getattr(self.stream, name)


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128

    def __init__(self, items, delay_ms):
        self.items = items
        self.delay_ms = delay_ms
        self.lock = threading.Lock()
        self.traffic = {}
        super().__init__(("127.0.0.1", 0), FixtureHandler)

    def begin_request(self, token, phase, kind, request_bytes):
        with self.lock:
            metrics = self.traffic.setdefault((token, phase), Counter())
            metrics.update(requests=1, upload_bytes=request_bytes, in_flight_requests=1)
            metrics[f"resource:{kind}"] += 1

    def record_response(self, token, phase, response_bytes, body_bytes):
        with self.lock:
            metrics = self.traffic[(token, phase)]
            metrics.update(download_bytes=response_bytes, response_body_bytes=body_bytes, in_flight_requests=-1)

    def snapshot(self, token, phase="measured"):
        with self.lock:
            metrics = self.traffic.get((token, phase), Counter()).copy()
        return {"requests": metrics["requests"], "upload_bytes": metrics["upload_bytes"],
                "download_bytes": metrics["download_bytes"], "response_body_bytes": metrics["response_body_bytes"],
                "in_flight_requests": metrics["in_flight_requests"],
                "resources": {key.removeprefix("resource:"): value for key, value in metrics.items()
                              if key.startswith("resource:")}}


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.rfile = CountingIO(self.rfile)
        self.wfile = CountingIO(self.wfile)

    def log_message(self, *_args):
        pass

    def do_GET(self):
        # All fixture resources keep the worker token and phase in their URL.
        parts = urlsplit(self.path).path.strip("/").split("/")
        if len(parts) < 4 or parts[0] != "run" or parts[2] not in {"warmup", "measured"}:
            self.send_error(404)
            return
        _, token, phase, kind, *_rest = parts
        self.server.begin_request(token, phase, kind, self.rfile.bytes)
        prefix = f"/run/{token}/{phase}"
        content_type, status = "text/html; charset=utf-8", 200
        if kind in SCENARIOS:
            body = fixture_body(kind, prefix, self.server.items)
        elif kind == "api.json":
            time.sleep(self.server.delay_ms / 1000)
            body = json.dumps(list(range(self.server.items))).encode()
            content_type = "application/json"
        elif kind == "script.js":
            body = b"document.getElementById('done').textContent = 'ready';"
            content_type = "application/javascript"
        elif kind == "style.css":
            body = b"li { color: #123456; }\n/*" + b"c" * 32768 + b"*/"
            content_type = "text/css"
        elif kind == "image.svg":
            body = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><desc>' + b"i" * 65536 + b'</desc><rect width="8" height="8"/></svg>'
            content_type = "image/svg+xml"
        else:
            body, status = b"Not found", 404
        sent_before = self.wfile.bytes
        body_bytes = 0
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            body_bytes = self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.server.record_response(token, phase, self.wfile.bytes - sent_before, body_bytes)


def process_table(proc_root=Path("/proc")):
    table = {}
    for directory in proc_root.iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            # comm may contain spaces/parentheses; fields after its last ')' are fixed.
            fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
            table[int(directory.name)] = {"ppid": int(fields[1]), "pgrp": int(fields[2]),
                                          "start": int(fields[19]), "rss": max(0, int(fields[21])),
                                          "state": fields[0]}
        except (FileNotFoundError, ProcessLookupError):
            continue
    return table


def process_tree_rss(root, tracked=None, proc_root=Path("/proc")):
    """Track descendants even when Chromium detaches or changes process group.

    Pair PID with kernel start time to avoid attributing a reused PID to a run.
    Previously observed descendants remain included if reparented to init.
    """
    table = process_table(proc_root)
    known = tracked if tracked is not None else {}
    selected = {pid for pid, entry in table.items()
                if pid == root or entry["pgrp"] == root or known.get(pid) == entry["start"]}
    while True:
        children = {pid for pid, entry in table.items() if entry["ppid"] in selected} - selected
        if not children:
            break
        selected.update(children)
    if tracked is not None:
        tracked.update({pid: table[pid]["start"] for pid in selected})
    live = [table[pid] for pid in selected if table[pid]["state"] != "Z"]
    return sum(entry["rss"] for entry in live) * os.sysconf("SC_PAGE_SIZE"), len(live)


class MemorySampler:
    def __init__(self, group, interval_ms):
        self.group = group
        self.interval = interval_ms / 1000
        self.phase = "startup"
        self.peaks = Counter()
        self.samples = Counter()
        self.max_processes = 0
        self.error = None
        self.tracked = {}
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.sample_loop, daemon=True)

    def sample_loop(self):
        while not self.stop_event.is_set():
            try:
                rss, processes = process_tree_rss(self.group, self.tracked)
                if processes:
                    phase = self.phase
                    self.peaks[phase] = max(self.peaks[phase], rss)
                    self.peaks["lifetime"] = max(self.peaks["lifetime"], rss)
                    self.samples[phase] += 1
                    self.max_processes = max(self.max_processes, processes)
            except (OSError, ValueError, IndexError) as error:
                self.error = f"{type(error).__name__}: {error}"
            self.stop_event.wait(self.interval)

    def start(self):
        self.thread.start()

    def finish(self):
        self.stop_event.set()
        self.thread.join()
        return {"peak_rss_mib": round(self.peaks["measured"] / 1048576, 3) if self.samples["measured"] else None,
                "lifetime_peak_rss_mib": round(self.peaks["lifetime"] / 1048576, 3) if self.samples else None,
                "phase_peak_rss_mib": {key: round(value / 1048576, 3) for key, value in self.peaks.items()},
                "samples": dict(self.samples), "max_processes": self.max_processes,
                "interval_ms": self.interval * 1000, "error": self.error}


@asynccontextmanager
async def display_session(config):
    """Use the caller's display or start a private Xvfb inside the worker group."""
    if config["display_mode"] == "headless":
        yield {"kind": "none", "startup_ms": 0}
        return
    if os.environ.get("DISPLAY"):
        yield {"kind": "existing", "value": os.environ["DISPLAY"], "startup_ms": 0}
        return
    executable = shutil.which("Xvfb")
    if not executable:
        raise RuntimeError("Headful requires DISPLAY or Xvfb. Install xvfb or use --display-modes headless")
    read_fd, write_fd = os.pipe()
    started = time.perf_counter()
    display = None
    try:
        display = subprocess.Popen([executable, "-displayfd", str(write_fd), "-screen", "0", "1280x720x24", "-nolisten", "tcp"],
                                   pass_fds=(write_fd,), stdout=sys.stderr, stderr=sys.stderr)
        os.close(write_fd)
        write_fd = None
        readable, _, _ = await asyncio.to_thread(select.select, [read_fd], [], [], 5)
        number = os.read(read_fd, 64).decode().strip() if readable else ""
        if not number.isdecimal() or display.poll() is not None:
            raise RuntimeError("Xvfb startup failed; see worker log")
        os.environ["DISPLAY"] = f":{number}"
        yield {"kind": "xvfb", "value": f":{number}", "screen": "1280x720x24",
               "startup_ms": (time.perf_counter() - started) * 1000}
    finally:
        os.environ.pop("DISPLAY", None)
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        if display and display.poll() is None:
            display.terminate()
            try:
                await asyncio.to_thread(display.wait, timeout=2)
            except subprocess.TimeoutExpired:
                display.kill()
                await asyncio.to_thread(display.wait)


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


@asynccontextmanager
async def open_browser(driver, config):
    panda = None
    browser = None
    started = time.perf_counter()
    try:
        if config["engine"] == "lightpanda":
            port = free_port()
            # Inherit the worker process group so memory/timeout cleanup includes it.
            panda = subprocess.Popen([config["lightpanda"], "serve", "--host", "127.0.0.1", "--port", str(port)],
                                     stdout=sys.stderr, stderr=sys.stderr)
            deadline = time.monotonic() + config["timeout_ms"] / 1000
            while time.monotonic() < deadline:
                if panda.poll() is not None:
                    raise RuntimeError(f"LightPanda exited with code {panda.returncode}")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    await asyncio.sleep(0.025)
            else:
                raise TimeoutError("LightPanda CDP server startup timed out")
            browser = await driver.chromium.connect_over_cdp(f"ws://127.0.0.1:{port}", timeout=config["timeout_ms"])
        else:
            options = {"headless": config["display_mode"] == "headless", "chromium_sandbox": False,
                       "timeout": config["timeout_ms"]}
            if config["chromium"]:
                options["executable_path"] = config["chromium"]
            browser = await driver.chromium.launch(**options)
        yield browser, (time.perf_counter() - started) * 1000
    finally:
        try:
            if browser:
                await browser.close()
        finally:
            if panda and panda.poll() is None:
                panda.terminate()
                try:
                    await asyncio.to_thread(panda.wait, timeout=2)
                except subprocess.TimeoutExpired:
                    panda.kill()
                    await asyncio.to_thread(panda.wait)


async def extract_page(browser, config, phase, index):
    context = await browser.new_context()
    try:
        page = await context.new_page()
        page.set_default_timeout(config["timeout_ms"])
        url = f'{config["base_url"]}/run/{config["token"]}/{phase}/{config["scenario"]}/{index}'
        response = await page.goto(url, wait_until="load", timeout=config["timeout_ms"])
        if response is None or response.status != 200:
            raise RuntimeError(f"Expected HTTP 200, got {response.status if response else None}")
        deadline = time.monotonic() + config["timeout_ms"] / 1000
        expected = expected_result(config["scenario"], config["items"])
        while True:
            result = await page.evaluate(EXTRACT)
            if result.get("ready") == "ready":
                if result != expected:
                    raise RuntimeError(f"Extraction mismatch: expected {expected}, got {result}")
                return result
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Fixture never became ready: {result}")
            await asyncio.sleep(0.01)
    finally:
        await context.close()


async def run_page(driver, browser, config, phase, index, semaphore):
    # Queue waiting is excluded from individual latency, included in batch time.
    async with semaphore:
        started = time.perf_counter()
        startup_ms = None
        try:
            async with asyncio.timeout(config["timeout_ms"] / 1000):
                if browser is None:
                    async with open_browser(driver, config) as (cold_browser, startup_ms):
                        extraction = await extract_page(cold_browser, config, phase, index)
                        version = cold_browser.version
                else:
                    extraction = await extract_page(browser, config, phase, index)
                    version = browser.version
            return {"index": index, "status": "ok", "latency_ms": (time.perf_counter() - started) * 1000,
                    "browser_startup_ms": startup_ms, "browser_version": version, "extraction": extraction}
        except Exception as error:
            return {"index": index, "status": "error", "latency_ms": (time.perf_counter() - started) * 1000,
                    "browser_startup_ms": startup_ms, "error": f"{type(error).__name__}: {error}"[:4000]}


async def worker_batches(config):
    started = time.perf_counter()
    if config["engine"] == "patchright":
        from patchright.async_api import async_playwright
    else:
        from playwright.async_api import async_playwright
    async with async_playwright() as driver:
        driver_startup_ms = (time.perf_counter() - started) * 1000

        async def batches(browser, browser_startup_ms=None):
            semaphore = asyncio.Semaphore(config["concurrency"])
            emit("phase", phase="warmup")
            warmups = await asyncio.gather(*(run_page(driver, browser, config, "warmup", i, semaphore)
                                           for i in range(config["warmup"])))
            if any(row["status"] != "ok" for row in warmups):
                raise RuntimeError(f"Warmup failed: {warmups}")
            emit("phase", phase="measured")
            start = time.perf_counter()
            pages = await asyncio.gather(*(run_page(driver, browser, config, "measured", i, semaphore)
                                         for i in range(config["pages"])))
            batch_ms = (time.perf_counter() - start) * 1000
            emit("phase", phase="cleanup")
            return {"status": "ok" if all(row["status"] == "ok" for row in pages) else "error",
                    "driver_startup_ms": driver_startup_ms, "browser_startup_ms": browser_startup_ms,
                    "batch_ms": batch_ms, "pages": pages}

        if config["mode"] == "warm":
            async with open_browser(driver, config) as (browser, startup_ms):
                result = await batches(browser, startup_ms)
        else:
            result = await batches(None)
    return result


async def worker(config):
    async with display_session(config) as display:
        result = await worker_batches(config)
        result["display"] = display
    return result


def stop_process_group(process, tracked=None):
    # Kill remaining Chromium renderers/LightPanda on timeout or worker failure.
    identities = dict(tracked or {})
    process_tree_rss(process.pid, identities)

    def signal_descendants(sig):
        table = process_table()
        for pid, start in identities.items():
            if table.get(pid, {}).get("start") == start and table[pid]["state"] != "Z":
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass

    signal_descendants(signal.SIGTERM)
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    process_tree_rss(process.pid, identities)
    signal_descendants(signal.SIGKILL)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_sample(config, output, server, sample_ms, cancelled=None):
    stem = f'{config["scenario"]}-{config["mode"]}-{config["display_mode"]}-c{config["concurrency"]}-i{config["iteration"]}-{config["engine"]}'
    log_path = output / "logs" / f"{stem}.log"
    result = None
    reader_errors = []
    with log_path.open("w") as log:
        process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--worker", json.dumps(config)],
                                   stdout=subprocess.PIPE, stderr=log, text=True, start_new_session=True)
        sampler = MemorySampler(process.pid, sample_ms)
        sampler.start()

        def read_events():
            nonlocal result
            for line in process.stdout:
                try:
                    event = json.loads(line)
                    if event.get("event") == "phase":
                        sampler.phase = event["phase"]
                    elif event.get("event") == "result":
                        result = event["result"]
                except (ValueError, KeyError, TypeError) as error:
                    reader_errors.append(str(error))

        reader = threading.Thread(target=read_events, daemon=True)
        reader.start()
        # Every job has a deadline, plus bounded driver/startup/cleanup overhead.
        batches = math.ceil(config["pages"] / config["concurrency"]) + math.ceil(config["warmup"] / config["concurrency"])
        budget = (batches + 2) * config["timeout_ms"] / 1000 + 10
        deadline = time.monotonic() + budget
        timed_out = False
        interrupted = False
        try:
            while process.poll() is None:
                if cancelled and cancelled.is_set():
                    interrupted = True
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                try:
                    process.wait(timeout=min(0.25, max(0.001, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    continue
        finally:
            stop_process_group(process, sampler.tracked)
            process.wait()
            reader.join(timeout=3)
            process.stdout.close()
            memory = sampler.finish()
    if timed_out or interrupted or result is None or reader_errors:
        result = {"status": "error", "pages": [], "batch_ms": None,
                  "error": "Worker timed out" if timed_out else "Worker interrupted" if interrupted else f"Worker failed (exit={process.returncode}); see {log_path.name}",
                  "worker_errors": reader_errors}
    elif process.returncode != 0:
        result["status"] = "error"
        result["worker_exit_code"] = process.returncode
    if memory["error"]:
        result["status"] = "error"
        result["memory_error"] = memory["error"]
    result.update({key: config[key] for key in ("engine", "scenario", "mode", "display_mode", "concurrency", "iteration", "token")})
    result.update(requested_pages=config["pages"], memory=memory,
                  traffic=server.snapshot(config["token"]),
                  warmup_traffic=server.snapshot(config["token"], "warmup"),
                  log=str(log_path.relative_to(output)))
    return result


def percentile(values, percent):
    """Nearest-rank percentile, including the slowest sample for small n."""
    return sorted(values)[max(0, math.ceil(len(values) * percent / 100) - 1)] if values else None


def median_or_none(values):
    return round(statistics.median(values), 3) if values else None


def summarize(samples):
    groups = {}
    for sample in samples:
        key = tuple(sample[key] for key in ("engine", "scenario", "mode", "display_mode", "concurrency"))
        groups.setdefault(key, []).append(sample)
    rows = []
    for (engine, scenario, mode, display_mode, concurrency), group in groups.items():
        # Exclude incomplete batches from headline speed comparisons, retain raw failures.
        complete = [sample for sample in group if sample["status"] == "ok"]
        pages = [page for sample in group for page in sample["pages"]]
        successful = [page for page in pages if page["status"] == "ok"]
        latencies = [page["latency_ms"] for sample in complete for page in sample["pages"]]
        startup = [sample["browser_startup_ms"] for sample in complete if sample.get("browser_startup_ms") is not None]
        if mode == "cold":
            startup = [page["browser_startup_ms"] for sample in complete for page in sample["pages"]
                       if page.get("browser_startup_ms") is not None]
        traffic = {key: sum(sample["traffic"][key] for sample in group)
                   for key in ("requests", "upload_bytes", "download_bytes", "response_body_bytes")}
        rows.append({"engine": engine, "scenario": scenario, "mode": mode, "display_mode": display_mode, "concurrency": concurrency,
                     "iterations": len(group), "complete_iterations": len(complete),
                     "status": "ok" if len(complete) == len(group) else "error",
                     "successful_pages": len(successful), "requested_pages": sum(s["requested_pages"] for s in group),
                     "median_latency_ms": median_or_none(latencies), "p95_latency_ms": percentile(latencies, 95),
                     "median_batch_ms": median_or_none([s["batch_ms"] for s in complete]),
                     "median_pages_per_second": median_or_none([len(s["pages"]) * 1000 / s["batch_ms"] for s in complete]),
                     "median_browser_startup_ms": median_or_none(startup),
                     "median_driver_startup_ms": median_or_none([s["driver_startup_ms"] for s in complete]),
                     "median_peak_rss_mib": median_or_none([s["memory"]["peak_rss_mib"] for s in group if s["memory"]["peak_rss_mib"] is not None]),
                     "max_lifetime_peak_rss_mib": max((s["memory"]["lifetime_peak_rss_mib"] for s in group if s["memory"]["lifetime_peak_rss_mib"] is not None), default=None),
                     "http_requests": traffic["requests"], "http_upload_bytes": traffic["upload_bytes"],
                     "http_download_bytes": traffic["download_bytes"], "http_response_body_bytes": traffic["response_body_bytes"],
                     "http_bytes_per_attempt": round((traffic["upload_bytes"] + traffic["download_bytes"]) / sum(s["requested_pages"] for s in group), 3)})
    return rows


def choices_csv(allowed):
    def parse(value):
        values = value.split(",")
        if not values or any(v not in allowed for v in values) or len(values) != len(set(values)):
            raise argparse.ArgumentTypeError(f"Choose comma-separated unique values from {','.join(allowed)}")
        return values
    return parse


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Must be positive")
    return number


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("Must be nonnegative")
    return number


def concurrencies(value):
    try:
        values = [positive(part) for part in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError("Expected positive concurrency values, e.g. 1,4") from error
    if len(set(values)) != len(values):
        raise argparse.ArgumentTypeError("Concurrency values must be unique")
    return values


def preflight(args):
    if sys.version_info < (3, 12) or sys.platform != "linux":
        raise ValueError("Python 3.12+ and Linux /proc are required for process-tree RSS measurement")
    runnable_headful = any(engine != "lightpanda" for engine in args.engines)
    if args.display_modes == ["headful"] and not runnable_headful:
        raise ValueError("LightPanda is headless-only; use --display-modes headless")
    if "headful" in args.display_modes and runnable_headful and not os.environ.get("DISPLAY") and not shutil.which("Xvfb"):
        raise ValueError("Headful requires DISPLAY or Xvfb. Install xvfb (e.g. sudo apt-get install xvfb) or pass --display-modes headless")
    versions = {}
    for engine in args.engines:
        package = "patchright" if engine == "patchright" else "playwright"
        if importlib.util.find_spec(package) is None:
            raise ValueError(f"Missing {package}. Run npm run benchmark:setup in a network-enabled environment")
        versions[package] = importlib.metadata.version(package)
    args.lightpanda = args.lightpanda or shutil.which("lightpanda")
    if not args.lightpanda and (ROOT / ".deps/lightpanda").is_file():
        args.lightpanda = str(ROOT / ".deps/lightpanda")
    if "lightpanda" in args.engines:
        if not args.lightpanda or not os.access(args.lightpanda, os.X_OK):
            raise ValueError("LightPanda missing. Run npm run benchmark:setup or pass --lightpanda /path/to/lightpanda")
        args.lightpanda = str(Path(args.lightpanda).resolve())
        binary = Path(args.lightpanda)
        with binary.open("rb") as source:
            versions["lightpanda_sha256"] = hashlib.file_digest(source, "sha256").hexdigest()
        try:
            version = subprocess.run([args.lightpanda, "--version"], capture_output=True, text=True, timeout=5)
            versions["lightpanda_version"] = (version.stdout or version.stderr).strip()[:500] if version.returncode == 0 else "unavailable"
        except (OSError, subprocess.TimeoutExpired):
            versions["lightpanda_version"] = "unavailable"
    if args.chromium:
        if not os.access(args.chromium, os.X_OK):
            raise ValueError(f"Chromium is not executable: {args.chromium}")
        args.chromium = str(Path(args.chromium).resolve())
    if (ROOT / ".deps/benchmark-browsers").is_dir() and "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".deps/benchmark-browsers")
    return versions


def parser():
    command = argparse.ArgumentParser(description="LightPanda / Playwright / Patchright: local speed, RSS, and HTTP traffic comparison")
    command.add_argument("--engines", type=choices_csv(ENGINES), default=list(ENGINES))
    command.add_argument("--scenarios", type=choices_csv(SCENARIOS), default=list(SCENARIOS))
    command.add_argument("--modes", type=choices_csv(MODES), default=list(MODES))
    command.add_argument("--display-modes", type=choices_csv(DISPLAY_MODES), default=list(DISPLAY_MODES))
    command.add_argument("--concurrency", type=concurrencies, default=[1, 4])
    command.add_argument("--schedule", choices=("serial", "parallel"), default="serial")
    command.add_argument("--pages", type=positive, default=6)
    command.add_argument("--iterations", type=positive, default=3)
    command.add_argument("--warmup", type=nonnegative, default=1)
    command.add_argument("--items", type=positive, default=500)
    command.add_argument("--delay-ms", type=nonnegative, default=40)
    command.add_argument("--timeout-ms", type=positive, default=15000)
    command.add_argument("--sample-ms", type=positive, default=25)
    command.add_argument("--seed", type=int, default=20261002)
    command.add_argument("--lightpanda", default=os.environ.get("LIGHTPANDA_BIN"))
    command.add_argument("--chromium", default=os.environ.get("BENCHMARK_CHROMIUM_BIN"))
    command.add_argument("--output", type=Path, help="New output directory; existing directories are never overwritten")
    return command


def write_report(output, metadata, samples):
    summary = summarize(samples)
    payload = {"schema_version": 1, "metadata": metadata, "summary": summary, "samples": samples}
    temporary = output / "results.json.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(output / "results.json")
    with (output / "summary.csv").open("w", newline="") as destination:
        if summary:
            writer = csv.DictWriter(destination, fieldnames=list(summary[0]))
            writer.writeheader()
            writer.writerows(summary)
    return summary


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        versions = preflight(args)
    except (ValueError, OSError, importlib.metadata.PackageNotFoundError) as error:
        print(f"Setup error: {error}", file=sys.stderr)
        return 2
    output = args.output or ROOT / ".lab-output" / f'browser-benchmark-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}'
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(f"Output already exists: {output}. Choose a new --output directory.", file=sys.stderr)
        return 2
    (output / "logs").mkdir()
    settings = vars(args).copy()
    settings["output"] = str(output)
    metadata = {"started_at": datetime.now(timezone.utc).isoformat(), "settings": settings,
                "platform": platform.platform(), "python": sys.version, "cpu_count": os.cpu_count(),
                "versions": versions,
                "unsupported_conditions": [{"engine": "lightpanda", "display_mode": "headful", "reason": "LightPanda is headless-only"}]
                    if "lightpanda" in args.engines and "headful" in args.display_modes else [],
                "scope": "Local HTTP fixtures only; identical verified DOM extraction; no external websites",
                "memory_method": "Sampled sum of Linux /proc/<pid>/stat RSS for the worker process tree, retaining observed descendants that detach or reparent and checking PID start time. Includes Python, automation driver, browser children and private Xvfb when used; shared pages may be counted multiple times; controller, fixture server and an existing shared DISPLAY excluded",
                "traffic_method": "Actual uncompressed plaintext HTTP/1.1 request/response bytes at the fixture server, including headers; excludes CDP, TCP/IP, TLS and background browser traffic",
                "timing_method": "Fresh context per page; warm reuses browser, cold includes browser start/close per page; warmup excluded; nearest-rank p95; parallel schedule shares host resources"}
    try:
        server = FixtureServer(args.items, args.delay_ms)
    except OSError as error:
        metadata["setup_error"] = str(error)
        write_report(output, metadata, [])
        print(f"Cannot start local fixture server: {error}", file=sys.stderr)
        return 2
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    randomizer = random.Random(args.seed)
    samples = []
    cancelled = threading.Event()
    try:
        for scenario in args.scenarios:
            for mode in args.modes:
                for concurrency in args.concurrency:
                    for display_mode in args.display_modes:
                        order = [engine for engine in args.engines if display_mode == "headless" or engine != "lightpanda"]
                        if not order:
                            continue
                        randomizer.shuffle(order)
                        for iteration in range(1, args.iterations + 1):
                            # Rotate ordering between repeats to reduce serial order bias.
                            engines = order[(iteration - 1) % len(order):] + order[:(iteration - 1) % len(order)]
                            configs = [{"engine": engine, "scenario": scenario, "mode": mode, "display_mode": display_mode,
                                        "concurrency": concurrency, "iteration": iteration, "token": uuid.uuid4().hex,
                                        "base_url": base_url, "pages": args.pages, "warmup": args.warmup,
                                        "items": args.items, "timeout_ms": args.timeout_ms,
                                        "lightpanda": args.lightpanda, "chromium": args.chromium} for engine in engines]
                            print(f"{scenario}/{mode}/{display_mode}/concurrency={concurrency} iteration={iteration} engines={','.join(engines)} ({args.schedule})", flush=True)
                            if args.schedule == "parallel":
                                with ThreadPoolExecutor(max_workers=len(configs)) as pool:
                                    try:
                                        batch = list(pool.map(lambda config: run_sample(config, output, server, args.sample_ms, cancelled), configs))
                                    except KeyboardInterrupt:
                                        cancelled.set()
                                        raise
                            else:
                                batch = [run_sample(config, output, server, args.sample_ms, cancelled) for config in configs]
                            samples.extend(batch)
                            write_report(output, metadata, samples)
                            for sample in batch:
                                passed = sum(p["status"] == "ok" for p in sample["pages"])
                                print(f'  {sample["engine"]}: {sample["status"]}, {passed}/{args.pages} pages, batch={sample.get("batch_ms")} ms, peak RSS={sample["memory"]["peak_rss_mib"]} MiB, HTTP={sample["traffic"]["upload_bytes"] + sample["traffic"]["download_bytes"]} bytes', flush=True)
    except KeyboardInterrupt:
        metadata["interrupted"] = True
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()
        metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_report(output, metadata, samples)
    print(f"Results: {output / 'results.json'}\nSummary: {output / 'summary.csv'}", flush=True)
    return 1 if metadata.get("interrupted") or any(s["status"] != "ok" for s in samples) else 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        try:
            emit("result", result=asyncio.run(worker(json.loads(sys.argv[2]))))
        except Exception as error:
            import traceback
            traceback.print_exc(file=sys.stderr)
            emit("result", result={"status": "error", "pages": [], "batch_ms": None,
                                   "error": f"{type(error).__name__}: {error}"[:4000]})
            sys.exit(1)
    else:
        sys.exit(main())
