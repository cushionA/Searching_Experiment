"""Semantic boundaries for fully automatic SKU acceptance."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

HERE = Path(__file__).resolve().parents[1] / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("sku_nonllm_gate_test_module", HERE / "sku_nonllm_gate_v1.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def scenario(forward, reverse=None):
    reverse = reverse or [None] * len(forward)
    rows = [{"row_key": f"r{i}", "atoms": [] if r is None else [{"type": "fabric", "value": "パイル", "quote": "パイル"}]}
            for i, r in enumerate(reverse)]
    evidence = [{"span": {"quote": "exact raw text"}}]

    class Evaluator:
        def rak_facts_for(self, *args): return []
        def au_facts_for(self, *args): return []
        def evaluate(self, req, row, facts): return {"status": forward[int(row["row_key"][1:])], "evidence": evidence}
        def evaluate_au_only(self, atom, reqs, facts, row):
            return {"status": reverse[int(row["row_key"][1:])], "evidence": evidence}
        def derived_conflicts(self, *args): return []
        def product_contrast(self, *args): return False

    req = {"type": "component_presence", "component": "lace", "value": True,
           "requirement_id": "lace", "span": {"quote": "あり"}, "decomposition": "complete"}
    gates = SimpleNamespace(make_evaluator=lambda *args: Evaluator(), selected_atoms=lambda *args: [req],
                            comparable=lambda *args: False, compare_atoms=lambda *args: "unknown",
                            src=SimpleNamespace(verify_span=lambda *args: True))
    case = {"case_id": "case", "dossier_id": "fixed-pair", "au_product_id": "one-fixed-url",
            "rakuten_selected": {"variant_id": "one-selected-rakuten-sku", "source_sku_key": "source-key"}}
    return case, SimpleNamespace(rows=rows), gates


class BinarySKUAcceptanceTests(unittest.TestCase):
    def evaluate(self, forward, reverse=None):
        case, facts, gates = scenario(forward, reverse)
        return module.strict_case(case, facts, gates, SimpleNamespace())

    def test_missing_lace_evidence_auto_drops_without_review(self):
        result = self.evaluate(["unknown"])
        self.assertEqual(result["decision"], "drop")
        self.assertIsNone(result["au_row_key"])

    def test_one_supported_fixed_row_and_excluded_others_accept(self):
        result = self.evaluate(["support", "conflict"])
        self.assertEqual((result["decision"], result["au_row_key"]), ("accept", "r0"))
        self.assertEqual(result["full_au_row_keys"], ["r0", "r1"])

    def test_unknown_other_row_cannot_be_removed_to_manufacture_uniqueness(self):
        self.assertEqual(self.evaluate(["support", "unknown"])["decision"], "drop")

    def test_reverse_au_fabric_condition_is_mandatory(self):
        self.assertEqual(self.evaluate(["support"], ["unverified"])["decision"], "drop")
        self.assertEqual(self.evaluate(["support"], ["support"])["decision"], "accept")

    def test_opposite_option_always_drops(self):
        result = self.evaluate(["conflict", "conflict"])
        self.assertEqual(result["decision"], "drop")
        self.assertEqual(result["reason"], "every_row_has_explicit_conflict")

    def test_multiple_fully_supported_rows_auto_drop(self):
        self.assertEqual(self.evaluate(["support", "support"])["decision"], "drop")


@unittest.skipUnless(module.DEFAULT_GATE_CODE.exists(), "frozen comparison gate not restored")
class FrozenGateExtensionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gates = module.load_gate()

    def test_partial_dimensions_do_not_prove_missing_length(self):
        req = {"type": "dimension", "role": "labeled", "labels": ["width", "length"], "value": [100., 80.]}
        cand = {"type": "dimension", "role": "labeled", "labels": ["width"], "value": [100.]}
        self.assertEqual(self.gates.compare_atoms(req, cand, lambda *args: False), "unknown")
        self.assertEqual(self.gates.compare_atoms(cand, req, lambda *args: False), "support")

    def test_numeric_unit_and_role_prevent_equal_number_false_match(self):
        a = {"type": "quantity", "value": 10, "unit": "本", "quantity_role": "package_total"}
        b = {"type": "quantity", "value": 10, "unit": "畳", "quantity_role": "area_capacity"}
        self.assertFalse(self.gates.comparable(a, b))

    def test_explicit_lace_absence_does_not_become_presence(self):
        facts = self.gates.title_facts("カーテン レースなし", frozenset(), ())
        self.assertEqual(set(facts["component_presence:lace"]["values"]), {False})
        both = self.gates.title_facts("カーテン レースあり レースなし", frozenset(), ())
        self.assertFalse(both["component_presence:lace"]["single_valued"])

    def test_selected_variant_fabric_supplies_reverse_evidence(self):
        span = {"quote": "パイル・タオル", "start": 100, "end": 107}
        attrs = [{"title": "素材（生地・毛糸）", "value": "パイル・タオル", "value_span": span}]
        fabric = [f for f in self.gates.attribute_facts(attrs) if f["atom"]["type"] == "fabric"]
        self.assertEqual(len(fabric), 1)
        self.assertEqual(fabric[0]["span"]["quote"], "パイル")
        self.assertFalse(fabric[0]["derived"])

    def test_lace_absence_requires_equal_declared_total_and_drape_count(self):
        row = {"atoms": [{"type": "piece_total", "value": 2, "span": {"quote": "2枚"}, "quote": "2枚"}]}
        drape = {"atom": {"type": "component_count", "component": "drape", "value": 2, "quote": "カーテン2枚"},
                 "single_valued": True, "span": {"quote": "カーテン2枚"}}
        facts = module.add_declared_lace_absence(row, [drape], self.gates)
        self.assertEqual(facts[-1]["atom"]["value"], False)
        self.assertEqual(len(facts[-1]["line_spans"]), 2)
        self.assertEqual(module.add_declared_lace_absence({"atoms": []}, [drape], self.gates), [drape])
        row["atoms"][0]["value"] = 4
        self.assertEqual(module.add_declared_lace_absence(row, [drape], self.gates), [drape])

    def test_title_does_not_turn_a_hanger_use_case_into_a_variant(self):
        facts = self.gates.title_facts("ハンガー ニット スーツ パンツ", frozenset(), ("ノーマル", "バー付き", "パンツ"))
        self.assertNotIn("variant", facts)
        facts = self.gates.title_facts("座椅子 4WAY", frozenset(), ("4WAY", "回転"))
        self.assertIn("4WAY", facts["variant"]["values"])
        facts = self.gates.title_facts("4WAYタイプ 回転タイプ", frozenset(), ("4WAY", "回転"))
        self.assertFalse(facts["variant"]["single_valued"])

    def test_adjustable_height_is_not_a_conflicting_fixed_height(self):
        raw = "幅68×奥行28.5×高さ10/15cm"
        facts = self.gates.describe_lines([
            {"text": "商品詳細"}, {"text": "サイズ"}, {"text": raw}], frozenset(), ())
        atom = next(f["atom"] for f in facts if f.get("atom", {}).get("type") == "dimension")
        self.assertEqual(atom["quote"], raw)
        self.assertEqual(atom["alternatives"], {"height": [10., 15.]})
        req = {"type": "dimension", "role": "labeled", "labels": ["height"], "value": [15.]}
        self.assertEqual(self.gates.compare_atoms(req, atom, lambda *args: False), "support")
        req["value"] = [20.]
        self.assertEqual(self.gates.compare_atoms(req, atom, lambda *args: False), "conflict")


if __name__ == "__main__":
    unittest.main()
