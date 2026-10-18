from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "experiments" / "sku-matching" / "prepare_axis_label_pairs_v1.py"
SPEC = importlib.util.spec_from_file_location("prepare_axis_label_pairs_v1", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class AxisLabelPairPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.products = self.root / "products.jsonl"
        self.cases = self.root / "cases.jsonl"
        self.membership = self.root / "membership.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def write_rows(self, path: Path, rows):
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    def fixture_rows(self, *, unstable_au=False, unstable_rak=False, missing_label=False):
        first_label = "" if missing_label else "色"
        product = {
            "dossier_id": "pair-1",
            "au_product": {"product_id": "au-1", "title": "must-not-leak"},
            "au_rows": [
                {"axes": [
                    {"axis_name": first_label, "axis_name_span": {"quote": first_label, "locator": "/x", "raw_file": "raw.json", "sha256": "a" * 64, "start": 1, "end": 2}, "value": "red"},
                    {"axis_name": "色", "axis_name_span": {"quote": "色"}, "value": "blue"},
                ]},
                {"axes": [
                    {"axis_name": "形" if unstable_au else first_label, "axis_name_span": {"quote": "形" if unstable_au else first_label}, "value": "green"},
                    {"axis_name": "色", "axis_name_span": {"quote": "色"}, "value": "black"},
                ]},
            ],
            "color_vocab": ["must-not-leak"],
        }

        def case(case_id: str, rak_label: str):
            return {
                "case_id": case_id,
                "dossier_id": "pair-1",
                "au_product_id": "au-1",
                "split": "exploration",
                "rakuten_selected": {"axes": [
                    {"axis_index": 0, "axis_key": "axis-color", "axis_label": rak_label, "axis_label_span": {"quote": rak_label}, "value": "red"},
                    {"axis_index": 1, "axis_key": "axis-color-2", "axis_label": "色", "axis_label_span": {"quote": "色"}, "value": "blue"},
                ]},
                "gold": "must-not-leak",
            }

        return [product], [case("case-1", "色"), case("case-2", "形" if unstable_rak else "色")], [
            {"case_id": "case-1", "dossier_id": "pair-1"},
        ]

    def prepare_fixture(self, **kwargs):
        products, cases, membership = self.fixture_rows(**kwargs)
        self.write_rows(self.products, products)
        self.write_rows(self.cases, cases)
        self.write_rows(self.membership, membership)
        return module.prepare_pairs(self.products, self.cases, self.membership)

    def test_source_only_output_preserves_duplicate_label_positions_and_provenance(self):
        rows, provenance = self.prepare_fixture()
        self.assertEqual(len(rows), 1)
        pair = rows[0]
        self.assertEqual(pair["pair_id"], "pair-1")
        self.assertEqual([axis["axis_id"] for axis in pair["au_axes"]], ["au-position-0", "au-position-1"])
        self.assertEqual([axis["label"] for axis in pair["au_axes"]], ["色", "色"])
        self.assertEqual([axis["axis_id"] for axis in pair["rakuten_axes"]], [
            "rakuten-index-0-key-axis-color", "rakuten-index-1-key-axis-color-2",
        ])
        self.assertTrue(pair["selected_dev_subset"])
        self.assertEqual(pair["source_case_count"], 2)
        self.assertEqual(pair["source_case_ids"], ["case-1", "case-2"])
        self.assertEqual(pair["source_splits"], ["exploration"])
        self.assertEqual(pair["au_axes"][0]["source_ref"]["json_pointer"], "/au_rows/0/axes/0/axis_name")
        self.assertEqual(pair["au_axes"][0]["source_ref"]["label_quote"], "色")
        self.assertEqual(pair["au_axes"][0]["source_ref"]["line_number"], 1)
        self.assertEqual(provenance["selected_subset_source_split_values"], ["exploration"])
        self.assertFalse(provenance["selected_subset_is_independent_validation"])

        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield key, child
                    yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)

        payload = {"pairs": rows, "provenance": provenance}
        serialized_keys = {key for key, _ in walk(payload)}
        self.assertTrue(serialized_keys.isdisjoint({"value", "body", "title", "description", "gold", "family_values"}))
        serialized_values = [value for _, value in walk(payload)]
        for forbidden in ("must-not-leak", "red", "blue", "green", "black", "gold"):
            self.assertNotIn(forbidden, serialized_values)

    def test_blank_source_label_is_preserved_and_counted(self):
        rows, provenance = self.prepare_fixture(missing_label=True)
        self.assertEqual(rows[0]["au_axes"][0]["label"], "")
        self.assertEqual(rows[0]["au_axes"][0]["source_ref"]["label_quote"], "")
        self.assertEqual(provenance["missing_au_axis_label_count"], 1)

    def test_unstable_au_axis_order_is_reported(self):
        products, cases, membership = self.fixture_rows(unstable_au=True)
        self.write_rows(self.products, products)
        self.write_rows(self.cases, cases)
        self.write_rows(self.membership, membership)
        with self.assertRaisesRegex(ValueError, "AU axis label/order differs"):
            module.prepare_pairs(self.products, self.cases, self.membership)

    def test_unstable_rakuten_identity_or_label_is_reported(self):
        products, cases, membership = self.fixture_rows(unstable_rak=True)
        self.write_rows(self.products, products)
        self.write_rows(self.cases, cases)
        self.write_rows(self.membership, membership)
        with self.assertRaisesRegex(ValueError, "Rakuten axis identity/labels differ"):
            module.prepare_pairs(self.products, self.cases, self.membership)

    def test_present_span_quote_must_match_source_axis_label(self):
        products, cases, membership = self.fixture_rows()
        products[0]["au_rows"][0]["axes"][0]["axis_name_span"]["quote"] = "違うラベル"
        self.write_rows(self.products, products)
        self.write_rows(self.cases, cases)
        self.write_rows(self.membership, membership)
        with self.assertRaisesRegex(ValueError, "source span quote differs from axis_name"):
            module.prepare_pairs(self.products, self.cases, self.membership)

    def test_missing_null_and_non_string_axis_labels_are_rejected(self):
        for side in ("au", "rakuten"):
            for invalid in ("missing", None, 17):
                with self.subTest(side=side, invalid=invalid):
                    products, cases, membership = self.fixture_rows()
                    if side == "au":
                        axis = products[0]["au_rows"][0]["axes"][0]
                        field = "axis_name"
                    else:
                        axis = cases[0]["rakuten_selected"]["axes"][0]
                        field = "axis_label"
                    if invalid == "missing":
                        axis.pop(field)
                        expected_error = "missing axis label field"
                    else:
                        axis[field] = invalid
                        expected_error = "axis label field must be a string"
                    self.write_rows(self.products, products)
                    self.write_rows(self.cases, cases)
                    self.write_rows(self.membership, membership)
                    with self.assertRaisesRegex(ValueError, expected_error):
                        module.prepare_pairs(self.products, self.cases, self.membership)

    def test_duplicate_case_id_is_rejected(self):
        products, cases, membership = self.fixture_rows()
        cases.append(dict(cases[0]))
        self.write_rows(self.products, products)
        self.write_rows(self.cases, cases)
        self.write_rows(self.membership, membership)
        with self.assertRaisesRegex(ValueError, "duplicate case_id"):
            module.prepare_pairs(self.products, self.cases, self.membership)

    def test_case_to_au_product_binding_is_validated(self):
        products, cases, membership = self.fixture_rows()
        cases[0]["au_product_id"] = "different-au-product"
        self.write_rows(self.products, products)
        self.write_rows(self.cases, cases)
        self.write_rows(self.membership, membership)
        with self.assertRaisesRegex(ValueError, "case AU product binding differs"):
            module.prepare_pairs(self.products, self.cases, self.membership)

    def test_existing_output_directory_is_refused(self):
        output = self.root / "existing"
        output.mkdir()
        with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
            module.main([
                "--products", str(self.products), "--cases", str(self.cases),
                "--membership", str(self.membership), "--output-dir", str(output),
            ])


if __name__ == "__main__":
    unittest.main()
