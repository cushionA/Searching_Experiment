from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "sku-matching"))
import sku_literal_family_proof_v1 as proof  # noqa: E402
sys.path.insert(0, str(ROOT / "experiments" / "sku-matching" / "claude_v9_gate"))
import sku_gate_sources as sources  # noqa: E402


class LiteralFamilyProofTests(unittest.TestCase):
    def make_fixture(self, au_value="Red", au_family=None, duplicate_axis=False):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        family = ["Red", "Blue"]
        au_family = family if au_family is None else au_family
        selectors = [{"key": "color", "label": "Color", "values": [{"value": v} for v in family]}]
        html = '<html><script>{"variantSelectors":' + json.dumps(selectors, separators=(",", ":")) + '}</script></html>'
        (root / "rakuten.html").write_text(html, encoding="utf-8")
        names = {"row": "Color"}
        sku = {"optionName": names, "rowNames": au_family}
        if duplicate_axis:
            sku["optionName"]["column"] = "Color"
            sku["columnNames"] = au_family
        au_obj = {"itemInfo": {"skuInfo": sku}}
        (root / "au.json").write_text(json.dumps(au_obj), encoding="utf-8")
        store = sources.RawStore(root)
        r_label_span = sources.make_span(store, "rakuten.html", {"kind": "html_text", "encoding": "utf-8", "path": "fixture.label"}, html.index("Color"), html.index("Color") + 5, "Color")
        r_value_start = html.index('"value":"Red"') + len('"value":"')
        r_value_span = sources.make_span(store, "rakuten.html", {"kind": "html_text", "encoding": "utf-8", "path": "fixture.selected"}, r_value_start, r_value_start + 3, "Red")
        case = {"case_id": "case-x", "dossier_id": "dossier-x", "au_product_id": "au-x", "rakuten_selected": {
            "raw_file": "rakuten.html", "sha256": store.sha("rakuten.html"), "source_sku_key": "sku-x",
            "axes": [{"axis_label": "Color", "axis_label_span": r_label_span, "family_values": family,
                      "value": "Red", "value_span": r_value_span}]}}
        axis_name_span = sources.json_leaf_span(store, "au.json", "$.itemInfo.skuInfo.optionName.row", "Color")
        au_index = au_family.index(au_value)
        au_value_span = sources.json_leaf_span(store, "au.json", f"$.itemInfo.skuInfo.rowNames[{au_index}]", au_value)
        product = {"dossier_id": "dossier-x", "au_product": {"product_id": "au-x", "raw_file": "au.json", "sha256": store.sha("au.json")},
                   "au_rows": [{"row_key": "row-x", "axes": [{"axis_name": "Color", "axis_name_span": axis_name_span,
                       "value": au_value, "value_span": au_value_span}]}]}
        forward = {"task_id": "f", "direction": "rakuten_to_au", "case_id": "case-x", "dossier_id": "dossier-x",
                   "au_product_id": "au-x", "au_row_key": "row-x", "source_sku_key": "sku-x",
                   "raw_condition_ref": {"axis_name": "Color", "value": "Red", "value_span": r_value_span}}
        reverse = {"task_id": "r", "direction": "au_to_rakuten", "case_id": "case-x", "dossier_id": "dossier-x",
                   "au_product_id": "au-x", "au_row_key": "row-x", "source_sku_key": "sku-x",
                   "raw_condition_ref": {"axis_name": "Color", "value": au_value, "value_span": au_value_span}}
        return store, case, product, forward, reverse, html

    def tearDown(self):
        if hasattr(self, "tmp"):
            self.tmp.cleanup()

    def test_equal_exact_selected_values_prove_support(self):
        args = self.make_fixture()
        result = proof.prove_task_pair(args[3], args[4], args[1], args[2], args[0])
        self.assertEqual(result["status"], "support")
        self.assertEqual(result["proof_version"], "literal_complete_ordered_family_v1")

    def test_distinct_values_in_identical_complete_ordered_families_prove_conflict(self):
        args = self.make_fixture(au_value="Blue")
        result = proof.prove_task_pair(args[3], args[4], args[1], args[2], args[0])
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["reason"], "distinct_exact_values_in_identical_complete_ordered_family")

    def test_reordered_or_incomplete_family_stays_unknown(self):
        args = self.make_fixture(au_family=["Blue", "Red"])
        result = proof.prove_task_pair(args[3], args[4], args[1], args[2], args[0])
        self.assertEqual(result["status"], "unknown")
        self.assertIn("complete ordered raw option families differ", result["reason"])

    def test_duplicate_exact_axis_name_stays_unknown(self):
        args = self.make_fixture(duplicate_axis=True)
        result = proof.prove_task_pair(args[3], args[4], args[1], args[2], args[0])
        self.assertEqual(result["status"], "unknown")
        self.assertIn("ambiguous", result["reason"])

    def test_reverse_context_must_mirror_frozen_selected_value(self):
        args = self.make_fixture()
        args[4]["raw_condition_ref"]["value"] = "Blue"
        result = proof.prove_task_pair(args[3], args[4], args[1], args[2], args[0])
        self.assertEqual(result["status"], "unknown")
        self.assertIn("frozen AU row", result["reason"])

    def test_corrupt_selected_span_stays_unknown(self):
        args = self.make_fixture()
        args[4]["raw_condition_ref"]["value_span"]["quote"] = "Blue"
        result = proof.prove_task_pair(args[3], args[4], args[1], args[2], args[0])
        self.assertEqual(result["status"], "unknown")
        self.assertIn("quoted text differs", result["reason"])

    def test_row_and_case_binding_must_match(self):
        args = self.make_fixture()
        args[4]["au_row_key"] = "other-row"
        result = proof.prove_task_pair(args[3], args[4], args[1], args[2], args[0])
        self.assertEqual(result["status"], "unknown")
        self.assertIn("binding differs", result["reason"])

    def test_paired_task_source_sha_must_match_frozen_manifest(self):
        args = self.make_fixture()
        provenance = {"case_source_sha256": "case-hash", "product_source_sha256": "product-hash",
                      "source_version": "v2", "row_array_source": {"file": "rows.jsonl", "line": 1},
                      "row_key": "row-x"}
        args[3]["provenance"] = dict(provenance)
        args[4]["provenance"] = dict(provenance)
        pins = {"v2": {"cases_sha256": "case-hash", "products_sha256": "wrong-product-hash"}}
        result = proof.prove_task_pair(args[3], args[4], args[1], args[2], args[0], pins)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("product-source SHA", result["reason"])


if __name__ == "__main__":
    unittest.main()
