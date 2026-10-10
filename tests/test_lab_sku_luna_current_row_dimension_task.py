from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "sku-matching"))
import sku_luna_current_row_dimension_task as dimension


def fixture(title="ドレープカーテン 本体サイズ"):
    attrs = [{"axis": "本体横幅", "value": "100", "unit": "cm"},
             {"axis": "本体高さ", "value": "178", "unit": "cm"}]
    row = {"row_key": "r-current", "conditions": [
        {"axis": "カラー", "value": "ベージュ"},
        {"axis": "サイズ", "value": "幅100×丈178cm(4枚組)"}]}
    case = {"selected_attributes": attrs, "au_rows": [row], "sources": [
        {"source_id": "S0", "kind": "title", "text": title,
         "source": {"raw_file": "/private/source.json", "sha256": "private-digest"}},
        {"source_id": "S1", "kind": "description", "text": "高さ", "source": {"raw_file": "private"}}]}
    contexts = {"S0": {"source_id": "S0", "kind": "title", "scope": "fixed_product",
                       "text": title, "contexts": [title], "provenance": {"path": "private"}},
                "S1": {"source_id": "S1", "kind": "description", "scope": "fixed_product",
                       "text": "高さ", "contexts": ["高さ200cm"]}}
    return case, row, attrs, contexts


def prompt_payload(request):
    text = request["body"]["contents"][0]["parts"][0]["text"]
    return text, json.loads(text.split("INPUT=", 1)[1])


class CurrentRowDimensionTaskTests(unittest.TestCase):
    def test_row_must_be_exact_member_of_case(self):
        case, row, targets, contexts = fixture()
        changed = json.loads(json.dumps(row))
        changed["conditions"][1]["value"] = "幅100×丈180cm(4枚組)"
        with self.assertRaisesRegex(ValueError, "exactly match"):
            dimension.request(case, changed, targets, contexts)

    def test_targets_must_match_selected_mandatory_dimensions(self):
        case, row, targets, contexts = fixture()
        changed = [*targets]
        changed[0] = {**changed[0], "value": "110"}
        with self.assertRaisesRegex(ValueError, "selected mandatory"):
            dimension.request(case, row, changed, contexts)
        with self.assertRaisesRegex(ValueError, "selected mandatory"):
            dimension.request(case, row, targets + [{"axis": "奥行", "value": "40", "unit": "cm"}], contexts)

    def test_provider_body_contains_only_current_scoped_sources_and_strict_schema(self):
        case, row, targets, contexts = fixture()
        req = dimension.request(case, row, targets, contexts)
        text, payload = prompt_payload(req)
        self.assertEqual(set(payload), {"selected_dimensions", "current_au_conditions", "sources"})
        self.assertEqual(payload["selected_dimensions"], targets)
        self.assertEqual(payload["current_au_conditions"], [
            {"source_id": "A0", "axis": "カラー", "value": "ベージュ", "scope": "fixed_product",
             "kind": "sku_condition", "current_row": True},
            {"source_id": "A1", "axis": "サイズ", "value": "幅100×丈178cm(4枚組)", "scope": "fixed_product",
             "kind": "sku_condition", "current_row": True}])
        self.assertEqual(set(payload["sources"]), {"A0", "A1", "S0"})
        self.assertTrue(all(v.get("scope") == "fixed_product" and v.get("current_row") is True
                            for k, v in payload["sources"].items() if k.startswith("A")))
        self.assertNotIn("S1", payload["sources"])
        for forbidden in ("private-digest", "raw_file", "provenance", "path", "choices", "row_key"):
            self.assertNotIn(forbidden, json.dumps(payload, ensure_ascii=False))
        schema = req["body"]["generationConfig"]["responseSchema"]
        self.assertEqual(schema["properties"]["checks"]["minItems"], 2)
        self.assertEqual(schema["properties"]["checks"]["maxItems"], 2)
        self.assertEqual(set(schema["properties"]["checks"]["items"]["properties"]), {"status", "source_ids"})

    def test_curtain_alias_is_conditional_and_empty_targets_are_supported(self):
        case, row, targets, contexts = fixture()
        curtain_text, _ = prompt_payload(dimension.request(case, row, targets, contexts))
        self.assertIn("「幅/横幅」は本体横幅", curtain_text)
        self.assertIn("「丈/高さ」は本体高さ", curtain_text)
        case, row, _, contexts = fixture(title="収納ボックス")
        case["selected_attributes"] = []
        text, payload = prompt_payload(dimension.request(case, row, [], contexts))
        self.assertNotIn("本体横幅", text)
        self.assertEqual(payload["selected_dimensions"], [])
        schema = dimension.request(case, row, [], contexts)["body"]["generationConfig"]["responseSchema"]
        self.assertEqual((schema["properties"]["checks"]["minItems"],
                          schema["properties"]["checks"]["maxItems"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
