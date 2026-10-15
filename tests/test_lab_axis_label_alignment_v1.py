from __future__ import annotations

import importlib.util
import json
import hashlib
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "experiments/sku-matching/align_axis_labels_v1.py"
spec = importlib.util.spec_from_file_location("axis_alignment_v1", SCRIPT)
axis = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(axis)


def pair(au, rk, pair_id="p"):
    return {"pair_id": pair_id, "au_axes": au, "rakuten_axes": rk}


def ax(axis_id, label):
    return {"axis_id": axis_id, "label": label}


class AxisAlignmentTests(unittest.TestCase):
    def test_gate_skips_zero_and_more_au_axes(self):
        zero = axis.align_pair(pair([], [ax("r", "色")]), lambda *_: self.fail("scorer called"))
        many = axis.align_pair(pair([ax("a", "色"), ax("b", "サイズ")], [ax("r", "色")]), lambda *_: self.fail("scorer called"))
        self.assertEqual(zero["skip_reason"], "zero_au_axes")
        self.assertEqual(many["skip_reason"], "au_axis_count_exceeds_rakuten")

    def test_unmatched_counterpart_still_gets_forced_top1_and_candidate_only(self):
        result = axis.align_pair(pair([ax("a", "包装")], [ax("r1", "色"), ax("r2", "サイズ")]),
                                 lambda _a, b: {"色": .9, "サイズ": .2}[b])
        self.assertEqual(result["mappings"][0]["best"]["axis_id"], "r1")
        self.assertFalse(result["mappings"][0]["semantic_equivalence_verified"])
        self.assertEqual(result["asymmetry_candidates"], [
            {"axis_id": "r2", "label": "サイズ", "status": "candidate_only",
             "semantic_equivalence_verified": False}])

    def test_collision_is_explicit(self):
        result = axis.align_pair(pair([ax("a1", "色名"), ax("a2", "カラー")],
                                      [ax("r1", "色"), ax("r2", "カラー")]),
                                 lambda _a, b: {"色": .5, "カラー": .2}[b])
        self.assertTrue(all(m["collision"] for m in result["mappings"]))
        self.assertEqual(result["mappings"][0]["collision_au_axis_ids"], ["a1", "a2"])

    def test_tie_preserves_distinct_target_ids_in_order(self):
        result = axis.align_pair(pair([ax("a", "色")], [ax("r1", "カラー"), ax("r2", "色名")]),
                                 lambda *_: .7)
        mapping = result["mappings"][0]
        self.assertEqual(mapping["best"]["axis_id"], "r1")
        self.assertTrue(mapping["tie"])
        self.assertEqual([x["axis_id"] for x in mapping["tied_candidates"]], ["r1", "r2"])
        self.assertEqual(mapping["exact_raw_label_baseline"]["status"], "no_exact_match")

    def test_duplicate_label_tie_is_ambiguous_not_asymmetry(self):
        result = axis.align_pair(
            pair([ax("a", "色")], [ax("r1", "色"), ax("r2", "色")]),
            lambda *_: .9,
        )
        self.assertEqual(result["mappings"][0]["best"]["axis_id"], "r1")
        self.assertEqual(result["asymmetry_candidates"], [])
        self.assertEqual(result["ambiguous_rakuten_axes"], [
            {"axis_id": "r1", "label": "色", "au_axis_ids": ["a"],
             "semantic_equivalence_verified": False},
            {"axis_id": "r2", "label": "色", "au_axis_ids": ["a"],
             "semantic_equivalence_verified": False},
        ])
        self.assertEqual(result["mappings"][0]["exact_raw_label_baseline"]["status"], "tie")

    def test_missing_label_is_unknown_but_axes_still_count_for_gate(self):
        result = axis.align_pair(pair([ax("a", " ")], [ax("r", "色")]), lambda *_: self.fail("scorer called"))
        self.assertEqual(result["status"], "eligible")
        self.assertEqual(result["au_axis_count"], 1)
        self.assertEqual(result["mappings"][0]["status"], "unknown_label")
        self.assertEqual(result["asymmetry_candidates"][0]["axis_id"], "r")

    def test_noncanonical_records_rejected(self):
        duplicate = pair([ax("a", "色"), ax("a", "サイズ")], [ax("r", "色")])
        with self.assertRaisesRegex(ValueError, "duplicate axis_id"):
            axis.align_pair(duplicate, lambda *_: .1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.jsonl"
            path.write_text('{"pair_id":"p","au_axes":[],"rakuten_axes":[]}\n{"oops"}\n')
            with self.assertRaisesRegex(ValueError, "invalid JSON"):
                axis._read_records(path)

    def test_encoder_receives_unique_labels_only_and_existing_output_refused(self):
        class FakeModel:
            MAX_LENGTH = 128
            def token_lengths(self, texts):
                return [1] * len(texts)
            def encode(self, texts, batch_size):
                seen.append(list(texts))
                return [[1.0, 0.0] for _ in texts]

        seen = []
        record = pair([dict(axis_id="a", label="色", values=["red"], source_ref="private")],
                      [ax("r", "カラー")], "pair")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inp, out = root / "in.jsonl", root / "out"
            inp.write_text(json.dumps(record, ensure_ascii=False) + "\n")
            fake_manifest = {"repo": "fake", "revision": "pinned"}
            def pure_predictions(records, _embeddings):
                return [axis.align_pair(record, lambda _left, _right: .8) for record in records]

            with (patch.object(axis, "_model", return_value=(FakeModel(), fake_manifest)),
                  patch.object(axis, "align_records", side_effect=pure_predictions)):
                summary = axis.run(inp, out, model_name="minilm")
            self.assertEqual(seen, [["カラー", "色"]])
            output = (out / "predictions.jsonl").read_text()
            self.assertNotIn("private", output)
            self.assertNotIn("red", output)
            self.assertTrue(summary["label_only"])
            self.assertRegex(summary["python_version"], re.compile(r"^\d+\.\d+"))
            self.assertEqual(set(summary["installed_package_versions"]),
                             {"numpy", "onnxruntime", "tokenizers", "torch", "transformers"})
            self.assertEqual(summary["label_token_lengths"], [1, 1])
            self.assertEqual(summary["label_max_token_length"], 1)
            self.assertEqual(summary["model_max_token_length"], 128)
            self.assertEqual(summary["elapsed_scope"],
                             "run start through predictions.jsonl serialization and SHA-256; excludes summary.json serialization")
            self.assertEqual(summary["predictions_sha256"],
                             hashlib.sha256((out / "predictions.jsonl").read_bytes()).hexdigest())
            self.assertEqual(summary["exact_baseline_matched_axis_count"], 0)
            self.assertEqual(summary["exact_baseline_agreement_denominator"], 0)
            self.assertEqual(summary["unknown_label_axis_count"], 0)
            self.assertTrue(summary["candidate_only"])
            self.assertFalse(summary["automatic_adoption_allowed"])
            with self.assertRaises(FileExistsError):
                axis.run(inp, out, model_name="minilm")


if __name__ == "__main__":
    unittest.main()
