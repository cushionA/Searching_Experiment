import pathlib
import shutil
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
NODE_TEST = ROOT / "experiments/captcha-small-model/local_visual_browser.test.mjs"
PLAYWRIGHT = ROOT / ".deps/bot-diagnostics/node_modules/playwright/package.json"


class LocalVisualBrowserTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed; browser fixture requires Node.js")
    def test_local_visual_browser_node_suite(self):
        if not PLAYWRIGHT.is_file():
            self.skipTest("local Playwright dependency is unavailable at .deps/bot-diagnostics; no browser tests can run")
        if not pathlib.Path("/usr/bin/chromium").is_file():
            self.skipTest("/usr/bin/chromium is unavailable; real Chromium fixture tests cannot run")
        subprocess.run(
            [shutil.which("node"), "--test", str(NODE_TEST)],
            cwd=ROOT,
            check=True,
            timeout=60,
        )


if __name__ == "__main__":
    unittest.main()
