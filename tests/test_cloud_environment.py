import os
import unittest
from unittest.mock import patch

from scripts.check_cloud_environment import inspect_environment


class CloudEnvironmentTests(unittest.TestCase):
    def test_core_report_does_not_claim_model_verification(self):
        report = inspect_environment()
        self.assertEqual(report["model_requested"], "gpt-6-luna")
        self.assertEqual(report["model_selection_surface"], "codex_execution_ui")
        self.assertFalse(report["model_runtime_verified"])
        self.assertFalse(report["codex_cli_required"])

    def test_adaptive_report_checks_saved_sandbox_setting(self):
        with patch.dict(os.environ, {"CRAWLEE_DISABLE_BROWSER_SANDBOX": "true"}):
            report = inspect_environment(adaptive=True)
        self.assertTrue(report["browser_sandbox_disabled"])
        self.assertTrue(all(report["adaptive_dependencies"].values()))


if __name__ == "__main__":
    unittest.main()
