from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "sku-matching"))
import sku_luna_adjustable_dimension_task as adjustable

RAW = ".lab-output/sku-real-au-expanded/item.json"
SHA = "a" * 64
QUOTE = "約 幅68×奥行28.5×高さ10/15cm"
FULL_CONTEXT = "tr: サイズ 約 幅68×奥行28.5×高さ10/15cm ※高さ2段階調節可能 ※未使用時は調節台をコンパクトに収納可能"
REFS = [{"raw_file": RAW, "sha256": SHA, "locator": "$.itemInfo.extraItemComment"},
        {"raw_file": RAW, "sha256": SHA, "locator": "$.itemInfo.extraItemCommentWithVideo"}]
TARGETS = [{"axis": "本体横幅", "value": "68", "unit": "cm"},
           {"axis": "本体奥行", "value": "28.5", "unit": "cm"},
           {"axis": "本体高さ", "value": "15", "unit": "cm"}]


def fixture(*, quote=QUOTE, context_text=FULL_CONTEXT, scope="fixed_product", row=None):
    condition = row or {"axis": "カラー", "value": "ブラウン"}
    row_conditions = [condition]
    case = {"sources": [
        {"source_id": "S0", "kind": "title", "text": "ステップ台 高さ調節", "source": REFS[0]},
        {"source_id": "S40", "kind": "description", "text": quote, "source": copy.deepcopy(REFS)},
        {"source_id": "S41", "kind": "description", "text": "※高さ2段階調節可能", "source": copy.deepcopy(REFS)},
    ], "au_rows": [{"row_key": "row-current", "conditions": row_conditions}]}
    contexts = {
        "S0": {"kind": "title", "scope": "fixed_product", "text": "ステップ台 高さ調節",
               "contexts": ["ステップ台 高さ調節"], "provenance": [REFS[0]]},
        "S40": {"kind": "description", "scope": scope, "text": quote, "contexts": [context_text],
                "provenance": copy.deepcopy(REFS), "context_provenance": [
                    {"raw_file": RAW, "sha256": SHA, "json_path": "$.itemInfo.extraItemComment"},
                    {"raw_file": RAW, "sha256": SHA, "json_path": "$.itemInfo.extraItemCommentWithVideo"}]},
        "S41": {"kind": "description", "scope": scope, "text": "※高さ2段階調節可能", "contexts": [context_text],
                "provenance": copy.deepcopy(REFS), "context_provenance": [
                    {"raw_file": RAW, "sha256": SHA, "json_path": "$.itemInfo.extraItemComment"}]},
    }
    source_map = {"S40": {"source_id": "S40", "quote": quote, "provenance": copy.deepcopy(REFS)},
                  "S41": {"source_id": "S41", "quote": "※高さ2段階調節可能", "provenance": copy.deepcopy(REFS)},
                  "A0": {"source_id": "A0", "quote": condition["value"], "provenance": {"quote": condition["value"]}}}
    answer = {"checks": [{"status": "support", "source_ids": ["S40"]} for _ in TARGETS]}
    return case, TARGETS, answer, source_map, row_conditions, contexts


def adjustable_guard(*args):
    return adjustable.guard_adjustable_dimensions(*args)


class AdjustableDimensionTests(unittest.TestCase):
    def test_exact_fixed_source_and_current_row_gets_only_discrete_height_alias(self):
        case, targets, answer, source_map, row, contexts = fixture()
        guarded, issues = adjustable_guard(case, targets, answer, source_map, row, contexts)
        self.assertEqual([check["status"] for check in guarded["checks"]], ["support"] * 3)
        self.assertEqual(issues, [])
        audit = adjustable.adjustable_dimension_alias_audit(case, targets, answer, source_map, row, contexts)
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["target_index"], 2)
        self.assertEqual(audit[0]["settings"], ["10", "15"])
        self.assertIn("高さ15cm", audit[0]["proxy_quote"])
        self.assertIn("幅68×奥行28.5", audit[0]["proxy_quote"])

    def test_height_outside_discrete_settings_keeps_strict_unknown(self):
        case, targets, answer, source_map, row, contexts = fixture()
        targets[2]["value"] = "12"
        guarded, _ = adjustable_guard(case, targets, answer, source_map, row, contexts)
        self.assertEqual(guarded["checks"][2]["status"], "contradiction")
        self.assertEqual(adjustable.adjustable_dimension_alias_audit(case, targets, answer, source_map, row, contexts), [])

    def test_missing_adjustment_phrase_or_wrong_context_source_is_rejected(self):
        case, targets, answer, source_map, row, contexts = fixture(context_text="tr: サイズ " + QUOTE)
        self.assertEqual(adjustable.adjustable_dimension_alias_audit(case, targets, answer, source_map, row, contexts), [])
        case, targets, answer, source_map, row, contexts = fixture()
        contexts["S40"]["context_provenance"][0]["sha256"] = "b" * 64
        self.assertEqual(adjustable.adjustable_dimension_alias_audit(case, targets, answer, source_map, row, contexts), [])

    def test_scope_part_range_and_non_discrete_claims_do_not_alias(self):
        case, targets, answer, source_map, row, contexts = fixture(scope="other_product")
        self.assertEqual(adjustable.adjustable_dimension_alias_audit(case, targets, answer, source_map, row, contexts), [])
        case, targets, answer, source_map, row, contexts = fixture(
            quote="収納時 約 幅68×奥行28.5×高さ10/15cm",
            context_text="収納時 約 幅68×奥行28.5×高さ10/15cm 高さ2段階調節可能")
        self.assertEqual(adjustable.adjustable_dimension_alias_audit(case, targets, answer, source_map, row, contexts), [])
        case, targets, answer, source_map, row, contexts = fixture(
            quote="約 幅68×奥行28.5×高さ10〜15cm",
            context_text="サイズ 約 幅68×奥行28.5×高さ10〜15cm 高さ2段階調節可能")
        self.assertEqual(adjustable.adjustable_dimension_alias_audit(case, targets, answer, source_map, row, contexts), [])

    def test_fixed_row_height_blocks_alias(self):
        case, targets, answer, source_map, row, contexts = fixture(
            row={"axis": "サイズ", "value": "高さ20cm"})
        self.assertEqual(adjustable.adjustable_dimension_alias_audit(case, targets, answer, source_map, row, contexts), [])
        guarded, _ = adjustable_guard(case, targets, answer, source_map, row, contexts)
        self.assertEqual(guarded["checks"][2]["status"], "contradiction")

    def test_wrong_current_row_or_uncited_source_cannot_trigger_alias(self):
        case, targets, answer, source_map, row, contexts = fixture()
        answer["checks"][2]["source_ids"] = ["S41"]
        self.assertEqual(adjustable.adjustable_dimension_alias_audit(case, targets, answer, source_map, row, contexts), [])
        answer["checks"][2]["source_ids"] = ["S40"]
        wrong_row = [{"axis": "カラー", "value": "ブラック"}]
        self.assertEqual(adjustable.adjustable_dimension_alias_audit(case, targets, answer, source_map, wrong_row, contexts), [])

    def test_unit_conversion_and_input_immutability(self):
        case, targets, answer, source_map, row, contexts = fixture()
        targets[2] = {"axis": "本体高さ", "value": "150", "unit": "mm"}
        before = copy.deepcopy((case, targets, answer, source_map, row, contexts))
        guarded, _ = adjustable_guard(case, targets, answer, source_map, row, contexts)
        audit = adjustable.adjustable_dimension_alias_audit(case, targets, answer, source_map, row, contexts)
        self.assertEqual([x["status"] for x in guarded["checks"]], ["support"] * 3)
        self.assertEqual(audit[0]["target"]["unit"], "mm")
        self.assertEqual((case, targets, answer, source_map, row, contexts), before)

    def test_compact_request_roundtrips_contexts_and_preserves_schema(self):
        payload = {"selected_dimensions": TARGETS, "current_au_conditions": [],
                   "au_sources": {"S40": {"kind": "description", "text": QUOTE, "scope": "fixed_product",
                                          "contexts": [FULL_CONTEXT, FULL_CONTEXT]},
                                  "S41": {"kind": "description", "text": "height", "scope": "fixed_product",
                                          "contexts": [FULL_CONTEXT]}}}
        schema = {"type": "OBJECT", "properties": {"checks": {"type": "ARRAY", "items": {"type": "OBJECT"}}}}
        original = {"instructions": "Read only this request", "body": {"contents": [{"role": "user", "parts": [
            {"text": "older instructions\nINPUT=" + json.dumps(payload, ensure_ascii=False)}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema}}}
        compacted = adjustable.compact_request(original)
        text = compacted["body"]["contents"][0]["parts"][0]["text"]
        compact_payload = json.loads(text.split("INPUT=", 1)[1])
        rebuilt = {}
        for sid, source in compact_payload["au_sources"].items():
            restored = {k: v for k, v in source.items() if k != "context_ids"}
            restored["contexts"] = [compact_payload["context_blocks"][cid] for cid in source["context_ids"]]
            rebuilt[sid] = restored
        self.assertEqual(rebuilt, payload["au_sources"])
        for key in ("selected_dimensions", "current_au_conditions"):
            self.assertEqual(compact_payload[key], payload[key])
        self.assertEqual(compacted["body"]["generationConfig"]["responseSchema"], schema)
        self.assertEqual(compacted["instructions"], original["instructions"])


if __name__ == "__main__":
    unittest.main()
