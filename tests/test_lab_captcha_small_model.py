"""Classification checks against saved real pages and explicit UI evidence."""
from __future__ import annotations

import importlib.util
from html.parser import HTMLParser
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("captcha_classifier", ROOT / "experiments/captcha-small-model/challenge_classifier.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


class CaptchaClassificationTests(unittest.TestCase):
    def test_saved_indeed_denial_is_not_an_image_puzzle(self):
        parser = VisibleText()
        parser.feed((ROOT / "lab-runs/bot-diagnostics-evidence/patchright-indeed-challenge.html").read_text())
        result = module.classify_observation({"http_status": 403, "title": "Blocked - Indeed.com", "visible_text": " ".join(parser.parts)})
        self.assertEqual(result["access_status"], "blocked")
        self.assertEqual(result["challenge_kind"], "unknown")

    def test_saved_indeed_js_check_is_browser_verification(self):
        parser = VisibleText()
        parser.feed((ROOT / "lab-runs/bot-diagnostics-evidence/impit-indeed-challenge.html").read_text())
        result = module.classify_observation({"http_status": 403, "title": "Security Check - Indeed.com", "visible_text": " ".join(parser.parts)})
        self.assertEqual(result["challenge_kind"], "browser_verification")

    def test_visible_tile_ui_takes_priority_over_generic_verification_copy(self):
        result = module.classify_observation({"http_status": 200, "visible_text": "Verify you are human", "fourget_tile_count": 16})
        self.assertEqual(result["challenge_kind"], "image_grid")

    def test_script_presence_and_documentation_do_not_prove_challenge(self):
        result = module.classify_observation({"http_status": 200, "title": "Troubleshooting guide", "visible_text": "This documentation explains Access Denied and Forbidden errors.", "resource_vendor_signals": ["recaptcha"]})
        self.assertEqual(result["access_status"], "content_observed")
        self.assertIsNone(result["challenge_kind"])

    def test_visible_provider_checkbox_is_a_widget(self):
        result = module.classify_observation({"http_status": 200, "visible_text": "Demo", "visible_widget_frames": [{"vendor": "hcaptcha", "checkbox_visible": True}]})
        self.assertEqual(result["access_status"], "widget_observed")
        self.assertEqual(result["challenge_kind"], "checkbox")

    def test_upstream_server_error_is_not_a_captcha(self):
        result = module.classify_observation({"http_status": 502, "visible_text": "upstream connect error", "resource_vendor_signals": ["turnstile"]})
        self.assertEqual(result["access_status"], "server_error")
        self.assertIsNone(result["challenge_kind"])

    def test_rate_limit_and_visible_widget_are_separate_observations(self):
        result = module.classify_observation({"http_status": 429, "visible_widget_frames": [{"vendor": "recaptcha", "checkbox_visible": True}]})
        self.assertEqual(result["access_status"], "rate_limited")
        self.assertEqual(result["challenge_kind"], "checkbox")

    def test_saved_duckduckgo_page_identifies_actual_image_prompt(self):
        text = "Unfortunately, bots use DuckDuckGo too. Please complete the following challenge to confirm this search was made by a human. Select all squares containing a duck:"
        result = module.classify_observation({"http_status": 202, "visible_text": text})
        self.assertEqual(result["challenge_kind"], "image_grid")


if __name__ == "__main__":
    unittest.main()
