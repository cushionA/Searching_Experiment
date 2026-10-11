"""Synthetic contract fixtures only; no performance labels or model downloads."""
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
SPEC = importlib.util.spec_from_file_location("scoped_html_evidence", HERE / "trial_scoped_html_evidence_v1.py")
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def fixtures():
    values = ["仕様A / 長さ80cm", "仕様B / 長さ90cm"]
    doc = {"document_id": "synthetic-doc", "title": {"text": "元の商品"},
           "description": {"text": "内容：仕様A / 長さ80cm。別商品へのリンクも残す。", "complete": True}}
    doc["document_sha256"] = R.digest(doc)
    requests, tasks, baseline = [], [], []
    for index, (selected, direction) in enumerate([(v, R.FORWARD) for v in values] + [(values[0], "au_to_rakuten")]):
        semantic = {"direction": direction, "axis_name": "販売仕様", "selected_value": selected,
                    "option_values": values, "target_document_id": doc["document_id"],
                    "target_document_sha256": doc["document_sha256"],
                    "target_selected_axes": [{"axis_name": "色", "value": "青"}],
                    "target_selected_axes_text": "色：青"}
        identity = R.digest(semantic)
        request = {**semantic, "request_sha256": identity, "request_id": "generic-description:" + identity}
        task = {"task_id": "synthetic-task-" + str(index), "request_id": request["request_id"],
                "direction": direction, "option_values": values,
                "raw_condition": {"axis_label": "販売仕様", "value": selected}, "case_id": "synthetic-case"}
        requests.append(request)
        tasks.append(task)
        baseline.append({"request_id": request["request_id"], "request_sha256": identity, "selected_value": selected,
                         "direction": direction, "source_request": request, "source_task_bindings": [task],
                         "state": "support", "winning_value": selected, "quote_refs": [],
                         "scope_status": "unproved", "production_adoption_allowed": False,
                         "variant": "old-variant", "old-diagnostic": {"untouched": True}})
    block = {"block_id": "synthetic-block", "target_document_id": doc["document_id"],
             "text": doc["description"]["text"], "context": [], "links": [{"text": "別商品", "href": "/item/other"}],
             "model_passage": "内容表\n" + doc["description"]["text"] + "\nリンク：別商品 /item/other",
             "quote_refs": [R.FULL.quote_ref(doc, "description", doc["description"]["text"])]}
    retrieval = [{"id": "synthetic-retrieval-" + str(i), "request_id": r["request_id"], "block_id": block["block_id"],
                  "target_document_id": doc["document_id"], "query": R.query_for(r, doc),
                  "passage": block["model_passage"], "quote_refs": block["quote_refs"]}
                 for i, r in enumerate(requests) if r["direction"] == R.FORWARD]
    return requests, [doc], tasks, baseline, [block], retrieval


def prepare(directory, data=None):
    directory.mkdir()
    for name, rows in zip(R.SOURCE_NAMES, data or fixtures(), strict=True):
        R.write_rows(directory / name, rows)
    R.write_json(directory / "manifest.json", {"labels_read": False, "labels_in_model_inputs": False,
                 "errors_only": True, "development_only": True, "independent_test": False,
                 "output_sha256": {name: R.sha(directory / name) for name in R.SOURCE_NAMES}})


class FakeProgress:
    def emit(self, **kwargs):
        pass


class FakeReranker:
    def __init__(self, *args, **kwargs):
        self.metadata = json.loads((HERE / "manifests/reranker.json").read_text())
        self.scored = []

    def token_lengths_pairs(self, pairs):
        return [513 if "TOO_LONG" in passage else 50 for _, passage in pairs]

    def score_pairs(self, pairs, batch_size):
        self.scored.extend(pairs)
        return [float(len(passage)) for _, passage in pairs]


def fake_jev_infer(requests, model_dir, threads, batch_size, on_ready):
    pins, runtime = R.JEV._pins(), {"inference_seconds": .01}
    on_ready(pins, runtime)
    records = []
    for row in requests:
        labels = [c["label"] for c in row["choices"]]
        logits = list(range(len(labels), 0, -1))
        probabilities = R.JEV._softmax(logits)
        records.append({"cache_key": row["cache_key"], "status": "ok", "logits": dict(zip(labels, logits, strict=True)),
                        "probabilities": dict(zip(labels, probabilities, strict=True)), "argmax_label": labels[0],
                        "argmax_index": 0, "argmax_probability": probabilities[0],
                        "token_counts": {v: 70 for v in labels}, "latency_seconds": .01, "note": None, "error_type": None})
    return records, pins, runtime


def fake_nli_infer(pairs, model_dir, threads, batch_size, on_ready):
    pins = {"model_id": R.NLI.MODEL_ID, "revision": R.NLI.MODEL_REVISION,
            "parameter_count": R.NLI.FP32_PARAMETER_COUNT}
    runtime = {"inference_seconds": .01}
    on_ready(pins, runtime)
    return [{"task_id": p["cache_key"], "probabilities": {"support": .6 if p["option_index"] == 0 else .1,
              "conflict": .2 if p["option_index"] == 0 else .8, "unknown": .2 if p["option_index"] == 0 else .1},
             "token_count_untruncated": 75, "latency_seconds": .01, "note": None, "error_type": None}
            for p in pairs], pins, runtime


class StructuredEvidenceContracts(unittest.TestCase):
    def test_retrieval_query_is_symmetric_and_keeps_every_whole_option(self):
        requests, documents, *_ = fixtures()
        left, right = [R.query_for(r, documents[0]) for r in requests[:2]]
        self.assertEqual(left, right)
        self.assertTrue(left.endswith("\n".join(requests[0]["option_values"])))
        self.assertEqual(left.count(requests[0]["selected_value"]), 1)

    def test_model_state_preserves_table_context_and_link_scope_without_desired_injection(self):
        requests, documents, _, _, blocks, _ = fixtures()
        self.assertEqual(R.state_for(requests[0], documents[0], blocks[0]), R.state_for(requests[1], documents[0], blocks[0]))
        state = R.state_for(requests[0], documents[0], blocks[0])
        self.assertIn("内容表", state)
        self.assertIn("/item/other", state)
        self.assertIn("色：青", state)
        self.assertNotIn(requests[1]["selected_value"], state)

    def test_complete_input_coverage_and_raw_bindings_are_validated(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "inputs"
            prepare(directory)
            actual = R.load_inputs(directory)
            self.assertEqual(len(actual[0]), 3)
            self.assertEqual(len(actual[5]), 2)

    def test_tampered_input_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "inputs"
            prepare(directory)
            with (directory / "blocks.jsonl").open("a") as handle:
                handle.write("{}\n")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                R.load_inputs(directory)

    def test_rehashed_desired_value_injected_query_is_rejected(self):
        data = fixtures()
        data[5][0]["query"] += "\n希望値：" + data[0][0]["selected_value"]
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "inputs"
            prepare(directory, data)
            with self.assertRaisesRegex(ValueError, "symmetric source renderer"):
                R.load_inputs(directory)

    def test_missing_request_block_pair_is_rejected(self):
        data = fixtures()
        data[5].pop()
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "inputs"
            prepare(directory, data)
            with self.assertRaisesRegex(ValueError, "all forward"):
                R.load_inputs(directory)

    def test_baseline_bindings_are_compared_without_reconstruction(self):
        data = fixtures()
        data[3][0]["source_task_bindings"] = []
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "inputs"
            prepare(directory, data)
            with self.assertRaisesRegex(ValueError, "complete source/task binding"):
                R.load_inputs(directory)

    def test_reranker_skips_long_pairs_before_truncating_backend_and_deduplicates(self):
        _, _, _, _, _, rows = fixtures()
        rows = [*rows, {**rows[0], "id": "long", "block_id": "long", "passage": "TOO_LONG"}]
        model = FakeReranker()
        with tempfile.TemporaryDirectory() as td:
            selections, runtime = R.rank_retrieval(rows, model, Path(td), FakeProgress())
            records = R.read(Path(td) / "retrieval-scores.jsonl")
        self.assertEqual(len(model.scored), 1)
        self.assertEqual(records[-1]["status"], "input_too_long")
        self.assertEqual(runtime["unique_pair_count"], 2)
        self.assertEqual(selections[rows[0]["request_id"]]["skipped_block_ids"], ["long"])

    def test_reranker_ties_have_stable_block_id_order_and_explicit_tie_flag(self):
        _, _, _, _, _, rows = fixtures()
        rows = [{**rows[0], "block_id": "b"}, {**rows[0], "id": "another", "block_id": "a"}]
        with tempfile.TemporaryDirectory() as td:
            selections, _ = R.rank_retrieval(rows, FakeReranker(), Path(td), FakeProgress())
        selected = selections[rows[0]["request_id"]]
        self.assertEqual(selected["block_id"], "a")
        self.assertTrue(selected["top_score_tie"])

    def test_missing_or_wrong_literal_body_reference_cannot_gain_support(self):
        requests, documents, _, _, blocks, _ = fixtures()
        by_doc = {d["document_id"]: d for d in documents}
        for refs in ([], [{"document_id": documents[0]["document_id"], "field": "description", "start": 0, "end": 2, "quote": "偽文"}]):
            with self.subTest(refs=refs):
                block = {**blocks[0], "quote_refs": refs}
                self.assertIsNone(R.block_refs(requests[0], by_doc, block))

    def test_raw_support_uses_three_class_unique_argmax_and_never_relative_mass(self):
        base = {"token_count_untruncated": 30, "error_type": None}
        self.assertTrue(R.unique_raw_support({**base, "probabilities": {"support": .4, "conflict": .3, "unknown": .3}}))
        self.assertFalse(R.unique_raw_support({**base, "probabilities": {"support": .08, "conflict": .76, "unknown": .16}}))
        self.assertFalse(R.unique_raw_support({**base, "probabilities": {"support": .4, "conflict": .4, "unknown": .2}}))
        self.assertFalse(R.unique_raw_support({**base, "token_count_untruncated": 513, "probabilities": {"support": 1, "conflict": 0, "unknown": 0}}))

    def test_end_to_end_freezes_without_labels_preserves_reverse_and_exact_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            directory, output = Path(td) / "inputs", Path(td) / "output"
            prepare(directory)
            with patch.object(R.JEV, "_infer", side_effect=fake_jev_infer) as ji, patch.object(R.NLI, "_infer", side_effect=fake_nli_infer) as ni:
                summary = R.run(directory, output, reranker_factory=FakeReranker,
                                jev_runner=R.JEV.run_choices, nli_runner=R.NLI.run_pairs)
            self.assertEqual(ji.call_count, 1)
            self.assertEqual(ni.call_count, 1)
            self.assertEqual(summary["unique_jev_request_count"], 1)
            self.assertEqual(summary["unique_nli_pair_count"], 2)
            self.assertFalse(summary["labels_read"])
            self.assertFalse(summary["production_adoption_allowed"])
            self.assertTrue(summary["baseline_exact_file_copy"])
            self.assertEqual((directory / "baseline-predictions.jsonl").read_bytes(), (output / "baseline-predictions.jsonl").read_bytes())
            old = R.read(directory / "baseline-predictions.jsonl")
            for variant in ("structured-jev", "structured-verified"):
                rows = R.read(output / (variant + "-predictions.jsonl"))
                self.assertEqual([r["state"] for r in rows], ["support", "unknown", "support"])
                self.assertEqual(rows[-1], old[-1])
                self.assertEqual(rows[0]["source_task_bindings"], old[0]["source_task_bindings"])
                self.assertTrue(any(ref["field"] == "description" for ref in rows[0]["quote_refs"]))
                self.assertEqual(rows[1]["quote_refs"], [])
                self.assertIsNone(rows[1]["winning_value"])
            choices = R.read(output / "model-requests.jsonl")
            self.assertEqual([c["label"] for c in choices[0]["choices"]], old[0]["source_request"]["option_values"] + [R.UNKNOWN])
            self.assertEqual(choices[0]["state"], choices[1]["state"])

    def test_verified_variant_can_only_veto_and_reverse_is_exact_copy(self):
        requests, _, _, old, _, _ = fixtures()
        record = {"status": "ok", "logits": {requests[0]["selected_value"]: 2, R.UNKNOWN: 1}, "argmax_label": requests[0]["selected_value"]}
        nli = [{"option_value": requests[0]["selected_value"], "token_count_untruncated": 30,
                "probabilities": {"support": .08, "conflict": .76, "unknown": .16}}]
        jev = R.prediction(requests[0], old[0], record, [{"literal": True}], None, None, nli)
        verified = R.prediction(requests[0], old[0], record, [{"literal": True}], None, None, nli, True)
        self.assertEqual(jev["state"], "support")
        self.assertEqual(verified["state"], "unknown")
        self.assertEqual(R.prediction(requests[-1], old[-1], None, None, None, None, []), old[-1])

    def test_existing_outputs_and_nonfrozen_cpu_settings_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            with self.assertRaises(FileExistsError):
                R.run(directory, directory)
            with self.assertRaisesRegex(ValueError, "frozen CPU"):
                R.run(directory, directory / "new", threads=2)


if __name__ == "__main__":
    unittest.main()
