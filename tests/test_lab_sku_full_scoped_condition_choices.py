"""CPU-free structural tests; synthetic fixtures are never evaluation data."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("full_scoped_choices", HERE / "trial_full_scoped_condition_choices_v1.py")
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def fixtures(values=None):
    values = values or ["幅100×80cm / レースあり", "幅100×80cm / レースなし"]
    doc = {"document_id": "synthetic-document", "title": {"text": "原商品🙂"},
           "description": {"text": "他商品の情報も含む全文。削除しない。", "complete": True}}
    doc["document_sha256"] = R.digest(doc)
    requests, tasks = [], []
    for index, selected in enumerate(values):
        semantic = {"direction": "rakuten_to_au", "axis_name": "販売仕様", "selected_value": selected,
                    "option_values": values, "target_document_id": doc["document_id"],
                    "target_document_sha256": doc["document_sha256"],
                    "target_selected_axes": [{"axis_name": "カラー", "value": "青🙂"}],
                    "target_selected_axes_text": "カラー：青🙂"}
        identity = R.digest(semantic)
        request = {**semantic, "request_sha256": identity, "request_id": "generic-description:" + identity}
        requests.append(request)
        tasks.append({"task_id": "synthetic-task-" + str(index), "request_id": request["request_id"],
                      "direction": request["direction"], "option_values": values,
                      "raw_condition": {"axis_label": "販売仕様", "value": selected},
                      "source_sku_key": "synthetic-raw-sku-" + str(index)})
    return requests, {doc["document_id"]: doc}, {r["request_id"]: [t] for r, t in zip(requests, tasks, strict=True)}


def prepare(directory, requests, documents, bindings):
    directory.mkdir()
    for name, rows in (("requests.jsonl", requests), ("documents.jsonl", list(documents.values())),
                       ("tasks.jsonl", [t for group in bindings.values() for t in group])):
        R.write_rows(directory / name, rows)
    R.write_json(directory / "manifest.json", {
        "labels_read": False, "request_count": len(requests), "document_count": len(documents),
        "task_count": sum(len(v) for v in bindings.values()),
        "output_sha256": {n: R.sha(directory / n) for n in ("requests.jsonl", "documents.jsonl", "tasks.jsonl")}})


def jev_record(request, values=None, winner=None, status="ok"):
    labels = request["option_values"] + [R.UNKNOWN]
    logits = values or list(range(len(labels), 0, -1))
    probabilities = dict(zip(labels, R.JEV._softmax(logits), strict=True))
    return {"status": status, "argmax_label": winner or labels[max(range(len(logits)), key=logits.__getitem__)],
            "logits": dict(zip(labels, logits, strict=True)), "probabilities": probabilities}


def fake_jev_infer(requests, model_dir, threads, batch_size, on_ready):
    pins = R.JEV._pins()
    runtime = {"inference_seconds": .1, "model_load_seconds": 0.0, "tokenization_seconds": 0.0}
    on_ready(pins, runtime)
    records = []
    for row in requests:
        labels = [c["label"] for c in row["choices"]]
        logits = list(range(len(labels), 0, -1))
        scores = dict(zip(labels, logits, strict=True))
        probabilities = dict(zip(labels, R.JEV._softmax(logits), strict=True))
        records.append({"cache_key": row["cache_key"], "status": "ok", "logits": scores,
                        "probabilities": probabilities, "argmax_label": labels[0], "argmax_index": 0,
                        "argmax_probability": probabilities[labels[0]], "token_counts": {v: 30 for v in labels},
                        "latency_seconds": .01, "note": None, "error_type": None})
    return records, pins, runtime


def fake_nli_infer(pairs, model_dir, threads, batch_size, on_ready):
    pins = {"model_id": R.NLI.MODEL_ID, "revision": R.NLI.MODEL_REVISION,
            "parameter_count": R.NLI.FP32_PARAMETER_COUNT}
    runtime = {"inference_seconds": .1, "model_load_seconds": 0.0}
    on_ready(pins, runtime)
    return [{"task_id": p["cache_key"], "probabilities": {"support": .7 if p["option_index"] == 0 else .2,
                                                               "conflict": .1, "unknown": .2 if p["option_index"] == 0 else .7},
             "token_count_untruncated": 30, "latency_seconds": .01, "note": None, "error_type": None}
            for p in pairs], pins, runtime


class FullScopedChoiceContracts(unittest.TestCase):
    def test_complete_whole_options_are_symmetric_and_selected_binding_does_not_enter_model(self):
        requests, documents, _ = fixtures()
        choices = R.choice_requests(requests, documents)
        self.assertEqual(choices[0]["question"], choices[1]["question"])
        self.assertEqual(choices[0]["state"], choices[1]["state"])
        self.assertEqual(choices[0]["choices"], choices[1]["choices"])
        self.assertEqual([c["label"] for c in choices[0]["choices"]], requests[0]["option_values"] + [R.UNKNOWN])
        self.assertNotIn(requests[0]["selected_value"], choices[0]["state"])
        self.assertEqual(choices[0]["choices"][0]["description"], "この商品の「販売仕様」は「" + requests[0]["selected_value"] + "」である。")

    def test_desired_values_share_exact_model_key_without_losing_request_identities(self):
        requests, documents, _ = fixtures()
        choices = R.choice_requests(requests, documents)
        key = lambda c: R.JEV.request_key(c["question"], c["state"], c["choices"])
        self.assertEqual(len(R.unique_inputs(choices, key)), 1)
        self.assertNotEqual(choices[0]["id"], choices[1]["id"])

    def test_loader_does_not_require_or_read_ranking_and_preserves_full_body(self):
        requests, documents, bindings = fixtures()
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "input"
            prepare(directory, requests, documents, bindings)
            actual = R.load_inputs(directory)
            self.assertEqual(actual[:3], (requests, documents, bindings))
            self.assertEqual(len(list(directory.iterdir())), 4)

    def test_source_file_corruption_is_rejected_before_inference(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "input"
            prepare(directory, *fixtures())
            with (directory / "requests.jsonl").open("a") as handle:
                handle.write("{}\n")
            with self.assertRaisesRegex(ValueError, "file SHA256"):
                R.load_inputs(directory)

    def test_rehashed_file_with_changed_semantic_value_is_rejected(self):
        requests, documents, bindings = fixtures()
        requests[0]["selected_value"] = requests[0]["option_values"][1]
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "input"
            prepare(directory, requests, documents, bindings)
            with self.assertRaisesRegex(ValueError, "request identity"):
                R.load_inputs(directory)

    def test_document_semantic_hash_is_checked(self):
        requests, documents, bindings = fixtures()
        next(iter(documents.values()))["description"]["text"] = "変更された全文"
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "input"
            prepare(directory, requests, documents, bindings)
            with self.assertRaisesRegex(ValueError, "document semantic SHA"):
                R.load_inputs(directory)

    def test_task_binding_has_to_match_the_complete_raw_condition(self):
        requests, documents, bindings = fixtures()
        bindings[requests[0]["request_id"]][0]["raw_condition"]["value"] = "別条件"
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "input"
            prepare(directory, requests, documents, bindings)
            with self.assertRaisesRegex(ValueError, "condition binding"):
                R.load_inputs(directory)

    def test_missing_task_binding_is_rejected(self):
        requests, documents, bindings = fixtures()
        bindings[requests[0]["request_id"]] = []
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "input"
            prepare(directory, requests, documents, bindings)
            with self.assertRaisesRegex(ValueError, "missing its source task"):
                R.load_inputs(directory)

    def test_control_label_collision_is_not_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "input"
            prepare(directory, *fixtures([R.UNKNOWN]))
            with self.assertRaisesRegex(ValueError, "collides"):
                R.load_inputs(directory)

    def test_contradictory_label_provenance_flags_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "input"
            prepare(directory, *fixtures())
            path = directory / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["labels_in_model_inputs"] = True
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "exclude labels"):
                R.load_inputs(directory)

    def test_unknown_control_is_kept_diagnostic_but_gate_winner_is_none(self):
        requests, documents, bindings = fixtures()
        request = requests[0]
        pred = R.jev_prediction(request, jev_record(request, [0, 1, 2]), documents, bindings)
        self.assertEqual((pred["state"], pred["winning_value"], pred["diagnostic_winning_value"]),
                         ("unknown", None, R.UNKNOWN))
        self.assertEqual(pred["quote_refs"], [])

    def test_errors_and_ties_are_ineligible_for_nli_and_never_support(self):
        requests, documents, bindings = fixtures()
        for record in (jev_record(requests[0], [2, 2, 1]), jev_record(requests[0], status="input_too_long")):
            with self.subTest(record=record):
                self.assertFalse(R.jev_eligibility(record)[0])
                self.assertEqual(R.jev_prediction(requests[0], record, documents, bindings)["state"], "unknown")

    def test_fusion_cannot_override_jev_unknown_even_with_strong_nli(self):
        requests, documents, bindings = fixtures()
        request = requests[0]
        jev = R.jev_prediction(request, jev_record(request, [0, 1, 2]), documents, bindings)
        nli = {"relative_entailment_scores": dict(zip(request["option_values"], [.99, .01], strict=True))}
        pred = R.fusion_prediction(request, jev, nli, documents, bindings)
        self.assertEqual((pred["state"], pred["fused_scores"]), ("unknown", None))

    def test_equal_fusion_can_change_alternative_winner_and_quotes_remain_literal(self):
        requests, documents, bindings = fixtures()
        request = requests[0]
        jev = R.jev_prediction(request, jev_record(request, [1.0, 1.01, 0]), documents, bindings)
        self.assertEqual(jev["state"], "unknown")
        nli = {"relative_entailment_scores": dict(zip(request["option_values"], [.8, .2], strict=True))}
        pred = R.fusion_prediction(request, jev, nli, documents, bindings)
        self.assertEqual(pred["state"], "support")
        self.assertTrue(all(R.verified_ref(ref, request, documents) for ref in pred["quote_refs"]))
        self.assertEqual(pred["source_task_bindings"], bindings[request["request_id"]])

    def test_all_original_options_are_required_in_fusion(self):
        requests, documents, bindings = fixtures()
        request = requests[0]
        jev = R.jev_prediction(request, jev_record(request), documents, bindings)
        with self.assertRaisesRegex(ValueError, "candidate sets"):
            R.fusion_prediction(request, jev, {"relative_entailment_scores": {request["selected_value"]: 1}}, documents, bindings)

    def test_single_option_fusion_preserves_previous_ratio_behavior(self):
        requests, documents, bindings = fixtures(["原文の単一仕様"])
        request = requests[0]
        record = {"option_value": request["selected_value"], "probabilities": {"support": .001, "unknown": .999, "conflict": 0},
                  "token_count_untruncated": 30, "error_type": None}
        nli = R.choose(request["option_values"], request["selected_value"], [record])
        self.assertEqual(nli["state"], "unknown")
        jev = R.jev_prediction(request, jev_record(request, [2, 1]), documents, bindings)
        self.assertEqual(R.fusion_prediction(request, jev, nli, documents, bindings)["state"], "support")

    def test_nli_missing_scores_cannot_gain_support(self):
        requests, documents, bindings = fixtures()
        request = requests[0]
        jev = R.jev_prediction(request, jev_record(request), documents, bindings)
        pred = R.fusion_prediction(request, jev, {"relative_entailment_scores": None}, documents, bindings)
        self.assertEqual((pred["state"], pred["reason"]), ("unknown", "nli_relative_scores_unavailable"))

    def test_local_factory_cache_preserves_typed_arguments_and_exact_identity(self):
        calls = []
        def original(*args, **kwargs):
            calls.append((args, kwargs))
            return object()
        cached = R.memoized_factory(original)
        dtype = object()
        first = cached("local-pin", torch_dtype=dtype, local_files_only=True)
        self.assertIs(first, cached("local-pin", torch_dtype=dtype, local_files_only=True))
        self.assertIs(calls[0][1]["torch_dtype"], dtype)
        self.assertIsNot(first, cached("other-pin", torch_dtype=dtype, local_files_only=True))
        self.assertEqual(len(calls), 2)

    def test_nli_factory_cache_restores_both_constructors_even_after_failure(self):
        class Tokenizer:
            @classmethod
            def from_pretrained(cls, *args, **kwargs):
                return object()
        class Model:
            @classmethod
            def from_pretrained(cls, *args, **kwargs):
                return object()
        original_tokenizer = Tokenizer.__dict__["from_pretrained"]
        original_model = Model.__dict__["from_pretrained"]
        module = SimpleNamespace(AutoTokenizer=Tokenizer, AutoModelForSequenceClassification=Model)
        with patch.dict(sys.modules, {"transformers": module}):
            with self.assertRaisesRegex(ValueError, "scorer failed"):
                with R.reuse_nli_factories():
                    self.assertIs(Tokenizer.from_pretrained("frozen", local_files_only=True),
                                  Tokenizer.from_pretrained("frozen", local_files_only=True))
                    self.assertIs(Model.from_pretrained("frozen", torch_dtype="float32"),
                                  Model.from_pretrained("frozen", torch_dtype="float32"))
                    raise ValueError("scorer failed")
        self.assertIs(Tokenizer.__dict__["from_pretrained"], original_tokenizer)
        self.assertIs(Model.__dict__["from_pretrained"], original_model)

    def test_pin_failure_is_not_disguised_as_a_model_unknown(self):
        def fail(*args, **kwargs):
            raise RuntimeError("bad model pin")
        with self.assertRaisesRegex(RuntimeError, "bad model pin"):
            R.score_nli_chunk(fail, [], Path("unused"), 4, 16)

    def test_tokenizer_failure_after_pin_verification_remains_unknown(self):
        requests, documents, bindings = fixtures()
        pairs = R.pairs_for(requests, documents, bindings)
        def fail(*args, **kwargs):
            kwargs["on_ready"]({"model_id": R.NLI.MODEL_ID, "revision": R.NLI.MODEL_REVISION,
                                "parameter_count": R.NLI.FP32_PARAMETER_COUNT}, {})
            raise ValueError("tokenizer rejected input")
        result = R.score_nli_chunk(fail, pairs, Path("unused"), 4, 16)
        self.assertEqual(len(result["records"]), len(pairs))
        self.assertTrue(all(r["probabilities"] is None and r["error_type"] == "ValueError" for r in result["records"]))
        self.assertEqual(R.choose(requests[0]["option_values"], requests[0]["selected_value"], result["records"][:2])["state"], "unknown")

    def test_full_run_deduplicates_and_fans_out_all_predictions_with_frozen_source_refs(self):
        requests, documents, bindings = fixtures()
        with tempfile.TemporaryDirectory() as td:
            input_dir, output_dir = Path(td) / "input", Path(td) / "output"
            prepare(input_dir, requests, documents, bindings)
            with patch.object(R.JEV, "_infer", side_effect=fake_jev_infer) as jev_infer, patch.object(R.NLI, "_infer", side_effect=fake_nli_infer) as nli_infer:
                summary = R.run(input_dir, output_dir, jev_runner=R.JEV.run_choices, nli_runner=R.NLI.run_pairs,
                                jev_chunk_requests=1, nli_chunk_pairs=1)
            self.assertEqual(jev_infer.call_count, 1)
            self.assertEqual(nli_infer.call_count, 2)
            self.assertEqual(summary["unique_jev_request_count"], 1)
            self.assertEqual(summary["unique_nli_pair_count"], 2)
            self.assertFalse(summary["labels_read"])
            self.assertFalse(summary["production_adoption_allowed"])
            for filename in ("jev-predictions.jsonl", "fusion-predictions.jsonl", "predictions.jsonl"):
                predictions = R.read(output_dir / filename)
                self.assertEqual([p["request_id"] for p in predictions], [r["request_id"] for r in requests])
                self.assertEqual([p["state"] for p in predictions], ["support", "unknown"])
                self.assertEqual(predictions[1]["source_request"], requests[1])
                self.assertEqual(summary["output_sha256"][filename], R.sha(output_dir / filename))
            self.assertEqual(R.read(output_dir / "source-documents.jsonl"), list(documents.values()))

    def test_unknown_jev_skips_nli_without_removing_any_request_or_option(self):
        def unknown_infer(*args, **kwargs):
            records, pins, runtime = fake_jev_infer(*args, **kwargs)
            for record in records:
                labels = list(record["logits"])
                values = [0.0] * (len(labels) - 1) + [2.0]
                record.update(logits=dict(zip(labels, values, strict=True)),
                              probabilities=dict(zip(labels, R.JEV._softmax(values), strict=True)),
                              argmax_label=R.UNKNOWN, argmax_index=len(labels) - 1,
                              argmax_probability=R.JEV._softmax(values)[-1])
            return records, pins, runtime
        def forbidden_nli(*args, **kwargs):
            self.fail("JEV unknown control must not run NLI")
        with tempfile.TemporaryDirectory() as td:
            input_dir, output_dir = Path(td) / "input", Path(td) / "output"
            prepare(input_dir, *fixtures())
            with patch.object(R.JEV, "_infer", side_effect=unknown_infer):
                summary = R.run(input_dir, output_dir, jev_runner=R.JEV.run_choices, nli_runner=forbidden_nli)
            self.assertEqual(summary["nli_skipped_requests"], {"jev_unknown_control": 2})
            self.assertEqual(summary["unique_nli_pair_count"], 0)
            predictions = R.read(output_dir / "predictions.jsonl")
            self.assertEqual(len(predictions), 2)
            self.assertTrue(all(p["state"] == "unknown" and p["winning_value"] is None for p in predictions))
            self.assertEqual(len(R.read(output_dir / "model-nli-pairs.jsonl")), 4)

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileExistsError):
                R.run(Path(td), Path(td))

    def test_frozen_cpu_settings_are_not_silently_changed(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, "frozen CPU"):
                R.run(Path(td), Path(td) / "unused", threads=2)


if __name__ == "__main__":
    unittest.main()
