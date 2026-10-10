from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "experiments/sku-matching/trial_nonllm_nli_v1.py"
SPEC = importlib.util.spec_from_file_location("trial_nonllm_nli_v1", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class NonLlmJapaneseNliTrialTests(unittest.TestCase):
    def test_hypotheses_cover_frozen_condition_types(self):
        cases = [
            ({"atom_kind": "color", "selected_value_raw": "紺"}, "カラーは「紺」"),
            ({"atom_kind": "dimension", "selected_value_raw": "100×90cm"}, "サイズは「100×90cm」"),
            ({"atom_kind": "piece_total", "selected_value_raw": "2枚組", "atom_value": 2}, "枚数は2枚"),
            ({"atom_kind": "component_presence", "selected_value_raw": "なし",
              "atom_value": {"component": "lace", "value": False}}, "レースカーテンは含まれない"),
            ({"atom_kind": "named_size", "axis_name_raw": "サイズ", "selected_value_raw": "セミダブル"},
             "サイズは「セミダブル」"),
            ({"atom_kind": "variant", "axis_name_raw": "タイプ", "selected_value_raw": "フラットタイプ"},
             "タイプは「フラットタイプ」"),
            ({"atom_kind": "fabric", "axis_name_raw": "素材", "selected_value_raw": "パイル"},
             "素材は「パイル」"),
        ]
        for condition, expected in cases:
            with self.subTest(condition=condition):
                self.assertIn(expected, module.hypothesis_for(condition))

    def test_pair_construction_retains_exact_evidence_object_and_quote(self):
        evidence = {
            "quote": "幅100×丈90cm(2枚)",
            "leaf_offset": {"start": 7, "end": 20, "verified": True},
            "source_sha256": "abc123",
            "source_ref": {"raw_file": "source.json", "registry_id": "ad1"},
        }
        packet = {
            "packet_id": "p1",
            "case_id": "c1",
            "au_row_key": "r1",
            "condition": {"atom_kind": "color", "selected_value_raw": "グレー"},
            "evidence": [evidence],
        }
        pair, = module.collect_pairs([packet])
        self.assertEqual(pair["premise"], evidence["quote"])
        self.assertIs(pair["evidence_binding"], evidence)
        self.assertEqual(pair["evidence_binding"]["leaf_offset"]["start"], 7)
        self.assertIn("グレー", pair["hypothesis"])

    def test_label_mapping_requires_card_declaration_and_generic_config(self):
        card = 'Scores: {0:\"entailment\", 1:\"neutral\", 2:\"contradiction}'
        config = {"id2label": {"0": "LABEL_0", "1": "LABEL_1", "2": "LABEL_2"}}
        self.assertEqual(module.resolve_mapping(card, config),
                         ("entailment", "neutral", "contradiction"))
        with self.assertRaises(RuntimeError):
            module.resolve_mapping(card, {"id2label": {
                "0": "contradiction", "1": "entailment", "2": "neutral"}})
        with self.assertRaises(RuntimeError):
            module.resolve_mapping("no declared order", config)

    def test_confidence_threshold_is_frozen_and_maps_low_confidence_to_unknown(self):
        self.assertEqual(module.CONFIDENCE_THRESHOLD, 0.90)
        # Probabilities are generated from logits; this test protects the threshold behavior.
        result = module.probability_record([0.05, 0.0, -0.05])
        self.assertEqual(result["proposal"], "unknown")
        confident = module.probability_record([8.0, 0.0, -1.0])
        self.assertEqual(confident["proposal"], "entailment")

    def test_unknown_condition_types_fail_closed(self):
        with self.assertRaises(ValueError):
            module.hypothesis_for({"atom_kind": "unknown", "selected_value_raw": "?"})


if __name__ == "__main__":
    unittest.main()
