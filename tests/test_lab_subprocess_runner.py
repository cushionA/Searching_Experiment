"""Real-process regressions for timeout and inherited-pipe handling."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import time
import unittest


PATH = Path(__file__).resolve().parents[1] / "experiments/captcha-small-model/subprocess_runner.py"
SPEC = importlib.util.spec_from_file_location("subprocess_runner", PATH)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


@unittest.skipUnless(sys.platform == "linux", "Kaggle's Linux process-group behavior")
class SubprocessRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / "process.log"

    def run_code(self, code, timeout=2):
        return runner.run_logged_process([sys.executable, "-u", "-c", code], self.log,
                                         timeout, drain_seconds=0.3, echo=False)

    def test_successful_parent_with_child_holding_stdout_is_not_a_model_timeout(self):
        code = "import os,time; child=os.fork(); time.sleep(30) if child == 0 else print('complete', flush=True)"
        started = time.monotonic()
        result = self.run_code(code)
        self.assertEqual(result["returncode"], 0)
        self.assertFalse(result["timed_out"])
        self.assertTrue(result["stdout_drain_truncated"])
        self.assertLess(time.monotonic() - started, 2)
        self.assertIn("complete", self.log.read_text())
        self.assertNotIn("TIMEOUT after", self.log.read_text())

    def test_running_parent_is_stopped_at_deadline_even_if_it_prints_completion(self):
        result = self.run_code("import time; print('model_complete',flush=True); time.sleep(30)", timeout=0.3)
        self.assertTrue(result["timed_out"])
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("TIMEOUT after", self.log.read_text())

    def test_closed_stdout_does_not_hide_a_still_running_parent(self):
        result = self.run_code("import os,time; os.close(1); os.close(2); time.sleep(30)", timeout=0.3)
        self.assertTrue(result["timed_out"])
        self.assertNotEqual(result["returncode"], 0)

    def test_nonzero_process_exit_is_preserved(self):
        result = self.run_code("import sys; print('failure'); sys.exit(7)")
        self.assertEqual(result["returncode"], 7)
        self.assertFalse(result["timed_out"])
        self.assertIn("failure", self.log.read_text())


if __name__ == "__main__":
    unittest.main()
