import copy
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/sku-matching"))
from sku_luna_dimension_guard import guard_dimensions


class LunaDimensionGuardTests(unittest.TestCase):
    def run_guard(self, targets, checks, sources, conditions=None):
        answer = {"checks": checks}
        before = copy.deepcopy(answer)
        guarded, issues = guard_dimensions(targets, answer, sources, conditions or [])
        self.assertEqual(answer, before, "guard must preserve the raw model answer")
        return guarded, issues

    def test_labeled_swapped_width_depth_mismatch_vetoes_tuple_permutation(self):
        targets = [
            {"axis": "本体幅", "value": 100, "unit": "cm"},
            {"axis": "本体奥行", "value": 90, "unit": "cm"},
        ]
        checks = [{"status": "support", "source_ids": ["A0"]} for _ in targets]
        sources = {"A0": {"quote": "本体 幅90×奥行100cm"}}
        conditions = [{"axis": "幅・奥行", "value": "幅90×奥行100cm"}]
        guarded, _ = self.run_guard(targets, checks, sources, conditions)
        self.assertEqual(guarded["checks"][0]["status"], "contradiction")
        self.assertEqual(guarded["checks"][1]["status"], "contradiction")

    def test_color_source_cannot_support_dimension(self):
        targets = [{"axis": "本体幅", "value": 100, "unit": "cm"}]
        guarded, _ = self.run_guard(targets, [{"status": "support", "source_ids": ["A0"]}],
                                    {"A0": {"quote": "色=白"}}, [{"axis": "色", "value": "白"}])
        self.assertEqual(guarded["checks"][0], {"status": "unknown", "source_ids": []})

    def test_converts_units_for_labeled_dimensions(self):
        targets = [{"axis": "幅", "value": 100, "unit": "cm"}]
        guarded, _ = self.run_guard(targets, [{"status": "support", "source_ids": ["S0"]}],
                                    {"S0": {"quote": "幅1000mm"}})
        self.assertEqual(guarded["checks"][0]["status"], "support")

    def test_complete_unlabeled_same_component_tuple_is_allowed(self):
        targets = [
            {"axis": "本体幅", "value": 100, "unit": "cm"},
            {"axis": "本体奥行", "value": 50, "unit": "cm"},
            {"axis": "本体高さ", "value": 80, "unit": "cm"},
        ]
        checks = [{"status": "support", "source_ids": ["S0"]} for _ in targets]
        guarded, _ = self.run_guard(targets, checks, {"S0": {"quote": "本体サイズ 100×50×80cm"}})
        self.assertTrue(all(c["status"] == "support" for c in guarded["checks"]))

    def test_incomplete_or_extra_tuple_values_do_not_prove_targets(self):
        targets = [
            {"axis": "本体幅", "value": 100, "unit": "cm"},
            {"axis": "本体奥行", "value": 50, "unit": "cm"},
        ]
        for quote in ("本体サイズ 100cm", "本体サイズ 100×50×80cm"):
            with self.subTest(quote=quote):
                checks = [{"status": "support", "source_ids": ["S0"]} for _ in targets]
                guarded, _ = self.run_guard(targets, checks, {"S0": {"quote": quote}})
                self.assertTrue(all(c["status"] == "unknown" for c in guarded["checks"]))

    def test_storage_measurement_does_not_support_body_width(self):
        targets = [{"axis": "本体幅", "value": 100, "unit": "cm"}]
        guarded, _ = self.run_guard(targets, [{"status": "support", "source_ids": ["A0"]}],
                                    {"A0": {"quote": "収納時 幅90cm"}},
                                    [{"axis": "収納時幅", "value": "90cm"}])
        self.assertEqual(guarded["checks"][0], {"status": "unknown", "source_ids": []})


if __name__ == "__main__":
    unittest.main()
