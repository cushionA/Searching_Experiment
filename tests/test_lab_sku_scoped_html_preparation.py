"""Protect real-source citation binding and predetermined error selection."""
import importlib.util
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1] / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("scoped_html_preparation_test", HERE / "prepare_scoped_html_trial_v1.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class PreparationContract(unittest.TestCase):
    def block_doc(self):
        ref = {"raw_file": "fixture.json", "sha256": "a" * 64, "locator": {"json_path": "$.itemInfo.extraItemComment"}}
        block = {"block_id": "b", "kind": "table_row", "source_ref": ref,
                 "visible_parts": ["内容", "幅100", "遮光カーテン 2枚"],
                 "text": "内容\n幅100\n遮光カーテン 2枚", "context": {}, "links": []}
        text = "内容\n幅100\n遮光カーテン 2枚"
        doc = {"document_id": "d", "document_sha256": "c" * 64,
               "description": {"text": text, "segments": [{"start": 0, "end": len(text),
               "source_refs": [{"sha256": "a" * 64, "locator": "$.itemInfo.extraItemComment"}]}]}}
        return block, doc

    def test_every_body_piece_binds_exactly(self):
        block, doc = self.block_doc()
        refs, failed = P.literal_refs(block, doc)
        self.assertFalse(failed)
        self.assertEqual(len(refs), 3)
        for ref in refs:
            self.assertEqual(ref["quote"], doc["description"]["text"][ref["start"]:ref["end"]])

    def test_same_word_in_other_field_cannot_supply_citation(self):
        block, doc = self.block_doc()
        doc["description"]["segments"][0]["source_refs"][0]["locator"] = "$.itemInfo.itemComment"
        refs, failed = P.literal_refs(block, doc)
        self.assertFalse(refs)
        self.assertEqual(len(failed), 3)

    def test_internal_space_not_rewritten_to_create_quote(self):
        block, doc = self.block_doc()
        block["visible_parts"][-1] = "遮光カーテン  2枚"
        _, failed = P.literal_refs(block, doc)
        self.assertEqual(failed, ["遮光カーテン  2枚"])

    def test_heading_also_requires_literal_evidence(self):
        block, doc = self.block_doc()
        block["context"] = {"table": {"preceding_header_rows": [{"text": "別商品", "visible_parts": ["別商品"]}]}}
        _, failed = P.literal_refs(block, doc)
        self.assertEqual(failed, ["別商品"])

    def test_link_destination_and_width_context_remain_visible(self):
        block, _ = self.block_doc()
        block["links"] = [{"text": "4枚セットはこちら", "href": "https://wowma.jp/item/other"}]
        rendered = P.render_passage(block)
        self.assertIn("幅100", rendered)
        self.assertIn("https://wowma.jp/item/other", rendered)
        self.assertIn("4枚セットはこちら", rendered)

    def test_error_selection_is_order_independent_and_capped_per_group(self):
        errors = [{"case_id": f"case-{i}", "cell": "fp", "au_product_id": "x"} for i in range(8)]
        errors += [{"case_id": "miss", "cell": "fn", "au_product_id": "x"}]
        selected, policy = P.choose_errors({"variants": {"fusion-predictions": {"errors": list(reversed(errors))}}})
        self.assertEqual({e["case_id"] for e in selected}, {"case-0", "case-1", "case-2", "miss"})
        self.assertEqual(policy["population_error_count"], 9)
        self.assertFalse(policy["new_model_results_used"])

    def test_duplicate_error_ids_rejected(self):
        row = {"case_id": "same", "cell": "fp", "au_product_id": "x"}
        with self.assertRaises(ValueError):
            P.choose_errors({"variants": {"fusion-predictions": {"errors": [row, row]}}})


if __name__ == "__main__":
    unittest.main()
