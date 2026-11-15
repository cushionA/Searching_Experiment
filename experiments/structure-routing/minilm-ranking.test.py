import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("minilm-ranking.py")
FIXTURE = """<!doctype html>
<html><body>
  <nav class="categories"><ul>
    <li class="category"><a href="/catalog/books">Books</a></li>
    <li class="category"><a href="/catalog/games">Games</a></li>
  </ul></nav>
  <main><section class="products">
    <article class="product-card"><a href="/product/green-book">Green Book</a></article>
    <article class="product-card"><a href="/product/blue-book">Blue Book</a></article>
  </section></main>
</body></html>"""


class MiniLMRankingFixtureTests(unittest.TestCase):
    def test_real_model_ranks_fixture_dom_without_claiming_quality(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = Path(temp_dir) / "fixture.html"
            html_path.write_text(FIXTURE, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--html", str(html_path)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "ok")
        self.assertEqual(output["model"]["repo_id"], "McGill-NLP/MiniLM-L6-dmr")
        self.assertEqual(output["model"]["revision"], "e9b9dd566fa1a146eb00b8f3230b87870144084a")
        self.assertEqual(output["candidate_count"], 4)
        self.assertEqual(output["group_count"], 2)
        self.assertGreater(output["metrics"]["forward_calls"], 0)
        self.assertEqual(output["metrics"]["forward_calls"], output["metrics"]["batches"])
        self.assertGreater(output["metrics"]["input_token_total"], 0)
        self.assertLessEqual(output["metrics"]["input_token_max"], output["metrics"]["max_sequence_length"])
        self.assertEqual(output["metrics"]["truncation_count"], 0)
        self.assertEqual(output["metrics"]["bbox_missing_candidates"], 4)
        self.assertFalse(output["heuristics_added"])
        self.assertEqual(output["external_api_calls"], 0)
        self.assertEqual(output["representative_dom_rankings"], 1)
        self.assertEqual(output["future_page_model_calls"], 0)
        print("MINILM_FIXTURE_RESULT=" + json.dumps({
            "model_revision": output["model"]["revision"],
            "model_license": output["model"]["license"],
            "metrics": output["metrics"],
            "ranking": output["ranking"],
            "frozen_rule": output["frozen_rule"],
            "external_api_calls": output["external_api_calls"],
            "future_page_model_calls": output["future_page_model_calls"],
        }, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
