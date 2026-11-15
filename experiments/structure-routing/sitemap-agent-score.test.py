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

    def test_non_200_saved_html_is_measured_separately_from_http_status(self):
        source = "<html><head><title>Page</title></head><body><main><h1>Page</h1><p>" + ("Useful saved page content. " * 8) + "</p></main></body></html>"
        result = score.evaluate_item(self.write_item(source, status=503), self.case, self.root)
        self.assertTrue(result["saved_html_valid"])
        self.assertEqual(result["http_status"], 503)
        self.assertEqual(result["classification"], "unverified")
        self.assertEqual(result["rationale"], "http_status_not_200")

    def test_scholarly_article_schema_and_article_region(self):
        source = """<html><head><script type="application/ld+json">{"@type":"ScholarlyArticle"}</script></head><body><main><h1>Measuring urban heat patterns</h1><p>""" + ("The study reports a measured scientific result. " * 8) + """</p></main></body></html>"""
        item = self.write_item(source, url="https://journal.example/article/42", final_url="https://journal.example/article/42")
        case = {"expected_type": "scholarly_article", "url_patterns": [r"/article/\d+$"]}
        result = score.evaluate_item(item, case, self.root)
        self.assertEqual(result["classification"], "strict_success")
        self.assertEqual(result["rationale"], "scholarly_article_schema_and_article_content")

    def test_news_article_rejects_search_shell_even_with_article_schema(self):
        source = """<html><head><script type="application/ld+json">{"@type":"NewsArticle"}</script></head><body><main><h1>Search results</h1><p>""" + ("Browse the latest headlines and related stories. " * 8) + """</p><a href="/news/1">Story</a></main></body></html>"""
        item = self.write_item(source, url="https://news.example/search?q=x", final_url="https://news.example/search?q=x")
        case = {"expected_type": "news_article", "url_patterns": [r"/search"]}
        result = score.evaluate_item(item, case, self.root)
        self.assertEqual(result["classification"], "unverified")
        self.assertEqual(result["rationale"], "detail_page_or_entity_heading_missing")

    def test_dataset_requires_dataset_schema_and_download_resource(self):
        source = """<html><head><script type="application/ld+json">{"@type":"Dataset"}</script></head><body><main><h1>Annual rainfall observations</h1><p>""" + ("This public dataset contains observations by region and year. " * 5) + """</p><a href="/files/rainfall.csv">Download CSV</a></main></body></html>"""
        item = self.write_item(source, url="https://data.example/datasets/rainfall", final_url="https://data.example/datasets/rainfall")
        case = {"expected_type": "public_dataset", "url_patterns": [r"/datasets/[^/?]+$" ]}
        result = score.evaluate_item(item, case, self.root)
        self.assertEqual(result["classification"], "strict_success")
        source_without_download = source.replace('<a href="/files/rainfall.csv">Download CSV</a>', "")
        result = score.evaluate_item(self.write_item(source_without_download, url="https://data.example/datasets/rainfall", final_url="https://data.example/datasets/rainfall"), case, self.root)
        self.assertEqual(result["classification"], "unverified")

    def test_bibliographic_and_book_item_evidence(self):
        body = "<p>" + ("Bibliographic record with publication details, author information, identifiers, and edition data. " * 4) + "</p>"
        source = "<html><head><meta name='citation_title' content='A collected work'><script type='application/ld+json'>{\"@type\":\"Book\"}</script></head><body><main><h1>A collected work</h1>" + body + "</main></body></html>"
        item = self.write_item(source, url="https://library.example/item/123", final_url="https://library.example/item/123")
        for expected in ("bibliographic_record", "book_or_museum_item"):
            case = {"expected_type": expected, "allowed_hosts": ["library.example"]}
            result = score.evaluate_item(item, case, self.root)
            self.assertEqual(result["classification"], "strict_success", expected)

    def test_report_documentation_and_accommodation_types(self):
        content = "<p>" + ("This page provides detailed information about its subject and context. " * 4) + "</p>"
        cases = [
            ("report", "Report", "https://reports.example/publication/2026", "reports.example", "report_schema_and_article_content"),
            ("documentation_page", "TechArticle", "https://docs.example/reference/widget", "docs.example", "documentation_heading_and_article_content"),
            ("travel_accommodation_property", "Hotel", "https://stay.example/hotels/harbor", "stay.example", "accommodation_or_property_schema_and_item_content"),
        ]
        for expected, schema_type, url, host, rationale in cases:
            source = "<html><head><script type='application/ld+json'>{\"@type\":\"" + schema_type + "\"}</script></head><body><main><h1>Harbor House details</h1>" + content + "</main></body></html>"
            item = self.write_item(source, url=url, final_url=url)
            case = {"expected_type": expected, "allowed_hosts": [host]}
            result = score.evaluate_item(item, case, self.root)
            self.assertEqual(result["classification"], "strict_success", expected)
            self.assertEqual(result["rationale"], rationale)

    def test_touristattraction_and_place_with_entity_heading_and_detail_content(self):
        content = "<p>" + ("Visitor information describes the site's history, location, opening details, and notable features. " * 4) + "</p>"
        for schema_type in ("TouristAttraction", "Place"):
            source = "<html><head><script type='application/ld+json'>{\"@type\":\"" + schema_type + "\"}</script></head><body><main><h1>North Ridge Observatory</h1>" + content + "</main></body></html>"
            item = self.write_item(source, url="https://visit.example/places/observatory", final_url="https://visit.example/places/observatory")
            case = {"expected_type": "travel_accommodation_property", "allowed_hosts": ["visit.example"]}
            result = score.evaluate_item(item, case, self.root)
            self.assertEqual(result["classification"], "strict_success", schema_type)

    def test_explicit_listing_schema_without_expected_entity_is_unverified(self):
        content = "<p>" + ("Browse available resources and review information for each result. " * 5) + "</p>"
        for schema_type in ("CollectionPage", "SearchResultsPage", "ItemList"):
            source = "<html><head><script type='application/ld+json'>{\"@type\":\"" + schema_type + "\"}</script></head><body><main><h1>Rainfall resources</h1>" + content + "<a href='/rainfall.csv'>Download CSV</a></main></body></html>"
            item = self.write_item(source, url="https://data.example/datasets/rainfall", final_url="https://data.example/datasets/rainfall")
            case = {"expected_type": "public_dataset", "allowed_hosts": ["data.example"]}
            result = score.evaluate_item(item, case, self.root)
            self.assertEqual(result["classification"], "unverified", schema_type)
            self.assertEqual(result["rationale"], "listing_schema_without_expected_entity_type")

    def test_itemlist_related_to_expected_entity_does_not_reject_detail(self):
        source = """<html><head><script type="application/ld+json">{"@type":["TouristAttraction","ItemList"]}</script></head><body><main><h1>North Ridge Observatory</h1><p>""" + ("Visitor information describes the site's history, location, opening details, and notable features. " * 4) + """</p></main></body></html>"""
        item = self.write_item(source, url="https://visit.example/places/observatory", final_url="https://visit.example/places/observatory")
        case = {"expected_type": "travel_accommodation_property", "allowed_hosts": ["visit.example"]}
        result = score.evaluate_item(item, case, self.root)
        self.assertEqual(result["classification"], "strict_success")

    def test_new_type_redirect_requires_frozen_final_url_allowance(self):
        source = """<html><head><script type="application/ld+json">{"@type":"NewsArticle"}</script></head><body><article><h1>Local transit expands service</h1><p>""" + ("The city announced new service and detailed its route changes. " * 6) + """</p></article></body></html>"""
        item = self.write_item(source, url="https://news.example/story?id=7", final_url="https://www.news.example/2026/story-7")
        case = {"expected_type": "news_article", "url_patterns": [r"/story\?id=7$"], "allowed_final_url_patterns": [r"/2026/story-7$"]}
        result = score.evaluate_item(item, case, self.root)
        self.assertEqual(result["classification"], "strict_success")
        case["allowed_final_url_patterns"] = []
        result = score.evaluate_item(item, case, self.root)
        self.assertEqual(result["classification"], "unverified")
        self.assertEqual(result["rationale"], "final_url_outside_evaluator_scope")

    def test_new_type_redirect_does_not_rescue_out_of_scope_original_url(self):
        source = """<html><head><script type="application/ld+json">{"@type":"NewsArticle"}</script></head><body><article><h1>Local transit expands service</h1><p>""" + ("The city announced new service and detailed its route changes. " * 6) + """</p></article></body></html>"""
        item = self.write_item(source, url="https://news.example/unlisted", final_url="https://news.example/2026/story-7")
        case = {"expected_type": "news_article", "url_patterns": [r"/story/"], "allowed_final_url_patterns": [r"/2026/story-7$"]}
        result = score.evaluate_item(item, case, self.root)
        self.assertEqual(result["classification"], "unverified")
        self.assertEqual(result["rationale"], "url_outside_evaluator_scope")

    def test_host_scope_keeps_original_and_allows_listed_final_host(self):
        source = """<html><head><script type="application/ld+json">{"@type":"NewsArticle"}</script></head><body><article><h1>Local transit expands service</h1><p>""" + ("The city announced new service and detailed its route changes. " * 6) + """</p></article></body></html>"""
        item = self.write_item(source, url="https://news.example/story/7", final_url="https://www.news.example/story/7")
        case = {"expected_type": "news_article", "allowed_hosts": ["news.example", "www.news.example"]}
        result = score.evaluate_item(item, case, self.root)
        self.assertEqual(result["classification"], "strict_success")
        case["allowed_hosts"] = ["www.news.example"]
        result = score.evaluate_item(item, case, self.root)
        self.assertEqual(result["classification"], "unverified")
        self.assertEqual(result["rationale"], "url_outside_evaluator_scope")


if __name__ == "__main__":
    unittest.main()
