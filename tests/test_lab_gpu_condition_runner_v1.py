from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "experiments/sku-matching/kaggle_gpu_condition_runner_v1.py"
SPEC = importlib.util.spec_from_file_location("gpu_condition_runner_v1", RUNNER)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runner)


def packet():
    return {
        "packet_id": "p-1", "case_id": "c-1", "au_row_key": "row-1",
        "condition": {"axis_name_raw": "セット内容", "selected_value_raw": "2点セット", "atom_kind": "quantity", "atom_value": "2"},
        "context": {"au_url": "https://shop.invalid/item", "rakuten_selected_axes": ["セット内容=2点セット"], "au_selected_axes": []},
        "evidence": [{"source_kind": "title", "quote": "カバー2点セット", "scope": "current_page_title", "source_ref": {"source_side": "au", "raw_file": "raw.html", "locator": "title-1", "registry_id": "e-title", "raw_sha256": "a" * 64}, "source_sha256": "a" * 64, "leaf_offset": {"verified": True, "encoding": "decoded_raw_source", "locator": "title-1", "start": 0, "end": 8, "source_sha256": "a" * 64}},
                     {"source_kind": "description", "quote": "カバーと枕カバーの2点。", "scope": "current_page_description", "source_ref": {"source_side": "au", "raw_file": "raw.html", "locator": "desc-1", "registry_id": "e-desc", "raw_sha256": "b" * 64}, "source_sha256": "b" * 64, "leaf_offset": {"verified": True, "encoding": "decoded_raw_source", "locator": "desc-1", "start": 12, "end": 24, "source_sha256": "b" * 64}}],
    }


class ConditionRunnerTests(unittest.TestCase):
    def test_cyclic_mappings_are_bijections_and_each_relation_uses_each_digit(self):
        perms = runner.relation_permutations()
        self.assertEqual(len(perms), 3)
        for p in perms:
            self.assertEqual(set(p), set(runner.RELATIONS))
            self.assertEqual(set(p.values()), set(runner.SYMBOLS))
        for relation in runner.RELATIONS:
            self.assertEqual({p[relation] for p in perms}, set(runner.SYMBOLS))

    def test_prompt_rotates_relation_digit_mapping_and_keeps_refs_host_side(self):
        p = packet()
        a, b = runner.relation_permutations()[:2]
        prompt_a, prompt_b = runner.build_prompt(p, a), runner.build_prompt(p, b)
        self.assertIn("0=support", prompt_a)
        self.assertIn("1=support", prompt_b)
        self.assertIn("カバーと枕カバーの2点。", prompt_a)
        self.assertNotIn("row-1", prompt_a)
        self.assertNotIn("raw.html", prompt_a)
        self.assertNotIn("abc", prompt_a)
        self.assertIn("exactly one digit", prompt_a)
        self.assertIn("Absence is never negative evidence", prompt_a)

    def test_au_condition_reverses_evidence_direction_and_is_atom_only(self):
        p = packet()
        p["condition"].update(side="au", axis_name_raw="色", atom_kind="color", atom_value="グレー",
                              selected_value_raw="グレー / 2点セット")
        prompt = runner.build_prompt(p, runner.relation_permutations()[0])
        self.assertIn("current Rakuten product evidence", prompt)
        self.assertIn('Atom value: "グレー"', prompt)
        self.assertIn("full axis value; context only", prompt)
        self.assertIn("グレー / 2点セット", prompt)

    def test_packet_rejects_malformed_block_and_nonbijection(self):
        p = packet()
        p["evidence"][0].pop("source_ref")
        with self.assertRaisesRegex(ValueError, "missing required"):
            runner.validate_packet(p)
        with self.assertRaisesRegex(ValueError, "bijection"):
            runner.build_prompt(packet(), {"support": "0", "conflict": "0", "unknown": "2"})
        p = packet()
        p["evidence"][0]["source_sha256"] = "not-a-sha"
        with self.assertRaisesRegex(ValueError, "64 hexadecimal"):
            runner.validate_packet(p)
        p = packet()
        p["evidence"][0]["leaf_offset"] = {"verified": False, "encoding": "decoded_raw_source", "locator": "title-1", "start": 0, "end": 8, "source_sha256": "a" * 64}
        with self.assertRaisesRegex(ValueError, "verified structured"):
            runner.validate_packet(p)
        p = packet()
        p["evidence"][0]["leaf_offset"]["locator"] = ""
        with self.assertRaisesRegex(ValueError, "concrete quote span"):
            runner.validate_packet(p)
        p = packet()
        p["evidence"][0]["leaf_offset"]["source_sha256"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "hashes do not agree"):
            runner.validate_packet(p)

    def test_noncurrent_scope_is_detected_as_out_of_scope(self):
        self.assertTrue(runner._scope_is_noncurrent("series_sibling_link"))
        self.assertFalse(runner._scope_is_noncurrent("current_page_title"))

    def test_verified_locator_objects_are_accepted(self):
        p = packet()
        p["evidence"][0]["leaf_offset"]["locator"] = {"kind": "jsonl_leaf", "file": "input.jsonl", "line": 7, "json_path": "$.title"}
        p["evidence"][0]["source_ref"]["locator"] = {"kind": "registry_entry", "id": "e-title"}
        runner.validate_packet(p)

    def test_record_hash_covers_complete_prediction_payload(self):
        rec = {"packet_id": "p-1", "aggregate_relation": "unknown", "permutations": [], "state": "complete"}
        digest = runner.canonical_sha(rec)
        altered = {**rec, "aggregate_relation": "support"}
        self.assertNotEqual(digest, runner.canonical_sha(altered))
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "predictions.jsonl"
            out.write_text(json.dumps({**rec, "record_sha256": digest}) + "\n")
            self.assertEqual(hashlib.sha256(out.read_bytes()).hexdigest(), runner.sha256_file(out))

    def test_cpu_logits_metadata_tracks_legal_mass_and_permutation_relation(self):
        try:
            import torch
        except ImportError:
            self.skipTest("torch not installed")
        mapping = runner.relation_permutations()[1]
        logits = torch.tensor([2.0, 1.0, 0.0, -1.0, -2.0])
        row = runner.classify_logits(logits, mapping, {"0": 0, "1": 1, "2": 2})
        self.assertEqual(row["restricted_argmax_digit"], "0")
        self.assertEqual(row["permutation_relation"], {v: k for k, v in mapping.items()}["0"])
        self.assertAlmostEqual(row["full_vocab_legal_digit_mass"], float(torch.softmax(logits, dim=-1)[:3].sum()))
        self.assertAlmostEqual(row["full_vocab_logsumexp"], float(torch.logsumexp(logits, dim=-1)))
        self.assertNotIn("full_raw_logits", row)

    def test_timeout_preserves_every_packet_with_complete_record_schema(self):
        try:
            import torch
        except ImportError:
            self.skipTest("torch not installed")
        packets = [packet(), packet()]
        packets[1]["packet_id"] = "p-2"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_path, output_dir = root / "packets.jsonl", root / "out"
            input_path.write_text("".join(json.dumps(p) + "\n" for p in packets))
            fake_meta = {"hf_quantization_config": {"load_in_4bit": True, "bnb_4bit_quant_type": "nf4"}}
            with patch.object(runner, "load_model", return_value=(torch, object(), object(), fake_meta, {"0": 1, "1": 2, "2": 3})), \
                 patch.object(runner.time, "monotonic", side_effect=[0.0, 2.0, 3.0, 4.0]):
                summary = runner.run(input_path, output_dir, timeout=1.0)
            rows = runner.read_jsonl(output_dir / "predictions-qwen3-5-4b-nf4-condition-v1.jsonl")
            self.assertEqual(len(rows), 2)
            self.assertEqual(summary["completed_records"], 2)
            for i, row in enumerate(rows):
                self.assertEqual(row["packet_sha256"], runner.canonical_sha(packets[i]))
                for key in ("index", "nf4_config", "permutations", "memory", "prompt_sha256", "input_token_ids_sha256", "record_sha256"):
                    self.assertIn(key, row)
                self.assertEqual(row["state"], "timeout")


if __name__ == "__main__":
    unittest.main()
