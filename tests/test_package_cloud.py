import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from package_cloud import package


class CloudPackageTests(unittest.TestCase):
    def test_includes_captcha_and_fourplay_sources_but_no_runtime_data(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cloud.zip"
            package(ROOT, output)

            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertIsNone(archive.testzip())
                self.assertLessEqual({
                    "experiments/captcha-small-model/challenge_classifier.py",
                    "experiments/captcha-small-model/subprocess_runner.py",
                    "lab-runs/bot-diagnostics-evidence/patchright-indeed-challenge.html",
                    "lab-runs/bot-diagnostics-evidence/impit-indeed-challenge.html",
                    "experiments/fourget-selfhost/start.py",
                    "experiments/fourget-selfhost/fourplay/server.cjs",
                    "experiments/bot-diagnostics/fourplay-runtime.mjs",
                    "experiments/bot-diagnostics/fourplay-bridge-trace.cjs",
                    "experiments/bot-diagnostics/dom-stability.mjs",
                    "docs/bot-diagnostics-fourplay.md",
                }, names)
                self.assertFalse(any(
                    "/.runtime/" in f"/{name}/"
                    or "/node_modules/" in f"/{name}/"
                    or name.startswith("lab-runs/") and name not in {
                        "lab-runs/bot-diagnostics-evidence/patchright-indeed-challenge.html",
                        "lab-runs/bot-diagnostics-evidence/impit-indeed-challenge.html",
                    }
                    for name in names
                ))


if __name__ == "__main__":
    unittest.main()
