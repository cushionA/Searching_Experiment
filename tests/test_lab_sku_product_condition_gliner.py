"""Synthetic contract fixtures only; no performance references or downloads."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("product_condition_gliner", HERE / "trial_product_condition_gliner_v1.py")
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)
import test_lab_sku_product_condition_models as P
import test_lab_sku_scoped_html_evidence as F


def source_fixture(root):
    data, contract = P.fixtures()
    prepared, eligibility, source = root / "prepared", root / "eligibility.json", root / "source"
    F.prepare(prepared, data)
    contract["source_sha256"] = {"prepared": {name: R.sha(prepared / name)
        for name in ("manifest.json", "requests.jsonl", "documents.jsonl", "tasks.jsonl")}}
    R.write_json(eligibility, contract)
    with patch.object(R.PRODUCT.JEV, "_infer", side_effect=F.fake_jev_infer), patch.object(R.PRODUCT.NLI, "_infer", side_effect=F.fake_nli_infer):
        R.PRODUCT.run(prepared, eligibility, source, reranker_factory=F.FakeReranker,
                      jev_runner=R.PRODUCT.JEV.run_choices, nli_runner=R.PRODUCT.NLI.run_pairs)
    return source


class FakeBackend:
    def __init__(self, *args):
        self.runtime = {"model_load_seconds": .1, "inference_seconds": 0, "parameter_count": R.PARAMETER_COUNT}
        self.dependencies = {}
        self.scored = []

    def token_lengths(self, requests):
        return [513 if "TOO_LONG" in request["text"] else 50 for request in requests]

    def classify(self, requests, batch_size):
        self.scored.extend(requests)
        self.runtime["inference_seconds"] += .01
        results = []
        for request in requests:
            labels = request["schema_labels"]
            probabilities = {label: .8 if i == 0 else .2 / (len(labels) - 1) for i, label in enumerate(labels)}
            results.append({request["schema_task_name"]: SimpleNamespace(label=labels[0], confidence=.8, probabilities=probabilities)})
        return results


class FakeProgress:
    def emit(self, **kwargs):
        pass


def grouped_fixtures():
    data, contract = P.fixtures()
    plan = P.plan_for(data, contract)
    group = plan["groups"][0]
    blocks = {b["block_id"]: b for b in data[4]}
    rankings = {group["group_id"]: {"ordered_block_ids": list(blocks)}}
    choices = R.PRODUCT.classification_requests(plan["groups"], blocks, rankings)
    requests = R.model_requests(choices, plan["groups"])
    return data, plan, blocks, choices, requests


def gliner_record(request, value=None, *, confidence=.8, tie=False, unknown=False, error=False):
    value = value or request["option_values"][0]
    labels = request["candidate_labels"]
    winner = request["unknown_label"] if unknown else value
    probabilities = {label: confidence if label == winner else (1 - confidence) / (len(labels) - 1) for label in labels}
    if tie:
        probabilities = {labels[0]: .5, labels[1]: .5, labels[2]: 0}
    return {**request, "status": "inference_error" if error else "ok", "chosen_value": None if unknown else value,
            "confidence": confidence, "probabilities": probabilities}


class ProductGLiNERContracts(unittest.TestCase):
    def test_same_original_product_text_and_whole_options_are_preserved(self):
        _, plan, _, choices, requests = grouped_fixtures()
        for choice, request in zip(choices, requests, strict=True):
            self.assertEqual(request["text"], choice["state"])
            self.assertNotIn("現在の選択行", request["text"])
            self.assertEqual(request["option_values"], plan["groups"][0]["option_values"])
            self.assertEqual(list(request["schema_label_to_original_value"].values()), [*request["option_values"], None])
            self.assertEqual(request["candidate_labels"], [*request["option_values"], request["unknown_label"]])

    def test_compiled_long_inputs_skip_before_classification_and_identical_inputs_deduplicate(self):
        _, _, _, _, requests = grouped_fixtures()
        requests = [requests[0], {**requests[0], "id": "same-input"}, {**requests[1], "text": "TOO_LONG"}]
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as td:
            records, count = R.classify_requests(requests, backend, Path(td), FakeProgress(), 8)
        self.assertEqual(count, 2)
        self.assertEqual(len(backend.scored), 1)
        self.assertEqual(records[-1]["status"], "input_too_long")
        self.assertIsNone(records[-1]["chosen_value"])
        self.assertEqual(records[-1]["actual_encoder_input_token_count"], 513)

    def test_fixed_confidence_unknown_tie_and_errors_cast_no_vote(self):
        _, _, _, _, requests = grouped_fixtures()
        request = requests[0]
        for record in (gliner_record(request, confidence=.4), gliner_record(request, confidence=.5, tie=True),
                       gliner_record(request, unknown=True), gliner_record(request, error=True)):
            with self.subTest(record=record):
                self.assertIsNone(R.winner(record, request["option_values"]))
        self.assertEqual(R.winner(gliner_record(request, confidence=.6), request["option_values"]), request["option_values"][0])

    def test_classifier_schema_mismatch_is_error_without_forced_label(self):
        class BadBackend(FakeBackend):
            def classify(self, requests, batch_size):
                return [{requests[0]["schema_task_name"]: SimpleNamespace(label="unknown", confidence=1, probabilities={"unknown": 1})}]
        _, _, _, _, requests = grouped_fixtures()
        with tempfile.TemporaryDirectory() as td:
            records, _ = R.classify_requests(requests[:1], BadBackend(), Path(td), FakeProgress(), 8)
        self.assertEqual(records[0]["status"], "inference_error")
        self.assertIsNone(records[0]["chosen_value"])

    def test_model_majority_agreement_and_same_block_agreement_are_distinct(self):
        _, plan, blocks, _, requests = grouped_fixtures()
        group = plan["groups"][0]
        first, second = group["option_values"]
        gliner = [gliner_record(request, second if i == 0 else first) for i, request in enumerate(requests)]
        jev = [P.candidate_record(group, request["block_id"], i + 1, first if i < 2 else second) for i, request in enumerate(requests)]
        majority = R.group_results(plan["groups"], gliner, jev, plan["documents"], blocks, R.VARIANTS[2])[group["group_id"]]
        per_block = R.group_results(plan["groups"], gliner, jev, plan["documents"], blocks, R.VARIANTS[3])[group["group_id"]]
        self.assertEqual(majority["winning_value"], first)
        self.assertIsNone(per_block["winning_value"])
        gliner[0] = gliner_record(requests[0], first)
        per_block = R.group_results(plan["groups"], gliner, jev, plan["documents"], blocks, R.VARIANTS[3])[group["group_id"]]
        self.assertEqual(per_block["winning_value"], first)

    def test_raw_negative_fanout_and_passthrough_keep_original_ids_and_binding_objects(self):
        data, plan, blocks, _, requests = grouped_fixtures()
        group = plan["groups"][0]
        negative = group["option_values"][1]
        gliner = [gliner_record(request, negative) for request in requests]
        jev = [P.candidate_record(group, request["block_id"], i + 1, negative) for i, request in enumerate(requests)]
        for variant in R.VARIANTS:
            with self.subTest(variant=variant):
                results = R.group_results(plan["groups"], gliner, jev, plan["documents"], blocks, variant)
                rows = R.SHARING.fan_out(plan, results, {p["request_id"]: p for p in data[3]})
                self.assertEqual([row["state"] for row in rows], ["unknown", "support", "support"])
                self.assertEqual(rows[-1], data[3][-1])
                self.assertEqual(rows[1]["source_task_bindings"], data[3][1]["source_task_bindings"])
                self.assertTrue(all(ref["field"] in {"title", "description"} for ref in rows[1]["quote_refs"]))

    def test_new_model_component_invalidates_group_and_decision_cache_identity(self):
        data, source_plan, blocks, choices, _ = grouped_fixtures()
        group = source_plan["groups"][0]
        jev = [P.candidate_record(group, choice["block_id"], choice["block_rank"]) for choice in choices]
        for row, choice in zip(jev, choices, strict=True):
            row.update(id=choice["id"])
        plan, mapped_choices, mapped_jev = R.rebuild_plan(source_plan, choices, jev, data[0], {"model_id": R.GL.MODEL_ID, "revision": R.GL.REVISION})
        self.assertNotEqual(plan["groups"][0]["group_id"], group["group_id"])
        self.assertNotEqual(plan["groups"][0]["model_cache_key"], group["model_cache_key"])
        self.assertEqual(mapped_choices[0]["state"], choices[0]["state"])
        self.assertEqual(mapped_choices[0]["choices"], choices[0]["choices"])
        self.assertEqual(mapped_jev[0]["source_group_id"], group["group_id"])
        self.assertEqual(plan["original_requests"], source_plan["original_requests"])

    def test_completed_source_load_verifies_all_input_and_pin_contracts(self):
        with tempfile.TemporaryDirectory() as td:
            source = source_fixture(Path(td))
            loaded = R.load_source(source)
            self.assertEqual(len(loaded[1]), 3)
            with (source / "model-requests.jsonl").open("a") as handle:
                handle.write("{}\n")
            with self.assertRaisesRegex(ValueError, "source SHA256"):
                R.load_source(source)

    def test_end_to_end_all_four_variants_freeze_before_labels_and_preserve_original_order(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = source_fixture(root)
            output = root / "gliner"
            summary = R.run(source, output, backend_factory=FakeBackend,
                            pin_verifier=lambda _: {"model_id": R.GL.MODEL_ID, "revision": R.GL.REVISION})
            self.assertEqual(summary["group_count"], 1)
            self.assertEqual(summary["block_count"], 3)
            self.assertEqual(summary["unique_model_input_count"], 3)
            self.assertFalse(summary["labels_read"])
            self.assertFalse(summary["new_jev_inference"])
            original = R.read(source / "source-requests.jsonl")
            baseline = {p["request_id"]: p for p in R.read(source / "baseline-predictions.jsonl")}
            for variant in R.VARIANTS:
                rows = R.read(output / (variant + "-predictions.jsonl"))
                self.assertEqual([row["request_id"] for row in rows], [request["request_id"] for request in original])
                self.assertEqual([row["state"] for row in rows], ["support", "unknown", "support"])
                self.assertEqual(rows[-1], baseline[rows[-1]["request_id"]])
                self.assertEqual(summary["output_sha256"][variant + "-predictions.jsonl"], R.sha(output / (variant + "-predictions.jsonl")))
            self.assertNotEqual(summary["model_identity"], summary["source_model_identity"])

    def test_existing_output_and_nonfrozen_settings_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(FileExistsError):
                R.run(root, root)
            with self.assertRaisesRegex(ValueError, "frozen CPU"):
                R.run(root, root / "out", threads=2)


if __name__ == "__main__":
    unittest.main()
