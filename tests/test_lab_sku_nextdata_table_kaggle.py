"""Critical transport and unknown handling for the new GPU diagnostic."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

CODE = Path(__file__).resolve().parents[1] / "experiments/sku-matching"
sys.path.insert(0, str(CODE))
import kaggle_nextdata_table_alloption_v1 as TASK
import prepare_nextdata_table_kaggle_v1 as PACKAGE


class CharacterTokenizer:
    def encode(self, text, add_special_tokens=False):
        return list(text)

    def num_special_tokens_to_add(self, pair=True):
        return 3


class AllOptionTransportTests(unittest.TestCase):
    def test_candidate_freeze_cannot_be_packaged_for_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frozen = root / "candidate.json"
            frozen.write_text(json.dumps({"root_verified": False}))
            output = root / "upload"
            with self.assertRaises(ValueError):
                PACKAGE.prepare(root / "not-yet-authoritative-input.jsonl", output, "owner", "slug", root / "jev.json", frozen, root / "proof.json")
            self.assertFalse(output.exists())

    def row(self):
        return {"pair_ref": "public-pair", "axis_name": "装飾", "option_values": ["レースあり", "レース無し"],
                "target_selected_facts": [{"axis_name": "装飾", "value": "レースあり"}], "context_header": "",
                "variants": {v: {"body_fields": [{"field_ref": "raw-body", "text": "あ\nＡ=🧵" * 230}]}
                             for v in TASK.VARIANTS}}

    def test_complete_character_coverage_and_actual_selections_in_every_chunk(self):
        row = self.row()
        chunks, issue = TASK.prepare_chunks(row, "fullbody", {k: CharacterTokenizer() for k in ("jev", "nli")})
        self.assertIsNone(issue)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(c["text"] for c in chunks), row["variants"]["fullbody"]["body_fields"][0]["text"])
        for index, chunk in enumerate(chunks):
            self.assertEqual(chunk["start"], chunks[index - 1]["end"] if index else 0)
            self.assertIn("装飾：レースあり", chunk["premise"])
            self.assertIn("レース無し", chunk["premise"])
            self.assertLessEqual(TASK.max_pair_tokens(row, chunk["premise"], {k: CharacterTokenizer() for k in ("jev", "nli")}), 512)

    def test_overlong_metadata_retained_as_explicit_unknown_without_body_fallback(self):
        row = self.row()
        row["target_selected_facts"][0]["value"] = "x" * 600
        chunks, issue = TASK.prepare_chunks(row, "tablebody", {k: CharacterTokenizer() for k in ("jev", "nli")})
        self.assertEqual(chunks, [])
        self.assertEqual(issue["reason"], "metadata_exceeds_512")
        self.assertEqual(issue["retained_body_character_count"], 1150)
        self.assertEqual(TASK.aggregate([], technical_failure=True), "unknown")

    def test_conflicting_chunks_and_technical_errors_never_automatically_prove(self):
        self.assertEqual(TASK.aggregate(["support", "conflict"]), "unknown")
        self.assertEqual(TASK.aggregate(["support"], technical_failure=True), "unknown")
        self.assertEqual(TASK.aggregate(["unknown", "unknown"]), "unknown")
        self.assertEqual(TASK.aggregate(["support", "unknown"]), "support")

    def public_source(self):
        payload = {"candidate_vector": [{"whole_option": "L"}, {"whole_option": "M"}],
                   "context": {"hypothesis": {"axis_name": "サイズ", "family_values": ["L", "M"]},
                               "target_selected_facts": [{"axis_name": "サイズ", "value": "L", "private_transport_id": "hidden-id"}],
                               "target_native_sku_inventory": {"axes": [{"axis_name": "サイズ", "members": [{"value": "L"}, {"value": "M"}]}],
                                                               "all_option_states": [{"values": [{"axis_name": "サイズ", "value": "L"}]},
                                                                                     {"values": [{"axis_name": "サイズ", "value": "M"}]}]},
                               "target_document_sha256": "a" * 64,
                               "eligible_target_body": {"document_sha256": "a" * 64, "blocks": [{"block_id": "a", "support_text": "full body"}]},
                               "eligible_source_bodies": [{"document_sha256": "a" * 64, "blocks": [{"block_id": "a", "support_text": "full body"}]}]}}
        return {"pair_ref": "ref", "full_payload": payload, "table_payload": copy.deepcopy(payload)}

    def test_adapter_keeps_all_semantic_inventory_values_and_excludes_transport_ids(self):
        row = TASK.adapt_public_pair(self.public_source())
        self.assertEqual(row["option_values"], ["L", "M"])
        self.assertEqual(row["target_selected_facts"], [{"axis_name": "サイズ", "value": "L"}])
        inventory = row["variants"]["fullbody"]["body_fields"][0]["text"]
        self.assertIn('"M"', inventory)
        self.assertNotIn("hidden-id", TASK.canonical(row))
        self.assertEqual(len(row["variants"]["fullbody"]["body_fields"]), 2)

    def test_explicit_original_full_fallback_is_preserved(self):
        source = self.public_source()
        row = TASK.adapt_public_pair(source)
        self.assertEqual(row["variants"]["fullbody"], row["variants"]["tablebody"])
        self.assertIn("full body", TASK.canonical(row["variants"]["tablebody"]))

    def test_changed_actual_selection_in_paired_variant_rejected(self):
        source = self.public_source()
        source["table_payload"]["context"]["target_selected_facts"][0]["value"] = "M"
        with self.assertRaises(ValueError):
            TASK.adapt_public_pair(source)

    def test_changed_original_document_identity_rejected(self):
        source = self.public_source()
        source["table_payload"]["context"]["eligible_target_body"]["document_sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            TASK.adapt_public_pair(source)

    def test_independent_population_freeze_rejects_partial_and_changed_vectors(self):
        rows, vectors = [], []
        for ref in range(971):
            options = [str(i) for i in range(5 if ref < 384 else 4)]
            payload = {"candidate_vector": [{"whole_option": v} for v in options],
                       "context": {"hypothesis": {"axis_name": "axis"},
                                   "eligible_target_body": {"document_sha256": "a" * 64, "blocks": []},
                                   "eligible_source_bodies": []}}
            rows.append({"pair_ref": ref, "full_payload": payload, "table_payload": copy.deepcopy(payload)})
            vectors.append({"pair_ref": ref, "axis_name": "axis", "option_values": options})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path, freeze_path, equality_path = root / "input.jsonl", root / "freeze.json", root / "equality.json"
            input_path.write_text("\n".join(TASK.canonical(row) for row in rows) + "\n")
            equality_path.write_text(json.dumps({"product_count": 46, "variant": "cleaned_description_fields_with_full_fallback",
                                                 "collector_status_counts": {"collected": 46}, "fallback_count": 0}))
            body_vector = [{"pair_ref": row["pair_ref"],
                            "eligible_source_bodies": row["full_payload"]["context"]["eligible_source_bodies"],
                            "eligible_target_body": row["full_payload"]["context"]["eligible_target_body"]} for row in rows]
            freeze = {"pair_count": 971, "product_count": 46, "case_count": 569, "option_prediction_count": 8536,
                      "public_input_sha256": TASK.sha(input_path), "pair_ref_vector_sha256": TASK.value_sha(list(range(971))),
                      "option_vector_sha256": TASK.value_sha(vectors), "sourceproof_receipt_sha256": TASK.sha(equality_path),
                      "full_body_vector_sha256": TASK.value_sha(body_vector),
                      "description_body_vector_sha256": TASK.value_sha(body_vector)}
            freeze_path.write_text(json.dumps(freeze))
            TASK.verify_root_population(input_path, rows, freeze_path, equality_path)
            with self.assertRaises(ValueError):
                TASK.verify_root_population(input_path, rows[:-1], freeze_path, equality_path)
            changed = copy.deepcopy(rows)
            changed[0]["full_payload"]["candidate_vector"][0]["whole_option"] = "different"
            with self.assertRaises(ValueError):
                TASK.verify_root_population(input_path, changed, freeze_path, equality_path)
            changed = copy.deepcopy(rows)
            changed[0]["table_payload"]["context"]["eligible_target_body"]["blocks"] = [{"block_id": "changed", "support_text": "unpinned text"}]
            with self.assertRaises(ValueError):
                TASK.verify_root_population(input_path, changed, freeze_path, equality_path)


if __name__ == "__main__":
    unittest.main()
