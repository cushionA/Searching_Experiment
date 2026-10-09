"""Pure normalization and evidence-availability checks for GLiNER SKU extraction."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "sku-matching"
spec = importlib.util.spec_from_file_location(
    "sku_gliner_extraction_test", EXPERIMENT / "evaluate_extraction.py"
)
assert spec and spec.loader
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)
backend_spec = importlib.util.spec_from_file_location(
    "sku_gliner_backend_test", EXPERIMENT / "backend_gliner_extract.py"
)
assert backend_spec and backend_spec.loader
backend = importlib.util.module_from_spec(backend_spec)
backend_spec.loader.exec_module(backend)


def spans(*values: str) -> list[dict[str, str]]:
    return [{"text": value, "source_text": value} for value in values]


class GLiNERExtractionNormalizationTests(unittest.TestCase):
    def test_lace_requires_explicit_selected_option(self):
        self.assertIsNone(evaluation._normalize_prediction("lace", spans("レースカーテンセット")))
        self.assertIsNone(evaluation._normalize_prediction("lace", spans("sheer curtain")))
        self.assertIs(evaluation._normalize_prediction("lace", spans("レースあり")), True)
        self.assertIs(evaluation._normalize_prediction("lace", spans("レース付き")), True)
        self.assertIs(evaluation._normalize_prediction("lace", spans("レースなし")), False)
        self.assertIs(evaluation._normalize_prediction("lace", spans("without lace")), False)
        self.assertIs(evaluation._normalize_prediction("lace", spans("with lace")), True)
        self.assertIs(evaluation._normalize_prediction("lace", spans("no lace")), False)
        self.assertIs(evaluation._normalize_prediction("lace", spans("not included")), False)
        self.assertIsNone(evaluation._normalize_prediction("lace", spans("レース有無")))

    def test_conflicting_lace_states_abstain_and_flag_ambiguity(self):
        value, ambiguous = evaluation._normalize_prediction_details(
            "lace", spans("レースあり", "レースなし")
        )
        self.assertIsNone(value)
        self.assertTrue(ambiguous)
        value, ambiguous = evaluation._normalize_prediction_details("lace", spans("レースあり / なし"))
        self.assertIsNone(value)
        self.assertTrue(ambiguous)

    def test_width_height_use_selected_pair_components(self):
        evidence = spans("幅100×丈178cm")
        self.assertEqual(evaluation._normalize_prediction("width_cm", evidence), 100.0)
        self.assertEqual(evaluation._normalize_prediction("height_cm", evidence), 178.0)

    def test_conflicting_series_size_and_piece_values_abstain(self):
        for field, evidence in (
            ("width_cm", spans("幅100", "幅150")),
            ("height_cm", spans("丈178cm", "丈200cm")),
            ("pieces", spans("2枚組", "4枚組")),
        ):
            with self.subTest(field=field):
                value, ambiguous = evaluation._normalize_prediction_details(field, evidence)
                self.assertIsNone(value)
                self.assertTrue(ambiguous)

    def test_piece_count_does_not_choose_among_multiple_numeric_mentions(self):
        value, ambiguous = evaluation._normalize_prediction_details(
            "pieces", spans("2枚組", "2枚組")
        )
        self.assertIsNone(value)
        self.assertTrue(ambiguous)

    def test_metadata_not_in_literal_sku_label_is_not_required_extraction(self):
        item = {
            "product_title": "カーテン 4枚セット レースカーテンセット",
            "sku_label": "幅100×丈178cm / ベージュ",
        }
        self.assertEqual(
            evaluation._literal_sku_label_visibility(item, "lace", True),
            "not_in_literal_sku_label",
        )
        self.assertEqual(
            evaluation._literal_sku_label_visibility(item, "pieces", 4),
            "not_in_literal_sku_label",
        )
        self.assertEqual(
            evaluation._literal_sku_label_visibility(item, "width_cm", 100),
            "visible",
        )

    def test_explicit_lace_option_is_visible(self):
        item = {"sku_label": "幅100×丈178cm / ベージュ / レースあり"}
        self.assertEqual(evaluation._literal_sku_label_visibility(item, "lace", True), "visible")
        item["sku_label"] = "幅100×丈178cm / ベージュ / レースなし"
        self.assertEqual(evaluation._literal_sku_label_visibility(item, "lace", False), "visible")

    def test_manifest_hashes_and_schema_labels_match_runtime_constants(self):
        manifest = __import__("json").loads(
            (EXPERIMENT / "manifests" / "gliner-extract.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["repo"], backend.REPO)
        self.assertEqual(manifest["revision"], backend.REVISION)
        self.assertEqual(manifest["files"], backend.FILES)
        self.assertEqual(manifest["evaluation_design"]["schema_labels"], backend.ENTITY_DESCRIPTIONS)


if __name__ == "__main__":
    unittest.main()
