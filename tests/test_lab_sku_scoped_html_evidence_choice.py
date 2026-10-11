"""Synthetic structural tests; real-label scoring happens after inference."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("scoped_html_evidence_choice", HERE / "trial_scoped_html_evidence_choice_v2.py")
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)
import test_lab_sku_scoped_html_evidence as F


def previous_fixture(root):
    prepared, previous = root / "prepared", root / "previous"
    F.prepare(prepared)
    with patch.object(R.JEV, "_infer", side_effect=F.fake_jev_infer), patch.object(R.NLI, "_infer", side_effect=F.fake_nli_infer):
        R.V1.run(prepared, previous, reranker_factory=F.FakeReranker,
                 jev_runner=R.JEV.run_choices, nli_runner=R.NLI.run_pairs)
    return prepared, previous


class EvidenceChoiceContracts(unittest.TestCase):
    def test_sorted_json_score_roundtrip_restores_only_choice_mapping_order(self):
        rows = [{"id": "synthetic", "question": "どれですか", "state": "原文",
                 "choices": [{"label": value, "description": "原条件"} for value in ("ゼット", "アール", "未知")]}]
        with patch.object(R.JEV, "_infer", side_effect=F.fake_jev_infer):
            original = R.JEV.run_choices(rows)["records"][0]
        stored = json.loads(json.dumps(original, ensure_ascii=False, sort_keys=True))
        self.assertNotEqual(list(stored["token_counts"]), [c["label"] for c in stored["choices"]])
        restored = R.restore_choice_score_order(stored)
        R.JEV._validate_scores(restored, restored)
        self.assertEqual(restored, stored)  # Mapping equality checks every original value.
        self.assertEqual(list(restored["token_counts"]), [c["label"] for c in stored["choices"]])
        with patch.object(R.JEV, "_infer") as inference:
            replay = R.JEV.run_choices(rows, cache={restored["cache_key"]: restored})
        self.assertEqual(inference.call_count, 0)
        self.assertEqual(replay["records"][0]["argmax_label"], original["argmax_label"])

    def test_score_order_restoration_rejects_key_tampering_and_still_checks_numeric_scores(self):
        rows = [{"id": "synthetic", "question": "どれですか", "state": "原文",
                 "choices": [{"label": value, "description": "原条件"} for value in ("ゼット", "アール", "未知")]}]
        with patch.object(R.JEV, "_infer", side_effect=F.fake_jev_infer):
            record = R.JEV.run_choices(rows)["records"][0]
        for field in ("logits", "probabilities", "token_counts"):
            with self.subTest(field=field):
                tampered = deepcopy(record)
                tampered[field]["偽候補"] = tampered[field].pop("ゼット")
                with self.assertRaisesRegex(ValueError, "score keys"):
                    R.restore_choice_score_order(tampered)
        tampered = deepcopy(record)
        tampered["logits"]["ゼット"] += 1
        restored = R.restore_choice_score_order(tampered)
        with self.assertRaisesRegex(ValueError, "softmax"):
            R.JEV._validate_scores(restored, restored)

    def test_scope_model_input_has_no_desired_value_and_preserves_raw_choices_as_metadata(self):
        requests, documents, _, _, source_blocks, _ = F.fixtures()
        by_doc = {d["document_id"]: d for d in documents}
        blocks = {b["block_id"]: b for b in source_blocks}
        rankings = {r["request_id"]: {"ordered_block_ids": list(blocks)} for r in requests[:2]}
        rows, skips = R.evidence_requests(requests, by_doc, blocks, rankings)
        self.assertEqual(skips, {})
        self.assertEqual(rows[0]["question"], rows[1]["question"])
        self.assertEqual(rows[0]["state"], rows[1]["state"])
        self.assertEqual(rows[0]["choices"], rows[1]["choices"])
        self.assertIn("現在のAU商品ID", rows[0]["state"])
        self.assertIn("色：青", rows[0]["state"])
        self.assertNotIn(requests[0]["selected_value"], rows[0]["question"])
        self.assertEqual(rows[0]["all_original_option_values"], requests[0]["option_values"])
        self.assertEqual(rows[0]["choices"][0]["description"], source_blocks[0]["model_passage"])

    def test_fixed_top_ten_and_unknown_control_preserve_rank_order_without_filtering_terms(self):
        requests, documents, _, _, source_blocks, _ = F.fixtures()
        blocks = {"block-" + str(i): {**source_blocks[0], "block_id": "block-" + str(i)} for i in range(12)}
        ordered = list(reversed(list(blocks)))
        rankings = {requests[0]["request_id"]: {"ordered_block_ids": ordered}}
        rows, _ = R.evidence_requests(requests[:1], {d["document_id"]: d for d in documents}, blocks, rankings)
        self.assertEqual(rows[0]["candidate_block_ids"], ordered[:10])
        self.assertEqual([c["label"] for c in rows[0]["choices"]], ["記述" + str(i + 1) for i in range(10)] + [R.EVIDENCE_UNKNOWN])
        self.assertEqual(rows[0]["evidence_label_to_block_id"], {"記述" + str(i + 1): bid for i, bid in enumerate(ordered[:10])})
        self.assertEqual(R.TOP_K, 10)

    def test_unknown_tie_and_error_scope_scores_prevent_value_inference(self):
        requests, documents, _, _, source_blocks, _ = F.fixtures()
        request = requests[0]
        blocks = {b["block_id"]: b for b in source_blocks}
        rankings = {request["request_id"]: {"ordered_block_ids": list(blocks)}}
        rows, _ = R.evidence_requests([request], {d["document_id"]: d for d in documents}, blocks, rankings)
        for record, reason in (({"status": "ok", "logits": {"記述1": 1, R.EVIDENCE_UNKNOWN: 2}, "argmax_label": R.EVIDENCE_UNKNOWN}, "evidence_unknown_control"),
                               ({"status": "ok", "logits": {"記述1": 2, R.EVIDENCE_UNKNOWN: 2}, "argmax_label": "記述1"}, "evidence_score_tie"),
                               ({"status": "input_too_long"}, "evidence_input_too_long")):
            with self.subTest(reason=reason):
                selected, skips, _ = R.choose_evidence([request], [{**rows[0], **record}], rankings,
                                                       {d["document_id"]: d for d in documents}, blocks, {})
                self.assertEqual(selected, {})
                self.assertEqual(skips[request["request_id"]], reason)

    def test_opaque_block_ids_are_metadata_only_and_do_not_collide_with_control_label(self):
        requests, documents, _, _, blocks, _ = F.fixtures()
        rows, _ = R.evidence_requests(requests[:1], {d["document_id"]: d for d in documents},
                                     {R.EVIDENCE_UNKNOWN: blocks[0]},
                                     {requests[0]["request_id"]: {"ordered_block_ids": [R.EVIDENCE_UNKNOWN]}})
        self.assertEqual(rows[0]["evidence_label_to_block_id"], {"記述1": R.EVIDENCE_UNKNOWN})
        self.assertEqual([c["label"] for c in rows[0]["choices"]], ["記述1", R.EVIDENCE_UNKNOWN])

    def test_tampered_ordinal_mapping_is_rejected_before_source_block_selection(self):
        requests, documents, _, _, source_blocks, _ = F.fixtures()
        blocks = {b["block_id"]: b for b in source_blocks}
        rankings = {requests[0]["request_id"]: {"ordered_block_ids": list(blocks)}}
        by_doc = {d["document_id"]: d for d in documents}
        rows, _ = R.evidence_requests(requests[:1], by_doc, blocks, rankings)
        record = {**rows[0], "evidence_label_to_block_id": {"記述1": "wrong-block"},
                  "status": "ok", "logits": {"記述1": 2, R.EVIDENCE_UNKNOWN: 1}, "argmax_label": "記述1"}
        with self.assertRaisesRegex(ValueError, "ordinal labels"):
            R.choose_evidence(requests[:1], [record], rankings, by_doc, blocks, {})

    def test_completed_previous_source_and_model_caches_are_verified(self):
        with tempfile.TemporaryDirectory() as td:
            prepared, previous = previous_fixture(Path(td))
            requests, _, _, _, blocks, retrieval, _ = R.V1.load_inputs(prepared)
            rankings, jev_cache, nli_cache, summary = R.load_previous(previous, prepared, requests, blocks, retrieval)
            self.assertEqual(len(rankings), 2)
            self.assertEqual(len(jev_cache), 1)
            self.assertEqual(len(nli_cache), 2)
            self.assertFalse(summary["labels_read"])

    def test_previous_selection_tampering_is_rejected_before_inference(self):
        with tempfile.TemporaryDirectory() as td:
            prepared, previous = previous_fixture(Path(td))
            requests, _, _, _, blocks, retrieval, _ = R.V1.load_inputs(prepared)
            with (previous / "retrieval-selections.jsonl").open("a") as handle:
                handle.write("{}\n")
            with self.assertRaisesRegex(ValueError, "previous output SHA256"):
                R.load_previous(previous, prepared, requests, blocks, retrieval)

    def test_missing_previous_completion_is_not_treated_as_ready(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileNotFoundError):
                R.load_previous(Path(td), Path(td), [], {}, [])

    def test_full_run_reuses_value_and_nli_exact_caches_and_preserves_reverse_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared, previous = previous_fixture(root)
            output = root / "v2"
            with patch.object(R.JEV, "_infer", side_effect=F.fake_jev_infer) as ji, patch.object(R.NLI, "_infer", side_effect=F.fake_nli_infer) as ni:
                summary = R.run(prepared, previous, output, jev_runner=R.JEV.run_choices, nli_runner=R.NLI.run_pairs)
            self.assertEqual(ji.call_count, 1)
            self.assertEqual(ni.call_count, 0)
            self.assertEqual(summary["evidence_choice_runtime"]["unique_request_count"], 1)
            self.assertEqual(summary["value_choice_runtime"]["previously_cached_unique_count"], 1)
            self.assertEqual(summary["previously_cached_unique_nli_count"], 2)
            self.assertEqual(summary["nli_pair_count"], 2)
            self.assertFalse(summary["labels_read"])
            self.assertFalse(summary["new_reranker_inference"])
            self.assertEqual((output / "baseline-predictions.jsonl").read_bytes(), (prepared / "baseline-predictions.jsonl").read_bytes())
            old = R.read(prepared / "baseline-predictions.jsonl")
            for name in ("structured-jev", "structured-verified"):
                rows = R.read(output / (name + "-predictions.jsonl"))
                self.assertEqual([r["state"] for r in rows], ["support", "unknown", "support"])
                self.assertEqual(rows[-1], old[-1])
                self.assertEqual(rows[0]["source_task_bindings"], old[0]["source_task_bindings"])
                self.assertEqual(rows[0]["evidence_choice_prediction"]["block_id"], "synthetic-block")
                self.assertFalse(rows[0]["production_adoption_allowed"])
            # Stage two uses the original v1 input verbatim when the block is unchanged.
            old_choices, new_choices = R.read(previous / "model-requests.jsonl"), R.read(output / "model-requests.jsonl")
            self.assertEqual(new_choices, old_choices)

    def test_scope_unknown_produces_complete_binary_drop_predictions_without_value_or_nli(self):
        def scope_unknown(requests, model_dir, threads, batch_size, on_ready):
            records, pins, runtime = F.fake_jev_infer(requests, model_dir, threads, batch_size, on_ready)
            for row, record in zip(requests, records, strict=True):
                labels = [c["label"] for c in row["choices"]]
                logits = [0] * (len(labels) - 1) + [2]
                probabilities = R.JEV._softmax(logits)
                record.update(logits=dict(zip(labels, logits, strict=True)), probabilities=dict(zip(labels, probabilities, strict=True)),
                              argmax_label=labels[-1], argmax_index=len(labels) - 1, argmax_probability=probabilities[-1])
            return records, pins, runtime
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared, previous = previous_fixture(root)
            with patch.object(R.JEV, "_infer", side_effect=scope_unknown) as ji, patch.object(R.NLI, "_infer") as ni:
                summary = R.run(prepared, previous, root / "out", jev_runner=R.JEV.run_choices, nli_runner=R.NLI.run_pairs)
            self.assertEqual(ji.call_count, 1)
            self.assertEqual(ni.call_count, 0)
            self.assertEqual(summary["value_choice_runtime"]["request_count"], 0)
            self.assertEqual(summary["evidence_choice_reasons"], {"evidence_unknown_control": 2})
            rows = R.read(root / "out/structured-verified-predictions.jsonl")
            self.assertEqual(len(rows), 3)
            self.assertEqual([row["state"] for row in rows[:2]], ["unknown", "unknown"])
            self.assertTrue(all(row["winning_value"] is None and row["quote_refs"] == [] for row in rows[:2]))

    def test_shared_jev_loader_context_covers_both_scoring_phases(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared, previous = previous_fixture(root)
            with patch.object(R.FULL, "reuse_jev_loader") as loader, patch.object(R.FULL, "reuse_nli_factories"),\
                    patch.object(R.JEV, "_infer", side_effect=F.fake_jev_infer), patch.object(R.NLI, "_infer", side_effect=F.fake_nli_infer):
                R.run(prepared, previous, root / "out")
            self.assertEqual(loader.call_count, 1)

    def test_existing_output_and_nonfrozen_settings_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(FileExistsError):
                R.run(root, root, root)
            with self.assertRaisesRegex(ValueError, "frozen CPU"):
                R.run(root, root, root / "out", threads=2)


if __name__ == "__main__":
    unittest.main()
