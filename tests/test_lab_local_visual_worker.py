from __future__ import annotations

import importlib.util
import io
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "experiments/captcha-small-model/local_visual_worker.py"
spec = importlib.util.spec_from_file_location("local_visual_worker", SCRIPT)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class FakeSolver:
    instances = []

    def __init__(self, *args):
        self.calls = []
        self.__class__.instances.append(self)

    def predict_type_a(self, paths, target):
        self.calls.append(("A", paths, target))
        return {"selection": [0, 8], "scores": [0.1] * 9, "target": target}

    def predict_type_b(self, path, target, rows, columns):
        self.calls.append(("B", path, target, rows, columns))
        return {"selection": [1], "scores": [0.0, 0.8, 0.0, 0.0], "target": target}


class LocalVisualWorkerTests(unittest.TestCase):
    def setUp(self):
        self.offline_flags = {name: os.environ.get(name) for name in
                              ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")}
        self.socket_methods = {name: getattr(socket.socket, name) for name in ("connect", "connect_ex")}
        self.socket_functions = {name: getattr(socket, name) for name in ("create_connection", "getaddrinfo")}
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.models = self.root / "models"
        self.models.mkdir()
        (self.models / "manifest.json").write_text("{}", encoding="utf-8")
        self.images = self.root / "images"
        self.images.mkdir()
        self.paths = []
        for i in range(10):
            path = self.images / f"{i}.png"
            path.write_bytes(f"image-{i}".encode())
            self.paths.append(path)
        FakeSolver.instances.clear()

    def tearDown(self):
        for name, value in self.offline_flags.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for name, value in self.socket_methods.items():
            setattr(socket.socket, name, value)
        for name, value in self.socket_functions.items():
            setattr(socket, name, value)
        self.tmp.cleanup()

    def run_worker(self, requests):
        input_stream = io.StringIO("".join(json.dumps(row) + "\n" for row in requests))
        output_stream = io.StringIO()
        code = worker.main(["--model-root", str(self.models), "--image-root", str(self.images)],
                           solver_factory=FakeSolver, stdin=input_stream, stdout=output_stream)
        return code, [json.loads(line) for line in output_stream.getvalue().splitlines()]

    def test_ready_once_multiple_requests_reuse_solver_and_hash_inputs(self):
        req_a = {"id": "a", "type": "A", "target": "cars", "images": [str(p) for p in self.paths[:9]]}
        req_b = {"id": "b", "type": "B", "target": "buses", "images": [str(self.paths[9])], "rows": 2, "columns": 2}
        code, events = self.run_worker([req_a, req_b])
        self.assertEqual(code, 0)
        self.assertEqual([event.get("event") for event in events if "event" in event], ["ready"])
        self.assertEqual([event["id"] for event in events[1:]], ["a", "b"])
        self.assertTrue(all(event["ok"] for event in events[1:]))
        self.assertEqual(len(FakeSolver.instances), 1)
        self.assertEqual(len(FakeSolver.instances[0].calls), 2)
        self.assertEqual(len(events[1]["prediction"]["input_image_sha256"]), 9)
        self.assertIn("model_manifest_sha256", events[0])
        self.assertIn("runtime", events[0])
        self.assertIn("source_sha256", events[0])

    def test_validation_errors_are_per_request_and_worker_recovers(self):
        outside = self.root / "outside.png"
        outside.write_bytes(b"outside")
        invalid = [
            {"id": "bad-count", "type": "A", "target": "cars", "images": [str(self.paths[0])]},
            {"id": "bad-path", "type": "B", "target": "cars", "images": [str(outside)]},
            {"id": "bad-grid", "type": "B", "target": "cars", "images": [str(self.paths[0])], "rows": True},
            {"id": "bad-target", "type": "B", "target": "  ", "images": [str(self.paths[0])]},
        ]
        good = {"id": "ok", "type": "B", "target": "cars", "images": [str(self.paths[0])], "rows": 2, "columns": 2}
        code, events = self.run_worker(invalid + [good])
        self.assertEqual(code, 0)
        self.assertEqual(len(events), 6)
        for event in events[1:5]:
            self.assertFalse(event["ok"])
            self.assertIn("error", event)
        self.assertTrue(events[-1]["ok"])
        self.assertEqual(len(FakeSolver.instances[0].calls), 1)

    def test_solver_result_indices_and_scores_are_validated(self):
        class InvalidSolver(FakeSolver):
            def predict_type_b(self, *args):
                return {"selection": [0, 0], "scores": [0.1] * 4}

        request = {"id": "bad-result", "type": "B", "target": "cars", "images": [str(self.paths[0])], "rows": 2, "columns": 2}
        output = io.StringIO()
        code = worker.main(["--model-root", str(self.models), "--image-root", str(self.images)],
                           solver_factory=InvalidSolver, stdin=io.StringIO(json.dumps(request) + "\n"), stdout=output)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(code, 0)
        self.assertFalse(events[1]["ok"])
        self.assertIn("duplicate", events[1]["error"]["message"])

    def test_initialization_failure_emits_fatal_and_returns_nonzero(self):
        def fail(*args):
            raise RuntimeError("broken model")
        output = io.StringIO()
        code = worker.main(["--model-root", str(self.models), "--image-root", str(self.images)],
                           solver_factory=fail, stdin=io.StringIO(), stdout=output)
        event = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(event["event"], "fatal")
        self.assertEqual(event["error"]["type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
