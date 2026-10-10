from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "sku-matching"
sys.path.insert(0, str(EXPERIMENT))
import sku_luna_compact_context_task as compact
import sku_luna_output_shape as shape

INPUT_DIR = EXPERIMENT / "results" / "20261010T185500Z-luna-multirow62" / "inference"
CASE_ID = "case-e1f588b9dd6b8903515e"


class CompactContextTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads((INPUT_DIR / "inputs.json").read_text(encoding="utf-8"))
        cls.contexts = json.loads((INPUT_DIR / "source-contexts.json").read_text(encoding="utf-8"))
        cls.case = next(item for item in cls.cases if item["case_id"] == CASE_ID)
        cls.context = cls.contexts[CASE_ID]

    @staticmethod
    def parse(request):
        text = request["body"]["contents"][0]["parts"][0]["text"]
        marker = "INPUT="
        start = text.index(marker) + len(marker)
        return text, json.loads(text[start:])

    def test_context_dedup_roundtrips_exactly_and_preserves_v3_payload_schema(self):
        original = shape.output_shape_request(self.case, self.context)
        compacted = compact.compact_request(self.case, self.context)
        old_text, old_payload = self.parse(original)
        new_text, new_payload = self.parse(compacted)
        rebuilt = {}
        for sid, source in new_payload["sources"].items():
            restored = {key: value for key, value in source.items() if key != "context_ids"}
            restored["contexts"] = [new_payload["context_blocks"][block] for block in source["context_ids"]]
            rebuilt[sid] = restored
        self.assertEqual(rebuilt, old_payload["sources"])
        for key in ("rakuten_conditions", "selected_attributes", "au_rows", "check_targets"):
            self.assertEqual(new_payload[key], old_payload[key])
        self.assertEqual(compacted["body"]["generationConfig"]["responseSchema"],
                         original["body"]["generationConfig"]["responseSchema"])
        self.assertLess(len(new_text), len(old_text))
        self.assertIn("context_idsはcontext_blocksのキー", new_text)

    def test_selected_attributes_are_preserved_and_raw_provenance_is_not_sent(self):
        request = compact.compact_request(self.case, self.context)
        text, payload = self.parse(request)
        self.assertEqual(payload["selected_attributes"], [
            {key: item.get(key) for key in ("axis", "value", "unit")}
            for item in self.case.get("selected_attributes", [])])
        bounded = payload["bounded_contents"]
        self.assertEqual(bounded["source_id"], "S30")
        self.assertEqual(bounded["scope"], "fixed_product")
        self.assertIn("<b>内容</b>", bounded["html_row"])
        self.assertEqual(bounded["next_section_label"], "カラー")
        for forbidden in ("raw_file", "sha256", "field_path", "itemInfo.extraItemComment"):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("case_id", payload)
        self.assertNotIn("au_product_id", payload)

    def test_context_compression_and_row_order_on_real_27_row_input(self):
        request = compact.compact_request(self.case, self.context)
        old_text, _ = self.parse(shape.output_shape_request(self.case, self.context))
        new_text, payload = self.parse(request)
        self.assertLess(len(new_text), len(old_text))
        self.assertEqual(len(payload["au_rows"]), 27)
        self.assertEqual([row["row_index"] for row in payload["au_rows"]], list(range(27)))
        self.assertEqual([row["row_index"] for row in payload["check_targets"]], list(range(27)))
        self.assertEqual(set(payload["sources"]["S30"]),
                         {"kind", "text", "scope", "context_ids"})


if __name__ == "__main__":
    unittest.main()
