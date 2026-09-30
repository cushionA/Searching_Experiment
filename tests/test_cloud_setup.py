import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "posix", "Cloud setup uses a POSIX shell")
class CloudSetupTests(unittest.TestCase):
    def test_core_setup_runs_without_codex_node_or_npm(self):
        with tempfile.TemporaryDirectory() as directory:
            commands = Path(directory)
            for name, executable in (("bash", shutil.which("bash")), ("dirname", shutil.which("dirname")), ("python3", sys.executable)):
                self.assertIsNotNone(executable)
                (commands / name).symlink_to(executable)
            environment = os.environ.copy()
            environment["PATH"] = str(commands)
            result = subprocess.run([str(commands / "bash"), "scripts/cloud_setup.sh"], cwd=ROOT,
                                    env=environment, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"ok": true', result.stdout)


if __name__ == "__main__":
    unittest.main()
