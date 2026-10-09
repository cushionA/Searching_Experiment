"""Behavioral checks for the SKU experiment's conservative matching boundary."""
from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "sku-matching"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


matcher = load_module(EXPERIMENT / "match_skus.py", "sku_matcher_test")
generator = load_module(EXPERIMENT / "generate_samples.py", "sku_generator_test")


def sku(site, *, color="ベージュ", width=100, height=80, lace=True, pieces=4,
        label=None, available=True, price=5000, title="カーテン 4枚セット レースカーテン"):
    label = label or f"幅{width}×丈{height}cm({pieces}枚組) / {color} / {'あり' if lace else 'なし'}"
    return {"sku_id": f"{site}-sku", "product_id": f"{site}-product", "product_title": title,
            "sku_label": label, "url": f"https://example.test/{site}", "price": price,
            "available": available,
            "attributes": {"width_cm": width, "height_cm": height, "color": color,
                           "lace": lace, "pieces": pieces}}


def pair(au_rows, rakuten_rows):
    return {"pair_id": "test-pair", "au": au_rows, "rakuten": rakuten_rows,
            "color_aliases": {"ベージュ": "ベージュ", "サンドベージュ": "サンドベージュ"}}


class PerfectSimilarityModel:
    """Every candidate ties at 1.0, making canonical safeguards observable."""
    class Vector:
        def __matmul__(self, other):
            return 1.0

    def encode(self, texts, batch_size=32):
        return [self.Vector() for _ in texts]


class SkuMatchingTests(unittest.TestCase):
    def test_export_keeps_only_complete_equal_available_single_price_skus(self):
        example = EXPERIMENT / "sku-matching" / "sample.example.jsonl"
        if not example.exists():
            example = EXPERIMENT / "sample.example.jsonl"
        source_before = example.read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "result"
            summary = matcher.export_matches(example, out)
            with (out / "matched.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            decisions = [json.loads(line) for line in (out / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(example.read_bytes(), source_before)
        self.assertEqual(summary["counts"]["output_rows"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rakuten_sku_id"], "synthetic-rakuten-001-01")
        self.assertEqual(rows[0]["au_sku_id"], "synthetic-au-001-01")
        self.assertTrue(any(d["status"] == "unmatched" for d in decisions))

    def test_four_piece_product_title_does_not_supply_sku_lace_or_override_verified_count(self):
        row = sku("r", lace=True, pieces=2,
                  label="幅100×丈80cm / ベージュ / あり",
                  title="カーテン 4枚セット 遮光 レースカーテンセット")
        normalized = matcher.normalize_sku(row)
        self.assertTrue(normalized["canonical"]["lace"])
        self.assertEqual(normalized["canonical"]["pieces"], 2)
        row["attributes"]["lace"] = None
        row["sku_label"] = "幅100×丈80cm / ベージュ"
        normalized = matcher.normalize_sku(row)
        self.assertIsNone(normalized["canonical"]["lace"])
        self.assertIn("lace", normalized["missing"])

    def test_size_color_lace_and_piece_near_misses_do_not_match(self):
        au = sku("a")
        negatives = [
            sku("r", height=180),
            sku("r", color="サンドベージュ"),
            sku("r", lace=False, pieces=2),
            sku("r", pieces=2),
        ]
        for candidate in negatives:
            with self.subTest(label=candidate["sku_label"]):
                matched, audit = matcher.match_pair(pair([au], [candidate]))
                self.assertEqual(matched, [])
                self.assertIn(audit[0]["status"], ("unmatched", "review"))

    def test_duplicate_semantic_skus_require_review_instead_of_arbitrary_match(self):
        au = sku("a")
        rakuten = sku("r")
        duplicate = dict(rakuten, sku_id="r-sku-2")
        matched, audit = matcher.match_pair(pair([au], [rakuten, duplicate]))
        self.assertEqual(matched, [])
        self.assertEqual([d["reason"] for d in audit[:2]],
                         ["ambiguous_rakuten_skus", "ambiguous_rakuten_skus"])

    def test_ambiguous_au_skus_require_review(self):
        a1, a2 = sku("a1"), sku("a2")
        r = sku("r")
        matched, audit = matcher.match_pair(pair([a1, a2], [r]))
        self.assertEqual(matched, [])
        self.assertEqual(audit[0]["reason"], "ambiguous_au_skus")

    def test_generator_yields_153_by_306_and_independent_balanced_eval_without_overwrite(self):
        family = generator.make_family(1, __import__("random").Random(4))
        self.assertEqual(len(family["au"]), 153)
        self.assertEqual(len(family["rakuten"]), 306)
        self.assertTrue(all(row["provenance"]["price"] == "SYNTHETIC" for row in family["rakuten"]))
        self.assertTrue(all(row["provenance"]["availability"] == "SYNTHETIC" for row in family["rakuten"]))
        self.assertTrue(all(row["provenance"]["sku_label"] == "OBSERVED_OPTION_FORMAT_RECONSTRUCTED_COMBINATIONS"
                            for row in family["au"]))
        self.assertTrue(all(row["attributes"]["lace"] is True for row in family["au"]))
        self.assertTrue(any(row["attributes"]["lace"] is False for row in family["rakuten"]))
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "generated"
            generator.generate(output, pairs=1, seed=7)
            line = (output / "dataset.jsonl").read_text(encoding="utf-8").splitlines()[0]
            data = json.loads(line)
            evaluation = json.loads((output / "evaluation.json").read_text(encoding="utf-8"))
            self.assertEqual(data["pair_id"], "synthetic-product-pair-000001")
            self.assertEqual(evaluation["count"], 1000)
            self.assertEqual(len(evaluation["cases"]), 1000)
            self.assertEqual(sum(c["expected_match"] for c in evaluation["cases"]), 500)
            self.assertTrue(all(c["expected_decision"] == ("matched" if c["expected_match"] else
                                "review" if c["scenario"] == "negative_missing_lace_evidence" else "unmatched")
                                for c in evaluation["cases"]))
            self.assertEqual({c["scenario"] for c in evaluation["cases"]} - {"positive_format_variation"},
                             {"negative_height_near_miss", "negative_near_color", "negative_lace_presence",
                              "negative_width_height_swap", "negative_width_piece_count",
                              "negative_missing_lace_evidence"})
            with self.assertRaises(FileExistsError):
                generator.generate(output, pairs=1, seed=7)

    def test_generated_control_families_reach_exclusion_or_review_gates(self):
        expected = {
            1: ("excluded", "unavailable"),
            2: ("review", "price_not_single_integer"),
            3: ("review", "unknown_option"),
            4: ("review", "missing_attributes:lace"),
            5: ("review", "ambiguous_rakuten_skus"),
        }
        for family_number, decision in expected.items():
            with self.subTest(family=family_number):
                family = generator.make_family(family_number, __import__("random").Random(20))
                _, audit = matcher.match_pair(family)
                control = next(row for row in audit if row["sku_id"] == family["rakuten"][0]["sku_id"])
                self.assertEqual((control["status"], control["reason"]), decision)

    def test_perfect_embedding_similarity_cannot_override_size_or_lace_conflicts(self):
        model = PerfectSimilarityModel()
        for candidate in (sku("r", height=180), sku("r", lace=False, pieces=2)):
            with self.subTest(label=candidate["sku_label"]):
                matched, audit = matcher.match_pair(pair([sku("a")], [candidate]), model=model)
                self.assertEqual(matched, [])
                self.assertEqual(audit[0]["status"], "unmatched")

    def test_price_proximity_and_perfect_similarity_never_accept_incomplete_au_skus(self):
        expensive = sku("a-expensive", price=9000, label="幅100×丈80cm / ベージュ / あり")
        close = sku("a-close", price=2010, label="幅100×丈80cm / ベージュ / あり")
        expensive["attributes"]["pieces"] = None
        close["attributes"]["pieces"] = None
        rakuten = sku("r", price=2020)
        matched, audit = matcher.match_pair(pair([expensive, close], [rakuten]), model=PerfectSimilarityModel())
        self.assertEqual(matched, [])
        self.assertEqual(audit[0]["status"], "review")
        self.assertEqual(audit[0]["reason"], "au_attributes_incomplete")
        # Both vectors tie, so the expensive first candidate stays first; price is not a match rule.
        self.assertEqual(audit[0]["model_candidates"][0]["au_sku_id"], "a-expensive-sku")


if __name__ == "__main__":
    unittest.main()
