import shutil
import subprocess
import unittest
import hashlib
import json
import tempfile
import os
import sys
import zipfile
from pathlib import Path


class BotDiagnosticsTests(unittest.TestCase):
    def test_checkpoint_preserves_evidence_and_excludes_browser_state(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            run = directory / "run"
            database = run / "runtime-state/chromium-data/pki/nssdb"
            database.mkdir(parents=True)
            (database / "key4.db").write_bytes(b"private-state-fixture")
            (run / "blobs").mkdir()
            (run / "blobs/evidence").write_bytes(b"saved-evidence")
            (run / "results.json").write_text("[]")
            output = directory / "checkpoint.zip"
            result = subprocess.run(
                [sys.executable, "-B", "experiments/bot-diagnostics/export.py",
                 "--run", str(run), "--output", str(output)],
                cwd=repo, env={**os.environ, "BOT_DIAGNOSTICS_RUN_ROOT": str(directory)},
                text=True, capture_output=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with zipfile.ZipFile(output) as checkpoint:
                names = checkpoint.namelist()
                self.assertFalse(any("runtime-state" in Path(name).parts for name in names))
                evidence = next(name for name in names if name.endswith("/blobs/evidence"))
                self.assertEqual(checkpoint.read(evidence), b"saved-evidence")
                self.assertTrue(any(name.endswith("/results.json") for name in names))
                self.assertIn("SHA256.json", names)

    @unittest.skipUnless(shutil.which("node"), "Node.js is an optional diagnostics dependency")
    def test_native_fourplay_and_camoufox_runtime(self):
        repo = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", "--test", "experiments/bot-diagnostics/fourplay-native-runtime.test.mjs",
             "experiments/bot-diagnostics/camoufox-fourplay-runtime.test.mjs"],
            cwd=repo, text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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

    @unittest.skipUnless(shutil.which("node"), "Node.js is an optional diagnostics dependency")
    def test_session_pool_policy_and_accounting(self):
        repo = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", "--test", "experiments/bot-diagnostics/session-pool.test.mjs",
             "experiments/bot-diagnostics/session-policy.test.mjs",
             "experiments/bot-diagnostics/session-manager.test.mjs"],
            cwd=repo, text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is an optional diagnostics dependency")
    def test_chromium_trust_preflight(self):
        repo = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", "--test", "experiments/bot-diagnostics/chromium-trust.test.mjs"],
            cwd=repo, text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
