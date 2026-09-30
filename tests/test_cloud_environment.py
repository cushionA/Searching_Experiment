import os
import socket
import unittest
from unittest.mock import patch

from scripts.check_cloud_environment import check_environment, inspect_environment


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
        self.assertEqual(set(report["adaptive_dependencies"]), {"crawlee", "impit", "playwright", "AdaptivePlaywrightCrawler"})
        self.assertTrue(all(type(value) is bool for value in report["adaptive_dependencies"].values()))

    def test_default_does_not_claim_model_verification_or_require_cli(self):
        with patch("socket.create_connection", side_effect=AssertionError("network")), patch.dict("os.environ", {}, clear=True):
            result = check_environment()
        self.assertTrue(result["ok"])
        self.assertEqual(result["model_requested"], "gpt-6-luna")
        self.assertEqual(result["model_selection_surface"], "codex_execution_ui")
        self.assertFalse(result["model_runtime_verified"])
        self.assertFalse(result["codex_cli_required"])

    def test_adaptive_missing_packages_and_sandbox_fail(self):
        with patch.dict(os.environ, {}, clear=True), patch("scripts.check_cloud_environment.importlib.import_module", side_effect=ImportError("missing")):
            result = check_environment(adaptive=True)
        self.assertFalse(result["ok"])
        self.assertFalse(result["browser_sandbox_disabled"])
        self.assertIn("impit", result["packages"])

    def test_probe_is_only_proxy_tcp_and_redacts_credentials(self):
        proxy = "http://alice:secret@proxy.example:8080"
        with patch("urllib.request.getproxies", return_value={"https": proxy}), patch("socket.create_connection", side_effect=socket.gaierror("DNS failure for " + proxy)) as connect:
            result = check_environment(probe_proxy=True)
        connect.assert_called_once_with(("proxy.example", 8080), timeout=3)
        self.assertEqual(result["proxy_probe"]["status"], "failed")
        self.assertFalse(result["ok"])
        self.assertNotIn("secret", str(result))
        self.assertNotIn("alice", str(result))

    def test_no_proxy_target_is_not_probed(self):
        with patch("urllib.request.getproxies", return_value={"https": "http://proxy.example:8080", "no": "aozora.gr.jp"}), patch("socket.create_connection") as connect:
            result = check_environment(probe_proxy=True)
        connect.assert_not_called()
        self.assertTrue(result["no_proxy_applies"])
        self.assertEqual(result["proxy_probe"]["status"], "not_applicable")

    def test_missing_cli_or_api_auth_never_claims_ready(self):
        with patch.dict("os.environ", {}, clear=True), patch("scripts.check_cloud_environment.codex_binary", return_value=None):
            cli = check_environment(backend="codex")
            api = check_environment(backend="responses")
            native = check_environment(backend="native")
        self.assertFalse(cli["ok"])
        self.assertTrue(cli["codex_cli_required"])
        self.assertFalse(api["ok"])
        self.assertFalse(native["native_tools_runtime_verified"])


if __name__ == "__main__":
    unittest.main()
