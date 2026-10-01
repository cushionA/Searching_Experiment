import shutil
import subprocess
import unittest
from pathlib import Path


class BotDiagnosticsTests(unittest.TestCase):
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
