from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments/sku-matching/trial_cpu_requirement_relations_v1.py"
SPEC = importlib.util.spec_from_file_location("cpu_requirement_relations_v1", SCRIPT)
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class CpuRequirementRelationsV1Tests(unittest.TestCase):
    def test_input_format_keeps_literal_quote_binding(self):
        row = {
            "task_id": "task-1", "case_id": "case-1", "au_row_key": "au-1",
            "requirement": {"type": "component_presence", "value": True, "component": "bar",
                            "axis_label": "付属品", "axis_value_quote": "バー付き"},
            "hypothesis": "この商品にはバーが付いている。",
            "evidence": {"evidence_id": "ev-1", "quote": "便利なフックとバー付きなので使いやすい",
                         "span": {"start": 10, "end": 30, "quote": "便利なフックとバー付きなので使いやすい"},
                         "source_scope": {"verified": True}},
            "source_scope": {"verified": True, "kind": "literal_quote"},
        }
        RUNNER.validate_task(row)
        record = RUNNER.make_record(row, {"support": 0.95, "conflict": 0.03, "unknown": 0.02},
                                   "support", 0.95, 0.1, 40, "nli")
        self.assertEqual(record["proposal"], "support")
        self.assertEqual(record["evidence_binding"]["quote"], row["evidence"]["quote"])
        self.assertEqual(record["evidence_binding"]["source_scope"], row["source_scope"])

    def test_nli_mapping_collapses_neutral_to_unknown(self):
        probabilities, winner, confidence = RUNNER.relation_probabilities([4.0, 1.0, 0.0])
        self.assertEqual(winner, "support")
        self.assertAlmostEqual(sum(probabilities.values()), 1.0)
        probabilities, winner, _ = RUNNER.relation_probabilities([0.0, 4.0, 1.0])
        self.assertEqual(winner, "unknown")

    def test_nonfinite_nli_logits_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            RUNNER.relation_probabilities([0.0, float("nan"), 1.0])

    def test_gliner_native_probabilities_map_to_relation_classes(self):
        source = {RUNNER.GLINER_CHOICES[0]: 0.91,
                  RUNNER.GLINER_CHOICES[1]: 0.03,
                  RUNNER.GLINER_CHOICES[2]: 0.06}
        probabilities, winner, confidence = RUNNER.normalize_gliner_probabilities(source)
        self.assertEqual(winner, "support")
        self.assertEqual(probabilities["conflict"], 0.03)
        self.assertAlmostEqual(confidence, 0.91)
        source[RUNNER.GLINER_CHOICES[2]] = float("inf")
        with self.assertRaisesRegex(ValueError, "finite"):
            RUNNER.normalize_gliner_probabilities(source)

    def test_component_mode_extracts_japanese_noun_and_inverts_negative_requirement(self):
        hypothesis = "この商品にはバーが付いている。"
        noun = RUNNER.component_noun_from_hypothesis(hypothesis)
        self.assertEqual(noun, "バー")
        self.assertEqual(RUNNER.component_schema_instruction(hypothesis, noun),
                         "引用文だけを根拠に、仮説「この商品にはバーが付いている。」について「バー」の状態を選んでください。")
        present_map = RUNNER.component_relation_map(True)
        absent_map = RUNNER.component_relation_map(False)
        self.assertEqual(present_map[RUNNER.COMPONENT_CHOICES[0]], "support")
        self.assertEqual(present_map[RUNNER.COMPONENT_CHOICES[1]], "conflict")
        self.assertEqual(absent_map[RUNNER.COMPONENT_CHOICES[0]], "conflict")
        self.assertEqual(absent_map[RUNNER.COMPONENT_CHOICES[1]], "support")
        self.assertEqual(absent_map[RUNNER.COMPONENT_CHOICES[2]], "unknown")
        self.assertEqual(RUNNER.component_noun_from_hypothesis("この商品にはレースカーテンが付いていない。"),
                         "レースカーテン")
        probs, winner, confidence = RUNNER.normalize_component_probabilities(
            {RUNNER.COMPONENT_CHOICES[0]: 0.85,
             RUNNER.COMPONENT_CHOICES[1]: 0.10,
             RUNNER.COMPONENT_CHOICES[2]: 0.05}, False)
        self.assertEqual(winner, "conflict")
        self.assertAlmostEqual(probs["conflict"], 0.85)
        self.assertAlmostEqual(confidence, 0.85)

    def test_oversize_guard_does_not_truncate_boundary(self):
        self.assertFalse(RUNNER.exceeds_token_limit(512))
        self.assertTrue(RUNNER.exceeds_token_limit(513))
        self.assertEqual(RUNNER.MAX_TOKENS, 512)

    def test_nli_pinned_file_hashes_are_sha256_and_match_frozen_vocab_pin(self):
        for digest in RUNNER.NLI_FILE_SHA256.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(
            RUNNER.NLI_FILE_SHA256["vocab.txt"],
            "5e9a696b0191b833cfdf8eefada01f41f23ccbd7e7746946864260b1cdd0a784",
        )

    def test_span_literal_mismatch_is_rejected(self):
        row = {"task_id": "t", "case_id": "c", "au_row_key": "r",
               "requirement": {"type": "x", "value": "y", "component": None,
                               "axis_label": "z", "axis_value_quote": "y"},
               "hypothesis": "仕様は「y」である。",
               "source_scope": {"verified": True, "kind": "literal_quote"},
               "evidence": {"evidence_id": "e", "quote": "引用文", "span": {"quote": "別の文"}}}
        with self.assertRaisesRegex(ValueError, "exactly equal"):
            RUNNER.validate_task(row)


if __name__ == "__main__":
    unittest.main()
