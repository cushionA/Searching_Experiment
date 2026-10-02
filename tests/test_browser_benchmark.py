import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import browser_benchmark as benchmark


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.server = benchmark.FixtureServer(items=3, delay_ms=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path):
        raw = f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode()
        with socket.create_connection(self.server.server_address, timeout=2) as connection:
            connection.sendall(raw)
            response = b""
            while chunk := connection.recv(65536):
                response += chunk
        return raw, response

    def test_http_counts_match_raw_socket_bytes_and_exclude_warmup(self):
        warmup_request, warmup_response = self.request("/run/a/warmup/static/0")
        request, response = self.request("/run/a/measured/static/0")
        metrics = self.server.snapshot("a")
        self.assertEqual(metrics["requests"], 1)
        self.assertEqual(metrics["upload_bytes"], len(request))
        self.assertEqual(metrics["download_bytes"], len(response))
        self.assertEqual(metrics["response_body_bytes"], len(response.split(b"\r\n\r\n", 1)[1]))
        self.assertEqual(metrics["resources"], {"static": 1})
        self.assertEqual(self.server.snapshot("a", "warmup")["upload_bytes"], len(warmup_request))
        self.assertEqual(self.server.snapshot("a", "warmup")["download_bytes"], len(warmup_response))
        self.assertEqual(self.server.snapshot("other")["requests"], 0)

    def test_dynamic_and_asset_resources_are_counted_by_worker(self):
        for resource in ("fetch/0", "api.json", "assets/1", "script.js", "style.css", "image.svg"):
            _, response = self.request(f"/run/b/measured/{resource}")
            self.assertTrue(response.startswith(b"HTTP/1.1 200"))
            body = response.split(b"\r\n\r\n", 1)[1]
            if resource == "api.json":
                self.assertEqual(json.loads(body), [0, 1, 2])
            if resource == "assets/1":
                self.assertIn(b"/run/b/measured/image.svg", body)
                self.assertIn(b"/run/b/measured/script.js", body)
        metrics = self.server.snapshot("b")
        self.assertEqual(metrics["requests"], 6)
        self.assertEqual(metrics["resources"]["api.json"], 1)
        self.assertGreater(metrics["download_bytes"], 98000)

    def test_request_is_counted_before_a_slow_response_completes(self):
        self.server.delay_ms = 200
        raw = b"GET /run/slow/measured/api.json HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        with socket.create_connection(self.server.server_address, timeout=2) as connection:
            connection.sendall(raw)
            deadline = time.monotonic() + 1
            while self.server.snapshot("slow")["requests"] == 0 and time.monotonic() < deadline:
                time.sleep(0.005)
            metrics = self.server.snapshot("slow")
            self.assertEqual(metrics["requests"], 1)
            self.assertEqual(metrics["upload_bytes"], len(raw))
            self.assertEqual(metrics["in_flight_requests"], 1)
            self.assertEqual(metrics["download_bytes"], 0)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_lightpanda_concurrent_contexts_use_independent_connections(self):
        connections = []
        active = 0
        overlap = asyncio.Event()

        class Connection:
            closed = False
            context_closed = False

            async def new_context(self):
                connection = self

                class Context:
                    async def close(self):
                        connection.context_closed = True

                return Context()

            async def close(self):
                self.closed = True

        class Chromium:
            async def connect_over_cdp(self, endpoint, **kwargs):
                self.assert_endpoint = endpoint
                connection = Connection()
                connections.append(connection)
                return connection

        chromium = Chromium()
        browser = benchmark.LightpandaBrowser(SimpleNamespace(chromium=chromium), "ws://fixture", "1.0", 100)

        async def page():
            nonlocal active
            async with benchmark.fresh_context(browser):
                active += 1
                if active == 4:
                    overlap.set()
                await asyncio.wait_for(overlap.wait(), timeout=1)

        await asyncio.gather(*(page() for _ in range(4)))
        self.assertEqual(len(connections), 4)
        self.assertEqual(chromium.assert_endpoint, "ws://fixture")
        self.assertTrue(all(c.closed and c.context_closed for c in connections))

    async def test_lightpanda_context_creation_failure_disconnects(self):
        class Connection:
            closed = False

            async def new_context(self):
                raise RuntimeError("context rejected")

            async def close(self):
                self.closed = True

        connection = Connection()

        class Chromium:
            async def connect_over_cdp(self, *_args, **_kwargs):
                return connection

        browser = benchmark.LightpandaBrowser(SimpleNamespace(chromium=Chromium()), "ws://fixture", "1.0", 100)
        with self.assertRaisesRegex(RuntimeError, "context rejected"):
            async with benchmark.fresh_context(browser):
                self.fail("A failed context cannot be yielded")
        self.assertTrue(connection.closed)

    async def test_wrong_extraction_is_failure_and_closes_context(self):
        class Page:
            def set_default_timeout(self, _timeout):
                pass

            async def goto(self, _url, **_kwargs):
                return SimpleNamespace(status=200)

            async def evaluate(self, _script):
                return {"ready": "ready", "count": 0}

        class Context:
            closed = False

            async def new_page(self):
                return Page()

            async def close(self):
                self.closed = True

        context = Context()

        class Browser:
            async def new_context(self):
                return context

        config = {"base_url": "http://127.0.0.1:1234", "token": "a", "scenario": "static",
                  "items": 3, "timeout_ms": 100}
        with self.assertRaisesRegex(RuntimeError, "Extraction mismatch"):
            await benchmark.extract_page(Browser(), config, "measured", 0)
        self.assertTrue(context.closed)

    async def test_timeout_cancels_task_and_is_not_a_success(self):
        cancelled = False

        async def stalled(*_args):
            nonlocal cancelled
            try:
                await asyncio.sleep(5)
            finally:
                cancelled = True

        with patch.object(benchmark, "extract_page", side_effect=stalled):
            result = await benchmark.run_page(None, SimpleNamespace(version="test"),
                                              {"timeout_ms": 20}, "measured", 0, asyncio.Semaphore(1))
        self.assertEqual(result["status"], "error")
        self.assertIn("TimeoutError", result["error"])
        self.assertTrue(cancelled)

    async def test_headful_launch_passes_headless_false(self):
        observed = {}

        class Browser:
            async def close(self):
                observed["closed"] = True

        class Chromium:
            async def launch(self, **kwargs):
                observed.update(kwargs)
                return Browser()

        config = {"engine": "playwright", "display_mode": "headful", "chromium": "/fixture/chromium", "timeout_ms": 100}
        async with benchmark.open_browser(SimpleNamespace(chromium=Chromium()), config):
            pass
        self.assertFalse(observed["headless"])
        self.assertTrue(observed["closed"])


@unittest.skipUnless(sys.platform == "linux", "RSS requires Linux /proc")
class MemoryTests(unittest.TestCase):
    def test_peak_rss_includes_allocating_child_and_isolates_process_group(self):
        child_code = "import sys; data=bytearray(24*1024*1024); print('ready', flush=True); sys.stdin.read()"
        parent_code = ("import subprocess,sys; "
                       f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,start_new_session=True); "
                       "child.stdout.readline(); print('ready',flush=True); sys.stdin.read()")
        process = subprocess.Popen([sys.executable, "-c", parent_code], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, text=True, start_new_session=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            rss, count = benchmark.process_tree_rss(process.pid)
            self.assertGreater(rss, 24 * 1024 * 1024)
            self.assertGreaterEqual(count, 2)
            sampler = benchmark.MemorySampler(process.pid, 5)
            sampler.phase = "measured"
            sampler.start()
            time.sleep(0.03)
            memory = sampler.finish()
            self.assertGreater(memory["peak_rss_mib"], 24)
            self.assertGreaterEqual(memory["max_processes"], 2)
            self.assertIsNone(memory["error"])
        finally:
            benchmark.stop_process_group(process)
            process.wait()
            process.stdout.close()
            process.stdin.close()

    def test_proc_names_containing_spaces_and_parentheses(self):
        with tempfile.TemporaryDirectory() as directory:
            pid = Path(directory) / "123"
            pid.mkdir()
            fields = ["S", "1", "456"] + ["0"] * 18 + ["10"]
            (pid / "stat").write_text("123 (worker (test)) " + " ".join(fields))
            rss, count = benchmark.process_tree_rss(456, proc_root=Path(directory))
        self.assertEqual(count, 1)
        self.assertEqual(rss, 10 * os.sysconf("SC_PAGE_SIZE"))


class ReportingTests(unittest.TestCase):
    def sample(self, status="ok", display_mode="headless"):
        return {"engine": "playwright", "scenario": "static", "mode": "warm", "display_mode": display_mode,
                "concurrency": 1, "status": status, "requested_pages": 1, "batch_ms": 100 if status == "ok" else 1,
                "browser_startup_ms": 50, "driver_startup_ms": 20,
                "pages": [{"status": "ok" if status == "ok" else "error", "latency_ms": 100}],
                "memory": {"peak_rss_mib": 50, "lifetime_peak_rss_mib": 60},
                "traffic": {"requests": 1, "upload_bytes": 10, "download_bytes": 90, "response_body_bytes": 80}}

    def test_failed_batch_cannot_improve_headline_speed(self):
        row = benchmark.summarize([self.sample(), self.sample("error")])[0]
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["complete_iterations"], 1)
        self.assertEqual(row["successful_pages"], 1)
        self.assertEqual(row["median_batch_ms"], 100)
        self.assertEqual(row["median_pages_per_second"], 10)
        self.assertEqual(row["http_download_bytes"], 180)
        self.assertEqual(row["http_bytes_per_attempt"], 100)

    def test_headful_and_headless_are_separate_groups(self):
        rows = benchmark.summarize([self.sample(), self.sample(display_mode="headful")])
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["display_mode"] for row in rows}, {"headful", "headless"})

    def test_all_failed_batches_have_no_throughput(self):
        row = benchmark.summarize([self.sample("error")])[0]
        self.assertIsNone(row["median_pages_per_second"])
        self.assertIsNone(row["median_latency_ms"])

    def test_partial_batch_successes_do_not_bias_latency(self):
        partial = self.sample("error")
        partial["pages"][0].update(status="ok", latency_ms=1)
        row = benchmark.summarize([self.sample(), partial])[0]
        self.assertEqual(row["successful_pages"], 2)
        self.assertEqual(row["median_latency_ms"], 100)

    def test_json_and_csv_keep_metrics_and_raw_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            benchmark.write_report(destination, {"settings": {}}, [self.sample()])
            payload = json.loads((destination / "results.json").read_text())
            self.assertEqual(payload["samples"][0]["traffic"]["requests"], 1)
            self.assertIn("http_download_bytes", (destination / "summary.csv").read_text())
            self.assertIn("display_mode", (destination / "summary.csv").read_text())

    def test_cli_rejects_invalid_conditions(self):
        for arguments in (["--pages", "0"], ["--concurrency", "1,0"], ["--engines", "unknown"],
                          ["--display-modes", "headful,headful"]):
            with self.subTest(arguments=arguments), patch("sys.stderr"), self.assertRaises(SystemExit):
                benchmark.parser().parse_args(arguments)

    def test_lightpanda_headful_is_reported_as_unsupported(self):
        args = benchmark.parser().parse_args(["--engines", "lightpanda", "--display-modes", "headful"])
        with self.assertRaisesRegex(ValueError, "headless-only"):
            benchmark.preflight(args)

    def test_parallel_matrix_runs_three_headless_and_two_headful_engines(self):
        barriers = {"headless": threading.Barrier(3), "headful": threading.Barrier(2)}
        observed = []
        lock = threading.Lock()

        def simulated_sample(config, *_args):
            barriers[config["display_mode"]].wait(timeout=3)
            with lock:
                observed.append(config.copy())
            result = self.sample(display_mode=config["display_mode"])
            result.update({key: config[key] for key in ("engine", "scenario", "mode", "concurrency", "iteration", "token")})
            return result

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results"
            with patch.object(benchmark, "preflight", return_value={}), patch.object(benchmark, "run_sample", side_effect=simulated_sample), patch("builtins.print"):
                code = benchmark.main(["--schedule", "parallel", "--scenarios", "static", "--modes", "warm",
                                       "--concurrency", "1", "--pages", "1", "--iterations", "1", "--warmup", "0",
                                       "--output", str(output)])
            report = json.loads((output / "results.json").read_text())
        self.assertEqual(code, 0)
        self.assertEqual(len(observed), 5)
        self.assertEqual(len({config["token"] for config in observed}), 5)
        self.assertEqual({config["engine"] for config in observed if config["display_mode"] == "headless"}, set(benchmark.ENGINES))
        self.assertEqual({config["engine"] for config in observed if config["display_mode"] == "headful"}, {"playwright", "patchright"})
        self.assertEqual(report["metadata"]["unsupported_conditions"][0]["engine"], "lightpanda")


if __name__ == "__main__":
    unittest.main()
