from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "sku-matching"
sys.path.insert(0, str(EXPERIMENT))
import sku_luna_curtain_dimension_alias as alias
import sku_luna_dimension_guard as old_guard

CASE_DIR = EXPERIMENT / "results" / "20261010T185500Z-luna-multirow62" / "inference"
CASE_ID = "case-fb8b163fce59c5808581"


def synthetic(case_title="カーテン 固定商品", scope="fixed_product", quote="幅150×丈200cm(2枚組)", axis="サイズ"):
    case = {"sources": [{"source_id": "S0", "kind": "title", "text": case_title}],
            "au_product_id": "au-1"}
    source_map = {"S0": {"quote": case_title, "kind": "title", "scope": scope},
                  "A1": {"quote": quote, "provenance": {"token": "unchanged"}}}
    row_conditions = [{"axis": "カラー", "value": "青"}, {"axis": axis, "value": quote}]
    return case, source_map, row_conditions


class CurtainDimensionAliasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads((CASE_DIR / "inputs.json").read_text(encoding="utf-8"))
        cls.contexts = json.loads((CASE_DIR / "source-contexts.json").read_text(encoding="utf-8"))[CASE_ID]
        cls.case = next(case for case in cls.cases if case["case_id"] == CASE_ID)

    def real_targets_and_quote(self):
        targets = [target for target in self.case["selected_attributes"]
                   if target["axis"] in {"本体横幅", "本体高さ"}]
        row = next(row for row in self.case["au_rows"]
                   if any(condition["value"] == "幅150×丈200cm(2枚組)" for condition in row["conditions"]))
        source_map = {"A" + str(i): {"quote": condition["value"]}
                      for i, condition in enumerate(row["conditions"])}
        source_map["S0"] = {"kind": "title", "scope": "fixed_product",
                            "text": self.contexts["S0"]["text"], "quote": self.contexts["S0"]["text"]}
        cond_index = next(i for i, condition in enumerate(row["conditions"])
                          if condition["value"] == "幅150×丈200cm(2枚組)")
        return targets, row["conditions"], source_map, cond_index

    def test_real_case_both_body_width_and_height_remain_supported(self):
        targets, conditions, source_map, cond_index = self.real_targets_and_quote()
        self.assertEqual({target["axis"] for target in targets}, {"本体横幅", "本体高さ"})
        answer = {"checks": [{"status": "support", "source_ids": [f"A{cond_index}"]} for _ in targets]}
        guarded, issues = alias.guard_curtain_dimensions(self.case, targets, answer, source_map, conditions)
        self.assertEqual([check["status"] for check in guarded["checks"]], ["support", "support"])
        self.assertEqual(issues, [])

    def test_different_length_becomes_height_contradiction(self):
        case, source_map, conditions = synthetic()
        target = [{"axis": "本体高さ", "value": 201, "unit": "cm"}]
        answer = {"checks": [{"status": "support", "source_ids": ["A1"]}]}
        guarded, issues = alias.guard_curtain_dimensions(case, target, answer, source_map, conditions)
        self.assertEqual(guarded["checks"][0], {"status": "contradiction", "source_ids": ["A1"]})
        self.assertEqual(issues[0]["error"], "explicit_dimension_axis_mismatch")

    def test_non_curtain_and_related_title_do_not_promote_height(self):
        for title, scope in (("椅子の商品", "fixed_product"), ("カーテン商品", "related_product")):
            with self.subTest(title=title, scope=scope):
                case, source_map, conditions = synthetic(title, scope)
                answer = {"checks": [{"status": "support", "source_ids": ["A1"]}]}
                guarded, _ = alias.guard_curtain_dimensions(case, [{"axis": "本体高さ", "value": 200, "unit": "cm"}],
                                                           answer, source_map, conditions)
                self.assertEqual(guarded["checks"][0]["status"], "unknown")

    def test_sleeve_and_inseam_length_alone_do_not_alias(self):
        for quote in ("袖丈20cm", "股下丈75cm"):
            with self.subTest(quote=quote):
                case, source_map, conditions = synthetic(quote=quote)
                answer = {"checks": [{"status": "support", "source_ids": ["A1"]}]}
                guarded, _ = alias.guard_curtain_dimensions(case, [{"axis": "本体高さ", "value": 20, "unit": "cm"}],
                                                           answer, source_map, conditions)
                self.assertEqual(guarded["checks"][0]["status"], "unknown")

    def test_storage_quote_does_not_support_body_height(self):
        case, source_map, conditions = synthetic(quote="収納時 幅150×丈200cm", axis="収納時サイズ")
        answer = {"checks": [{"status": "support", "source_ids": ["A1"]}]}
        guarded, _ = alias.guard_curtain_dimensions(case, [{"axis": "本体高さ", "value": 200, "unit": "cm"}],
                                                   answer, source_map, conditions)
        self.assertEqual(guarded["checks"][0]["status"], "unknown")
        self.assertEqual(alias.curtain_dimension_alias_audit(case, answer, source_map, conditions), [])

    def test_existing_unknown_is_never_upgraded(self):
        case, source_map, conditions = synthetic()
        answer = {"checks": [{"status": "unknown", "source_ids": []}]}
        guarded, issues = alias.guard_curtain_dimensions(case, [{"axis": "本体高さ", "value": 200, "unit": "cm"}],
                                                         answer, source_map, conditions)
        self.assertEqual(guarded, answer)
        self.assertEqual(issues, [])

    def test_nfkc_unit_conversion_and_input_immutability(self):
        case, source_map, conditions = synthetic(quote="幅１５０×丈２００㎝(２枚組)")
        targets = [{"axis": "本体高さ", "value": "2000", "unit": "mm"}]
        answer = {"checks": [{"status": "support", "source_ids": ["A1"]}]}
        originals = copy.deepcopy((case, targets, answer, source_map, conditions))
        guarded, issues = alias.guard_curtain_dimensions(case, targets, answer, source_map, conditions)
        self.assertEqual(guarded["checks"][0]["status"], "support")
        self.assertEqual(issues, [])
        self.assertEqual((case, targets, answer, source_map, conditions), originals)
        self.assertEqual(alias.curtain_dimension_alias_audit(case, answer, source_map, conditions)[0]["alias"], "丈→高さ")

    def test_full_tuple_and_explicit_contradiction_precedence_stay_in_old_guard(self):
        case, source_map, conditions = synthetic()
        # A one-target tuple follows the existing tuple logic only when all tuple values equal targets.
        tuple_answer = {"checks": [{"status": "support", "source_ids": ["A1"]}]}
        tuple_guarded, _ = alias.guard_curtain_dimensions(
            case, [{"axis": "本体高さ", "value": 150, "unit": "cm"},
                  {"axis": "本体横幅", "value": 200, "unit": "cm"}],
            {"checks": [{"status": "support", "source_ids": ["A1"]},
                         {"status": "support", "source_ids": ["A1"]}]}, source_map, conditions)
        self.assertEqual([x["status"] for x in tuple_guarded["checks"]], ["contradiction", "contradiction"])
        # The alias must not override an explicit mismatch in the transformed label.
        changed, _ = alias.guard_curtain_dimensions(
            case, [{"axis": "本体高さ", "value": 199, "unit": "cm"}], tuple_answer, source_map, conditions)
        self.assertEqual(changed["checks"][0]["status"], "contradiction")


if __name__ == "__main__":
    unittest.main()
