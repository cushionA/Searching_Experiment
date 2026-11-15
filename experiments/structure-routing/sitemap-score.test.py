import hashlib
import importlib.util
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path


SCORE_PATH = Path(__file__).with_name("sitemap-score.py")
SPEC = importlib.util.spec_from_file_location("sitemap_score", SCORE_PATH)
SCORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCORE)


class SitemapScoreFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.content = " ".join(["real product details and specifications for this item"] * 8)

    def tearDown(self):
        self.temp.cleanup()

    def check_page(self, name, source, rendered, status=200):
        source_path = self.root / f"{name}-source.html"
        source_path.write_text(source, encoding="utf-8")
        observation = {
            "source_html": str(source_path),
            "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "title": "Product 123",
            "status": status,
        }
        if rendered is not None:
            rendered_path = self.root / f"{name}-rendered.html"
            rendered_path.write_text(rendered, encoding="utf-8")
            observation["rendered_dom"] = str(rendered_path)
        return SCORE.valid_html(observation, self.root, {"title": "Product 123"})

    def test_header_login_link_does_not_reject_product_page(self):
        source = f"<html><head><title>Product 123</title></head><body><header><a href='/login'>Login</a></header><main><h1>Product 123</h1><p>{self.content}</p></main></body></html>"
        self.assertEqual(self.check_page("header-login", source, None), (True, "valid_html"))

    def test_captcha_script_or_widget_does_not_reject_normal_page(self):
        source = f"<html><head><title>Product 123</title><script>captcha challenge</script></head><body><main><h1>Product 123</h1><p>{self.content}</p><div class='captcha-widget'></div></main></body></html>"
        self.assertEqual(self.check_page("captcha-widget", source, None), (True, "valid_html"))

    def test_empty_source_with_rendered_main_is_valid_js_rendered_html(self):
        source = "<html><head><script>boot()</script></head><body><div id='app'></div></body></html>"
        rendered = f"<html><body><main><h1>Product 123</h1><p>{self.content}</p></main></body></html>"
        self.assertEqual(self.check_page("empty-spa", source, rendered), (True, "js_rendered_html"))

    def test_dictionary_cost_fields_are_aggregated(self):
        totals = defaultdict(float)
        present = defaultdict(bool)
        SCORE.add_costs(totals, present, {
            "sitemap_discovery": {"requests": 2, "responses": 2, "bytes": 120, "elapsed_ms": 30},
            "robots": {"requests": 1, "responses": 1, "bytes": 12},
            "document_requests": 3,
            "document_responses": 2,
            "http_requests": 9,
            "xhr_fetch": 4,
            "response_bytes": 500,
            "browser_ms": 70,
        })
        self.assertEqual(totals["sitemap_discovery_requests"], 2)
        self.assertEqual(totals["document_responses"], 2)
        self.assertEqual(totals["http_requests"], 9)

    def test_missing_metadata_stays_unknown(self):
        self.assertIsNone(SCORE.number_or_none(None))
        self.assertEqual(SCORE.number_or_none(0), 0)

    def test_non_200_response_is_not_valid_html(self):
        source = f"<html><head><title>Product 123</title></head><body><main><h1>Product 123</h1><p>{self.content}</p></main></body></html>"
        self.assertEqual(self.check_page("http-503", source, None, status=503), (False, "http_status_not_200"))

    def test_not_found_heading_is_rejected(self):
        source = f"<html><head><title>404 - Page not found</title></head><body><main><h1>404 - Page not found</h1><p>{self.content}</p></main></body></html>"
        self.assertEqual(self.check_page("not-found", source, None), (False, "maintenance_or_not_found_heading"))

    def test_maintenance_heading_is_rejected(self):
        source = f"<html><head><title>Under maintenance</title></head><body><main><h1>Under maintenance</h1><p>{self.content}</p></main></body></html>"
        self.assertEqual(self.check_page("maintenance", source, None), (False, "maintenance_or_not_found_heading"))

    def test_body_mention_of_404_does_not_reject_page(self):
        source = f"<html><head><title>Product 123</title></head><body><main><h1>Product 123</h1><p>404 is an error code. {self.content}</p></main></body></html>"
        self.assertEqual(self.check_page("body-404-mention", source, None), (True, "valid_html"))


if __name__ == "__main__":
    unittest.main()
