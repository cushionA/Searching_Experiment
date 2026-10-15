"""Boundary tests for conservative, source-preserving classical SKU quantities."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = next(parent for parent in (HERE, *HERE.parents)
            if (parent / "experiments/sku-matching").is_dir())
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))

try:
    import sku_classical_quantity_v1 as quantity
except ModuleNotFoundError as error:
    if error.name in {"jaconv", "pint"}:
        quantity = None
    else:
        raise


@unittest.skipIf(quantity is None, "install jaconv and Pint in the dedicated venv")
class ClassicalQuantityParserTests(unittest.TestCase):
    def assert_exact_token_spans(self, raw):
        parsed = quantity.parse_value(raw)
        covered = set()
        previous_start = -1
        for token in parsed["tokens"]:
            start, end = token["span"]
            self.assertGreaterEqual(start, previous_start)
            self.assertGreaterEqual(start, 0)
            self.assertGreater(end, start)
            self.assertLessEqual(end, len(raw))
            self.assertEqual(token["quote"], raw[start:end])
            covered.update(range(start, end))
            previous_start = start
        self.assertEqual({i for i, char in enumerate(raw) if not char.isspace()} - covered, set())
        return parsed

    def test_polarity_suffixes_keep_their_original_source_spans(self):
        lace = self.assert_exact_token_spans("レースあり")
        bare_negative = self.assert_exact_token_spans("なし")
        accessory_negative = self.assert_exact_token_spans("付属なし")
        self.assertEqual([(t["kind"], t.get("polarity")) for t in lace["tokens"]],
                         [("word", None), ("polarity", "positive")])
        self.assertEqual(bare_negative["tokens"][0]["polarity"], "negative")
        self.assertEqual(accessory_negative["tokens"][-1]["polarity"], "negative")
        self.assertEqual(quantity.compare_values("レースあり", "レースなし")["state"], "conflict")

    def test_bound_and_opaque_symbol_tokens_keep_literal_spans(self):
        bounded = self.assert_exact_token_spans("100cm以下")
        celsius_offset = self.assert_exact_token_spans("℃ offset")
        self.assertEqual(bounded["tokens"][-1]["limit"], "at_most")
        self.assertEqual(celsius_offset["tokens"][0]["kind"], "opaque")
        self.assertEqual(celsius_offset["tokens"][0]["quote"], "℃")
        self.assertEqual(quantity.compare_values("100cm以下", "90cm以下")["state"], "unknown")

    def test_role_labeled_multiplication_does_not_lose_the_first_dimension(self):
        parsed = self.assert_exact_token_spans("幅100×丈80cm")
        quantities = [token for token in parsed["tokens"] if token["kind"] == "quantity"]
        self.assertEqual(len(quantities), 2)
        self.assertEqual([token["role"] for token in quantities], ["幅", "丈"])
        self.assertEqual([token["value"] for token in quantities], ["100", "80"])

    def test_embedded_unit_lookalikes_are_not_parsed_as_physical_measurements(self):
        for raw in ("101mmfoo", "2m3"):
            with self.subTest(raw=raw):
                parsed = self.assert_exact_token_spans(raw)
                self.assertFalse(any(token.get("quantity_kind") == "physical"
                                     for token in parsed["tokens"]))

    def test_unknown_counter_suffix_is_preserved_without_inventing_a_bag_count(self):
        parsed = self.assert_exact_token_spans("250g×2P")
        self.assertTrue(any(token["kind"] == "word" and token["text"] == "P"
                            for token in parsed["tokens"]))
        self.assertFalse(any(token.get("counter") == "袋" for token in parsed["tokens"]))
        self.assertNotEqual(quantity.compare_values("250g×2P", "500g×1P")["state"], "support")

    def test_equivalent_length_units_share_a_canonical_signature(self):
        signatures = [quantity.canonical_signature(value)
                      for value in ("100cm", "1m", "1000mm")]
        self.assertEqual(signatures[0], signatures[1])
        self.assertEqual(signatures[1], signatures[2])
        self.assertEqual(quantity.compare_values("100cm", "1m")["state"], "support")
        self.assertEqual(quantity.compare_values("1m", "1000mm")["state"], "support")

    def test_dimension_order_is_preserved_and_swapped_dimensions_conflict(self):
        ordered = quantity.compare_values("100cm×80cm", "80cm×100cm")
        self.assertEqual(ordered["state"], "conflict")
        self.assertNotEqual(quantity.canonical_signature("100cm×80cm"),
                            quantity.canonical_signature("80cm×100cm"))

    def test_package_mass_and_bag_count_are_not_collapsed_to_total_mass(self):
        result = quantity.compare_values("250g×2袋", "500g×1袋")
        self.assertNotEqual(result["state"], "support")

    def test_unit_case_is_not_folded(self):
        upper = quantity.canonical_signature("1M")
        lower = quantity.canonical_signature("1m")
        self.assertNotEqual(upper, lower)
        self.assertNotEqual(quantity.compare_values("1M", "1m")["state"], "support")

    def test_ambiguous_single_symbol_units_do_not_create_cross_unit_support(self):
        for left, right in (("6W", "0.006kW"), ("3L", "3000ml")):
            with self.subTest(left=left, right=right):
                self.assertEqual(quantity.compare_values(left, right)["state"], "unknown")
        # Explicit length units remain safe point conversions.
        self.assertEqual(quantity.compare_values("100cm", "1m")["state"], "support")

    def test_literal_x_and_multiplication_sign_do_not_alias_without_numbers(self):
        for left, right in (("XL", "×L"), ("X", "×")):
            with self.subTest(left=left, right=right):
                self.assertNotEqual(quantity.compare_values(left, right)["state"], "support")
                self.assertNotEqual(quantity.canonical_signature(left),
                                    quantity.canonical_signature(right))

    def test_ascii_x_and_multiplication_sign_match_in_a_numeric_expression(self):
        left, right = "100x80cm", "100×80cm"
        self.assertEqual(quantity.canonical_signature(left), quantity.canonical_signature(right))
        self.assertEqual(quantity.compare_values(left, right)["state"], "support")

    def test_approximation_ranges_tolerance_and_limits_do_not_become_exact_conflicts(self):
        comparisons = (
            ("約100cm", "約110cm"),
            ("100〜120cm", "110〜130cm"),
            ("100±5cm", "100±6cm"),
            ("100cm以上", "110cm以上"),
            ("100cm以下", "90cm以下"),
        )
        for left, right in comparisons:
            with self.subTest(left=left, right=right):
                self.assertEqual(quantity.compare_values(left, right)["state"], "unknown")

    def test_color_words_remain_literal_without_aliasing_blue_and_light_blue(self):
        result = quantity.compare_values("青", "水色")
        self.assertEqual(result["state"], "unknown")
        self.assertNotEqual(quantity.canonical_signature("青"),
                            quantity.canonical_signature("水色"))

    def test_unknown_words_and_opaque_symbols_are_not_discarded(self):
        left = quantity.parse_value("青✨ 100cm")
        right = quantity.parse_value("青 100cm")
        self.assertIn("✨", left["opaque_tokens"])
        self.assertEqual(quantity.compare_values(left, right)["state"], "unknown")
        self.assertNotEqual(quantity.canonical_signature(left),
                            quantity.canonical_signature(right))

    def test_fullwidth_normalization_keeps_exact_unicode_source_offsets(self):
        raw = "🧵幅１００×８０ｃｍ（特別）"
        parsed = quantity.parse_value(raw)
        self.assertIn("幅100×80cm", parsed["normalized_text"])
        self.assertEqual(len(parsed["normalized_to_source_offsets"]),
                         len(parsed["normalized_text"]))
        covered = set()
        for token in parsed["tokens"]:
            start, end = token["span"]
            self.assertGreaterEqual(start, 0)
            self.assertGreater(end, start)
            self.assertLessEqual(end, len(raw))
            self.assertEqual(token["quote"], raw[start:end])
            covered.update(range(start, end))
        self.assertIn("🧵", parsed["opaque_tokens"])
        # Only formatting whitespace may remain un-tokenized; source content
        # such as emoji, fullwidth symbols and suffix words must stay represented.
        self.assertEqual({i for i, char in enumerate(raw) if not char.isspace()} - covered, set())

    def test_width_and_height_roles_prevent_cross_axis_support(self):
        width = quantity.parse_value("幅100cm")
        height = quantity.parse_value("高さ100cm")
        width_quantity = next(t for t in width["tokens"] if t["kind"] == "quantity")
        height_quantity = next(t for t in height["tokens"] if t["kind"] == "quantity")
        self.assertEqual(width_quantity["role"], "幅")
        self.assertEqual(height_quantity["role"], "高さ")
        self.assertEqual(quantity.compare_values(width, height)["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
