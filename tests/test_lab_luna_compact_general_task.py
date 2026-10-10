from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "sku-matching"
sys.path.insert(0, str(EXPERIMENT))
import sku_luna_compact_general_task as compact
import sku_luna_output_shape as shape

INPUT_DIR = EXPERIMENT / "results" / "20261010T185500Z-luna-multirow62" / "inference"
CURTAIN_CASE_ID = "case-e1f588b9dd6b8903515e"


class CompactGeneralTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads((INPUT_DIR / "inputs.json").read_text(encoding="utf-8"))
        cls.contexts = json.loads((INPUT_DIR / "source-contexts.json").read_text(encoding="utf-8"))
        cls.by_id = {case["case_id"]: case for case in cls.cases}

    @staticmethod
    def parse(request):
        text = request["body"]["contents"][0]["parts"][0]["text"]
        start = text.index("INPUT=") + len("INPUT=")
        return text, json.loads(text[start:])

    def test_all_original_input_and_schema_are_lossless_after_context_dedup(self):
        case = self.by_id[CURTAIN_CASE_ID]
        context = self.contexts[CURTAIN_CASE_ID]
        original = shape.output_shape_request(case, context)
        request = compact.compact_request(case, context)
        old_text, old = self.parse(original)
        new_text, new = self.parse(request)
        rebuilt = {}
        for sid, source in new["sources"].items():
            item = {key: value for key, value in source.items() if key != "context_ids"}
            item["contexts"] = [new["context_blocks"][block] for block in source["context_ids"]]
            rebuilt[sid] = item
        self.assertEqual(rebuilt, old["sources"])
        for key in ("rakuten_conditions", "selected_attributes", "au_rows", "check_targets"):
            self.assertEqual(new[key], old[key])
        self.assertEqual(request["body"]["generationConfig"]["responseSchema"],
                         original["body"]["generationConfig"]["responseSchema"])
        self.assertLess(len(new_text), len(old_text))
        self.assertTrue(request["local_metadata"]["bounded_context_available"])

    def test_bounded_contents_requires_fixed_curtain_title_and_sends_no_provenance(self):
        case = self.by_id[CURTAIN_CASE_ID]
        request = compact.compact_request(case, self.contexts[CURTAIN_CASE_ID])
        text, payload = self.parse(request)
        bounded = payload["bounded_contents"]
        self.assertEqual(bounded["source_id"], "S30")
        self.assertEqual(bounded["scope"], "fixed_product")
        self.assertIn("<b>内容</b>", bounded["html_row"])
        self.assertEqual(bounded["next_section_label"], "カラー")
        for forbidden in ("sha256", "raw_file", "field_path", "case_id", "au_product_id", "itemInfo.extraItemComment"):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("local_metadata", request["body"])

    def test_noncurtain_or_missing_contents_does_not_receive_bounded_cell(self):
        candidate = self.by_id[CURTAIN_CASE_ID]
        context = json.loads(json.dumps(self.contexts[CURTAIN_CASE_ID]))
        context["S0"]["text"] = "収納ボックス"
        request = compact.compact_request(candidate, context)
        _, payload = self.parse(request)
        self.assertNotIn("bounded_contents", payload)
        self.assertFalse(request["local_metadata"]["bounded_context_available"])
        self.assertEqual(request["local_metadata"]["bounded_context_reason"], "fixed_product_title_not_curtain")

    def test_ambiguous_contents_source_records_reason_without_fallback(self):
        case = self.by_id[CURTAIN_CASE_ID]
        context = json.loads(json.dumps(self.contexts[CURTAIN_CASE_ID]))
        context.pop("S30")
        request = compact.compact_request(case, context)
        _, payload = self.parse(request)
        self.assertNotIn("bounded_contents", payload)
        self.assertFalse(request["local_metadata"]["bounded_context_available"])
        self.assertTrue(request["local_metadata"]["bounded_context_reason"].startswith("fixed_product_contents_label_not_unique"))


if __name__ == "__main__":
    unittest.main()
