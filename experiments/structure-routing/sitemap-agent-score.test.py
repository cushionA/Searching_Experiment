import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("sitemap-agent-score.py")
SPEC = importlib.util.spec_from_file_location("sitemap_agent_score", MODULE_PATH)
score = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(score)


class SitemapAgentScoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.case = {"id": "02", "expected_type": "product", "url_patterns": [r"/products?/[^/]+/?$"]}

    def tearDown(self):
        self.temp.cleanup()

    def write_item(self, source, rendered=None, **extra):
        source_path = self.root / "source.html"
        rendered_path = self.root / "rendered.html"
        source_path.write_text(source, encoding="utf-8")
        rendered_path.write_text(rendered if rendered is not None else source, encoding="utf-8")
        item = {
            "url": "https://shop.example/products/widget",
            "final_url": "https://shop.example/products/widget",
            "status": 200,
            "valid": True,
            "source_html": str(source_path),
            "rendered_dom": str(rendered_path),
        }
        item.update(extra)
        return item

    def test_product_schema_is_independent_strict_success(self):
        source = """<html><head><title>Widget</title><script type="application/ld+json">{"@type":"Product"}</script></head><body><main><h1>Widget</h1><p>""" + ("Product details and specifications. " * 5) + """</p></main></body></html>"""
        result = score.evaluate_item(self.write_item(source), self.case, self.root)
        self.assertEqual(result["classification"], "strict_success")
        self.assertEqual(result["rationale"], "schema_product_evidence")

    def test_product_schema_can_be_in_div_only_layout(self):
        source = """<html><head><script type="application/ld+json">{"@type":"Product"}</script></head><body><div><h1>Widget</h1><p>""" + ("Product details and specifications. " * 5) + """</p></div></body></html>"""
        result = score.evaluate_item(self.write_item(source), self.case, self.root)
        self.assertEqual(result["classification"], "strict_success")

    def test_script_only_shell_is_rejected_even_with_long_script_text(self):
        source = "<html><head><title>Widget</title></head><body><div id='app'></div><script>" + ("Product Widget details " * 20) + "</script></body></html>"
        result = score.evaluate_item(self.write_item(source), self.case, self.root)
        self.assertEqual(result["classification"], "unverified")
        self.assertEqual(result["rationale"], "empty_or_spa_shell")

    def test_listing_without_product_type_is_unverified(self):
        source = "<html><head><title>Products</title></head><body><main><h1>Products</h1><p>" + ("Browse items in this catalog. " * 6) + "</p><a href='/products/widget'>Widget</a></main></body></html>"
        result = score.evaluate_item(self.write_item(source), self.case, self.root)
        self.assertEqual(result["classification"], "unverified")
        self.assertEqual(result["rationale"], "product_type_evidence_missing")

    def test_empty_spa_shell_does_not_count_as_saved_page(self):
        source = "<html><head><title>Widget</title></head><body><div id='app'></div></body></html>"
        result = score.evaluate_item(self.write_item(source), self.case, self.root)
        self.assertEqual(result["classification"], "unverified")
        self.assertEqual(result["rationale"], "empty_or_spa_shell")

    def test_hash_mismatch_invalidates_saved_html(self):
        source = "<html><head><title>Widget</title></head><body><main><h1>Widget</h1><p>" + ("Product detail. " * 8) + "</p><button>Add to cart</button></main></body></html>"
        result = score.evaluate_item(self.write_item(source, source, source_sha256="0" * 64), self.case, self.root)
        self.assertEqual(result["classification"], "unverified")
        self.assertEqual(result["rationale"], "source_html_hash_mismatch")

    def test_scroll_success_requires_fresh_link_and_saved_strict_detail(self):
        source = """<html><head><title>Widget</title><script type="application/ld+json">{"@type":"Product"}</script></head><body><main><h1>Widget</h1><p>""" + ("Product details and specifications. " * 5) + """</p></main></body></html>"""
        item = self.write_item(source)
        before = self.root / "before.html"
        after = self.root / "after.html"
        before.write_text("<html><body><main><a href='/products/old'>Old</a></main></body></html>", encoding="utf-8")
        after.write_text("<html><body><main><a href='/products/old'>Old</a><a href='/products/widget'>Widget</a></main></body></html>", encoding="utf-8")
        row = {
            "id": "02", "page_url": "https://shop.example/collections/all",
            "selected_items": [item],
            "snapshots": {"before": str(before), "after": str(after)},
            "fresh_links_after_scroll": ["https://shop.example/products/widget"],
        }
        result = score.evaluate_row(row, {"cases": [self.case]}, self.root)
        self.assertEqual(result["scroll_growth"]["strict_saved_detail_successes"], 1)
        row["fresh_links_after_scroll"] = []
        result = score.evaluate_row(row, {"cases": [self.case]}, self.root)
        self.assertEqual(result["scroll_growth"]["strict_saved_detail_successes"], 0)

    def test_live_contract_prefers_html_snapshots_and_reads_zero_usage(self):
        source = """<html><head><script type="application/ld+json">{"@type":"Product"}</script></head><body><div><h1>Widget</h1><p>""" + ("Product details and specifications. " * 5) + """</p></div></body></html>"""
        item = self.write_item(source, saved=True)
        item.pop("valid")
        before_html = self.root / "before-render.html"
        after_html = self.root / "after-render.html"
        before_html.write_text("<html><body><a href='/products/old'>Old</a></body></html>", encoding="utf-8")
        after_html.write_text("<html><body><a href='/products/old'>Old</a><a href='/products/widget'>Widget</a></body></html>", encoding="utf-8")
        before_json = self.root / "before-state.json"
        after_json = self.root / "after-state.json"
        before_json.write_text(json.dumps({"url": "https://shop.example/collections/all"}), encoding="utf-8")
        after_json.write_text(json.dumps({"url": "https://shop.example/collections/all"}), encoding="utf-8")
        row = {
            "id": "02",
            "selected_items": [item], "before_html": str(before_html), "after_html": str(after_html),
            "snapshots": {"before": str(before_json), "after": str(after_json)},
            "fresh_item_urls_after_scroll": ["https://shop.example/products/widget"],
            "fresh_links_after_scroll": 1, "external_llm_calls": 0, "local_inference": {"calls": 0},
        }
        policy = {"sites": {"02": {"expected_type": "product", "url_regex": r"/products?/[^/]+/?$"}}}
        result = score.evaluate_row(row, policy, self.root)
        self.assertEqual(result["scroll_growth"]["strict_saved_detail_successes"], 1)
        self.assertEqual(result["model_calls_from_result"], 0)
        self.assertIsNone(result["items"][0]["candidate_valid"])
        self.assertIs(result["items"][0]["candidate_saved"], True)


if __name__ == "__main__":
    unittest.main()
