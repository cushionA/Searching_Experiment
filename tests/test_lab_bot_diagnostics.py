import shutil
import subprocess
import unittest
import hashlib
import json
import tempfile
from pathlib import Path


class BotDiagnosticsTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is an optional diagnostics dependency")
    def test_verify_cli_fails_when_saved_evidence_is_corrupted(self):
        repo = Path(__file__).resolve().parents[1]
        body = b"original"
        digest = hashlib.sha256(body).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            (run / "blobs").mkdir()
            blob = run / "blobs" / digest
            blob.write_bytes(body)
            (run / "results.json").write_text("[]")
            (run / "ledger.json").write_text(json.dumps({
                "budgets": {"impit/fixture": {"requests": 1, "bytes_charged": len(body)}},
                "records": [{"index": 1, "key": "impit/fixture", "status": 200,
                             "cap": len(body), "bytes_charged": len(body), "body_sha256": digest}],
            }))
            command = ["node", "experiments/bot-diagnostics/runner.mjs", "verify", str(run)]
            valid = subprocess.run(command, cwd=repo, text=True, capture_output=True, timeout=30)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertTrue(json.loads(valid.stdout)["ok"])
            blob.write_bytes(b"changed")
            corrupted = subprocess.run(command, cwd=repo, text=True, capture_output=True, timeout=30)
            self.assertEqual(corrupted.returncode, 1, corrupted.stderr)
            self.assertFalse(json.loads(corrupted.stdout)["ok"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is an optional diagnostics dependency")
    def test_offline_diagnostics_guards(self):
        repo = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", "experiments/bot-diagnostics/runner.test.mjs"],
            cwd=repo, text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is an optional diagnostics dependency")
    def test_offline_framework_contract(self):
        repo = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", "experiments/bot-diagnostics/framework.test.mjs"],
            cwd=repo, text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
