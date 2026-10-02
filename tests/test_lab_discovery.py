import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

from jse.lab.__main__ import export_checkpoint, fixture_answer
from jse.lab.agent import answer_schema
from jse.lab.discovery import in_scope, search_urls, topic_config
from jse.lab.engine import Engine, verify
from jse.lab.fetch import Fetcher, FixtureTransport, LiveTransport, parse_page
from jse.lab.state import LabError, Store, validate_config


class SearchFixture(FixtureTransport):
    def get(self, url, cap, timeout, user_agent):
        if url.endswith("/robots.txt"):
            return 200, {"content-type": "text/plain"}, b"User-agent: *\nAllow: /\n", False
        if urlsplit(url).hostname == "html.duckduckgo.com":
            body = '<title>Search</title><a href="https://new.example/paper">Unknown research source</a>'
        else:
            body = '<title>Original study</title><p>Citation chasing follows references to discover additional studies.</p>'
        return 200, {"content-type": "text/html; charset=utf-8"}, body.encode(), False


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "run"

    def create(self, **changes):
        config = topic_config("find source discovery methods", ["duckduckgo"])
        config["limits"].update(pages_per_arm=2, **changes)
        store = Store.create(self.run, config)
        return store, Engine(store, SearchFixture())

    def request(self, store):
        return json.loads(store.artifact(store.state["active_request"]).read_text())

    def approve(self, store, engine):
        engine.advance()
        engine.answer(fixture_answer(self.request(store)))
        engine.decide(store.state["gate"]["id"], "approve", "fixture test authorization")

    def explore(self, store, engine):
        self.approve(store, engine)
        engine.advance()
        request = self.request(store)
        return dict(request_id=request["request_id"], next_urls=["https://new.example/paper"],
                    stop=False, reason="follow observed external source", claims=[], search_queries=[])

    def finish(self):
        store, engine = self.create()
        engine.answer(self.explore(store, engine))
        engine.advance()
        request = self.request(store)
        response = dict(request_id=request["request_id"], answer=[{
            "statement": "Citation chasing discovers studies by following references.",
            "evidence": [{"url": "https://new.example/paper",
                          "quote": "Citation chasing follows references to discover additional studies."}]}],
            unanswered="none", limitations="synthetic fixture", next_experiment="a bounded live trial")
        return store, engine, request, response

    def test_theme_generates_search_seeds_without_source_urls(self):
        config = validate_config(topic_config("未知 情報", ["duckduckgo", "crossref"]))
        self.assertEqual(len(config["seeds"]), 2)
        self.assertIn("query=", config["seeds"][1])
        config["seeds"][0] = "https://unobserved.example/"
        with self.assertRaises(LabError):
            validate_config(config)
        with self.assertRaises(LabError):
            validate_config(topic_config("topic", ["duckduckgo"], max_origins=0))

    def test_external_source_discovered_and_strict_scope_kept(self):
        store, engine = self.create()
        engine.answer(self.explore(store, engine))
        engine.advance()
        self.assertIn("https://new.example/paper", [p["url"] for p in store.state["arms"]["luna"]["pages"]])
        strict = {"allowed_origins": ["https://html.duckduckgo.com"]}
        self.assertFalse(in_scope(strict, "https://new.example/paper"))
        for url in ("https://127.0.0.1/", "https://localhost/", "https://db.internal/", "https://10.0.0.1/"):
            self.assertFalse(in_scope(store.state["config"], url))
        self.assertTrue(verify(store)["ok"])

    def test_free_html_providers_discover_external_sources(self):
        class FreeSearchFixture(SearchFixture):
            def get(self, url, cap, timeout, user_agent):
                if not url.endswith("/robots.txt") and urlsplit(url).hostname in ("mwmbl.org", "wiby.me"):
                    body = b'<title>Search results</title><a href="https://new.example/paper">Relevant source</a>'
                    return 200, {"content-type": "text/html; charset=utf-8"}, body, False
                return super().get(url, cap, timeout, user_agent)

        config = topic_config("citation chasing", ["mwmbl", "wiby"])
        config["limits"]["pages_per_arm"] = 3
        self.assertEqual(config["seeds"], ["https://mwmbl.org/search?q=citation+chasing", "https://wiby.me/?q=citation+chasing"])
        store = Store.create(self.run, config)
        engine = Engine(store, FreeSearchFixture())
        self.approve(store, engine)
        engine.advance()
        request = self.request(store)
        engine.answer(dict(request_id=request["request_id"], next_urls=["https://new.example/paper"],
                           stop=False, reason="follow free search results", claims=[], search_queries=[]))
        engine.advance()
        self.assertIn("https://new.example/paper", [p["url"] for p in store.state["arms"]["luna"]["pages"]])
        self.assertTrue(verify(store)["ok"])

    def test_origin_budget_counts_robots_and_survives_reload(self):
        config = topic_config("topic", ["duckduckgo"], max_origins=1)
        store = Store.create(self.run, config)
        Fetcher(store, SearchFixture()).page("luna", store.state["arms"]["luna"]["frontier"].pop())
        restored = Store(self.run)
        fetcher = Fetcher(restored, SearchFixture())
        with self.assertRaisesRegex(LabError, "origin_budget"):
            fetcher.http("luna", "https://new.example/robots.txt", "robots")
        self.assertEqual(len(restored.state["arms"]["luna"]["http"]), 2)
        self.assertTrue(verify(restored)["ok"])

    def test_query_expansion_is_bounded_and_not_model_invented_urls(self):
        store, engine = self.create()
        response = self.explore(store, engine)
        response.update(next_urls=[], search_queries=["citation chasing"])
        engine.answer(response)
        restored = Store(self.run)
        arm = restored.state["arms"]["luna"]
        self.assertEqual(arm["search_queries"][-1], "citation chasing")
        self.assertEqual(arm["frontier"][0]["url"], search_urls("citation chasing", ["duckduckgo"])[0])
        self.assertEqual(arm["frontier"][0]["depth"], 0)

    def test_duplicate_queries_rejected_without_consuming_query_budget(self):
        store, engine = self.create()
        response = self.explore(store, engine)
        response["search_queries"] = list(store.state["arms"]["luna"]["search_queries"])
        with self.assertRaisesRegex(LabError, "検索語"):
            engine.answer(response)
        self.assertEqual(len(store.state["arms"]["luna"]["search_queries"]), 1)

    def test_last_page_is_available_for_final_answer_and_checkpoint(self):
        store, engine, request, response = self.finish()
        self.assertEqual(request["kind"], "review")
        self.assertIn("https://new.example/paper", [o["url"] for o in request["context"]["observations"]])
        engine.answer(response)
        self.assertTrue(verify(store)["ok"])
        self.assertIn("https://new.example/paper", store.artifact("ANSWER.md").read_text())
        archive = self.root / "checkpoint.zip"
        export_checkpoint(store, archive)
        target = self.root / "restored"
        with zipfile.ZipFile(archive) as zipped:
            self.assertIn("ANSWER.md", zipped.namelist())
            zipped.extractall(target)
        self.assertTrue(verify(Store(target))["ok"])
        store.artifact("ANSWER.md").write_text("altered")
        self.assertFalse(verify(store)["ok"])

    def test_fabricated_final_quote_rejected(self):
        store, engine, _, response = self.finish()
        response["answer"][0]["evidence"][0]["quote"] = "This was never observed."
        with self.assertRaisesRegex(LabError, "引用"):
            engine.answer(response)
        self.assertNotIn("research_answer", store.state)

    def test_strict_scope_review_can_read_last_acquired_source(self):
        config = topic_config("read a known source", ["duckduckgo"])
        config.pop("discovery")
        config["seeds"] = [search_urls(f"source {i}", ["duckduckgo"])[0] for i in range(6)]
        config["limits"].update(pages_per_arm=6, max_depth=0)
        store = Store.create(self.run, config)
        engine = Engine(store, SearchFixture())
        self.approve(store, engine)
        engine.advance()
        request = self.request(store)
        self.assertEqual(request["kind"], "review")
        self.assertEqual(len(request["context"]["observations"]), 6)
        self.assertEqual(request["context"]["observations"][0]["url"], config["seeds"][0])
        self.assertIn("Unknown research source", request["context"]["observations"][0]["text"])
        self.assertNotIn("answer", request["response_format"])

    def test_missing_final_answer_or_evidence_fails_verification(self):
        store, engine, _, response = self.finish()
        engine.answer(response)
        saved_answer = store.state.pop("research_answer")
        report = verify(store)
        self.assertFalse(report["ok"])
        self.assertIn("answer artifact: research answer missing", report["errors"])
        store.state["research_answer"] = saved_answer
        store.state["claims"] = []
        self.assertFalse(verify(store)["ok"])

    def test_review_tail_preserves_requests_http_budget_and_quote_offsets(self):
        class TailFixture(SearchFixture):
            def get(self, url, *args):
                if url == "https://new.example/paper":
                    body = ('<title>Study</title><nav>' + 'navigation ' * 1000 +
                            '</nav><p>Relevant evidence near the end.</p>').encode()
                    return 200, {"content-type": "text/html"}, body, False
                return super().get(url, *args)

        store, _ = self.create()
        engine = Engine(store, TailFixture())
        engine.answer(self.explore(store, engine))
        engine.advance()
        original_path = store.state["active_request"]
        original = store.artifact(original_path).read_bytes()
        quote = "Relevant evidence near the end."
        self.assertNotIn(quote, self.request(store)["context"]["observations"][-1]["text"])
        charged = [(len(a["http"]), a["bytes_charged"]) for a in store.state["arms"].values()]
        engine.review_tail("read saved evidence beyond the navigation")
        request = self.request(store)
        self.assertGreater(request["context"]["observations"][-1]["text_offset"], 0)
        self.assertEqual(store.artifact(original_path).read_bytes(), original)
        self.assertEqual(charged, [(len(a["http"]), a["bytes_charged"]) for a in store.state["arms"].values()])
        engine.answer(dict(request_id=request["request_id"], answer=[{
            "statement": "The document contains relevant evidence.",
            "evidence": [{"url": "https://new.example/paper", "quote": quote}]}],
            unanswered="none", limitations="fixture", next_experiment="none"))
        self.assertTrue(verify(store)["ok"])

    def test_review_tail_keeps_request_budget(self):
        store, engine = self.create(agent_requests=4)
        with self.assertRaisesRegex(LabError, "review"):
            engine.review_tail("not yet reviewing")
        engine.answer(self.explore(store, engine))
        engine.advance()
        engine.review_tail("read the end")
        active = store.state["active_request"]
        with self.assertRaisesRegex(LabError, "agent_request_budget"):
            engine.review_tail("no further budget")
        self.assertEqual(store.state["active_request"], active)
        self.assertEqual(len(store.state["agent_requests"]), 4)

    def test_all_seed_retry_preserves_budget_and_multiple_reviews_verify(self):
        class NetworkFixture(SearchFixture):
            available = False

            def get(self, *args):
                if not self.available:
                    raise LabError("ProxyError")
                return super().get(*args)

        config = topic_config("topic", ["duckduckgo"], ["query one", "query two"])
        config["limits"]["pages_per_arm"] = 2
        store = Store.create(self.run, config)
        transport = NetworkFixture()
        engine = Engine(store, transport)
        self.approve(store, engine)
        engine.advance()
        def empty_review():
            request = self.request(store)
            return dict(request_id=request["request_id"], answer=[], unanswered="unverified",
                        limitations="fixture", next_experiment="same-run recovery")
        engine.answer(empty_review())
        charged = store.state["arms"]["luna"]["bytes_charged"]
        engine.retry_failed("same-run fixture recovery", all_seeds=True)
        self.assertEqual(len(store.state["arms"]["luna"]["frontier"]), 2)
        self.assertEqual(store.state["arms"]["luna"]["bytes_charged"], charged)
        transport.available = True
        engine.advance()
        engine.answer(empty_review())
        self.assertTrue(verify(store)["ok"])
        self.assertEqual(len([r for r in store.state["agent_requests"] if r["kind"] == "review"]), 2)
        self.assertGreater(store.state["arms"]["luna"]["bytes_charged"], charged)

    def test_crossref_metadata_and_search_redirects_are_replayed(self):
        body = json.dumps({"message": {"items": [{"title": ["Focused crawling"], "DOI": "10.example/paper", "URL": "https://doi.org/10.example/paper"}]}}).encode()
        title, text, links = parse_page(body, {"content-type": "application/json"}, "https://api.crossref.org/works?query=crawling", discovery=True)
        self.assertIn("not full text", text)
        self.assertEqual(links[0]["url"], "https://doi.org/10.example/paper")
        _, _, links = parse_page(b'<a href="/l/?uddg=https%3A%2F%2Fnew.example%2Fpaper">result</a>',
                                {"content-type": "text/html"}, "https://html.duckduckgo.com/html/?q=topic", discovery=True)
        self.assertEqual(links[0]["url"], "https://new.example/paper")
        self.assertIn("search_queries", answer_schema("explore", discovery=True)["properties"])
        self.assertIn("answer", answer_schema("review", discovery=True)["properties"])

    def test_crossref_http_request_accepts_json(self):
        with patch("urllib.request.getproxies", return_value={}):
            transport = LiveTransport()
        def inspect(request, **kwargs):
            self.assertEqual(request.get_header("Accept"), "application/json")
            raise LabError("request_inspected")
        with patch.object(transport.opener, "open", side_effect=inspect):
            with self.assertRaisesRegex(LabError, "request_inspected"):
                transport.get("https://api.crossref.org/works?query=topic", 1000, 1, "DiscoveryLab/0.1 (test)")

    def test_metadata_results_are_not_counted_as_html_or_paper_full_text(self):
        class MetadataFixture(SearchFixture):
            def get(self, url, *args):
                if url.endswith("robots.txt"):
                    return super().get(url, *args)
                return 200, {"content-type": "application/json"}, b'{"message":{"items":[]}}', False
        store = Store.create(self.run, topic_config("topic", ["crossref"]))
        arm = store.state["arms"]["luna"]
        Fetcher(store, MetadataFixture()).page("luna", arm["frontier"].pop())
        report = verify(store)
        self.assertTrue(report["ok"])
        self.assertEqual(report["arms"]["luna"]["documents_acquired"], 1)
        self.assertEqual(report["arms"]["luna"]["metadata_documents"], 1)
        self.assertEqual(report["arms"]["luna"]["html_pages"], 0)


if __name__ == "__main__":
    unittest.main()
