"""Small CPU-free contracts; synthetic fixtures are not evaluation records."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKU_DIR = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(SKU_DIR))
SPEC = importlib.util.spec_from_file_location("scoped_condition_nli", SKU_DIR / "trial_scoped_condition_nli_v1.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def source():
    doc = {"document_id": "synthetic-fixed-target", "title": {"text": "テスト商品🙂"},
           "description": {"text": "現在の仕様は4枚。別商品は2枚。"}}
    req = {"request_id": "synthetic-request", "request_sha256": "a" * 64, "direction": "rakuten_to_au",
           "axis_name": "販売仕様", "selected_value": "幅100×丈80cm（4枚組） / レースあり",
           "option_values": ["幅100×丈80cm（4枚組） / レースあり", "幅100×丈80cm（2枚組） / レースなし"],
           "target_document_id": doc["document_id"], "target_document_sha256": "b" * 64,
           "target_selected_axes_text": "カラー：青\nサイズ：幅100×丈80cm（4枚組）"}
    group = RUNNER.digest({k: req[k] for k in RUNNER.RANKING_FIELDS})
    window = {"window_id": "body-0", "text": doc["description"]["text"],
              "quote_ref": RUNNER.quote_ref(doc, "description", doc["description"]["text"])}
    binding = {"case_id": "synthetic-case", "au_row_key": "raw-row", "source_sku_key": "raw-sku"}
    return req, doc, {req["request_id"]: [binding]}, {group: [window]}


def nli_record(pair, state, confidence=.95, count=80):
    return {**pair, "relation": state, "confidence_threshold": .90,
            "predicted_confidence": confidence, "token_count_untruncated": count,
            "probabilities": {"support": .95, "conflict": .03, "unknown": .02}}


def prediction(state, refs=None):
    req, _, _, _ = source()
    return {"request_id": req["request_id"], "request_sha256": req["request_sha256"],
            "selected_value": req["selected_value"], "state": state, "quote_refs": refs or [],
            "winning_value": req["selected_value"] if state == "support" else None}


class ScopedConditionNliContracts(unittest.TestCase):
    def test_complete_raw_axis_and_value_form_hypothesis_without_extraction(self):
        req, doc, bindings, ranking = source()
        pairs = RUNNER.pairs_for([req], {doc["document_id"]: doc}, bindings, ranking)
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0]["hypothesis"], "この商品の〈販売仕様〉は〈" + req["selected_value"] + "〉である。")
        self.assertEqual(pairs[0]["source_request"]["option_values"], req["option_values"])
        self.assertEqual(pairs[1]["source_task_bindings"], bindings[req["request_id"]])
        self.assertEqual(pairs[1]["direction"], req["direction"])

    def test_premise_contains_fixed_title_row_and_literal_body_without_hypothesis(self):
        req, doc, bindings, ranking = source()
        pair = RUNNER.pairs_for([req], {doc["document_id"]: doc}, bindings, ranking)[1]
        for value in (doc["title"]["text"], req["target_selected_axes_text"], doc["description"]["text"]):
            self.assertIn(value, pair["premise"])
        self.assertNotIn(pair["hypothesis"], pair["premise"])
        self.assertNotIn("verified_quote", pair)
        RUNNER.NLI._validate_pair(pair)
        self.assertEqual(pair["quote_refs"][-1]["quote"], doc["description"]["text"])

    def test_no_received_body_still_has_title_and_row_pair(self):
        req, doc, bindings, _ = source()
        pairs = RUNNER.pairs_for([req], {doc["document_id"]: doc}, bindings, {})
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["window_id"], "title")

    def test_pinned_point_nine_conflict_vetoes_support(self):
        req, doc, bindings, ranking = source()
        pairs = RUNNER.pairs_for([req], {doc["document_id"]: doc}, bindings, ranking)
        records = [nli_record(pairs[0], "support", .99), nli_record(pairs[1], "conflict", .90)]
        result = RUNNER.predictions_for([req], records, bindings)[0]
        self.assertEqual(result["state"], "conflict")
        self.assertIsNone(result["winning_value"])
        self.assertFalse(result["production_adoption_allowed"])

    def test_low_confidence_unknown_and_overlong_never_contradict(self):
        req, doc, bindings, ranking = source()
        pair = RUNNER.pairs_for([req], {doc["document_id"]: doc}, bindings, ranking)[0]
        for rec in (nli_record(pair, "conflict", .89), nli_record(pair, "unknown", .99), nli_record(pair, "conflict", .99, 513)):
            with self.subTest(rec=rec):
                self.assertEqual(RUNNER.predictions_for([req], [rec], bindings)[0]["state"], "unknown")
                self.assertEqual(rec["premise"], pair["premise"])

    def test_ensemble_only_nli_conflict_can_veto_existing_support(self):
        result = RUNNER.ensemble_prediction(prediction("support"), prediction("conflict", [{"quote": "矛盾原文"}]))
        self.assertEqual(result["state"], "conflict")
        self.assertTrue(result["nli_conflict_veto"])
        self.assertEqual(result["quote_refs"], [{"quote": "矛盾原文"}])
        for state in ("support", "unknown"):
            result = RUNNER.ensemble_prediction(prediction("support"), prediction(state))
            self.assertEqual(result["state"], "support")
            self.assertFalse(result["nli_conflict_veto"])

    def test_nli_support_never_upgrades_gliner_unknown_or_conflict(self):
        for state in ("unknown", "conflict"):
            self.assertEqual(RUNNER.ensemble_prediction(prediction(state), prediction("support"))["state"], state)

    def test_ensemble_identity_and_fixed_target_quotes_must_bind(self):
        gliner = prediction("support")
        bad = {**prediction("conflict"), "request_sha256": "c" * 64}
        with self.assertRaisesRegex(ValueError, "identity differs"):
            RUNNER.ensemble_prediction(gliner, bad)
        req, doc, _, _ = source()
        ref = RUNNER.quote_ref(doc, "title", doc["title"]["text"])
        self.assertTrue(RUNNER.verified_ref(ref, req, {doc["document_id"]: doc}))
        self.assertFalse(RUNNER.verified_ref({**ref, "document_id": "sibling"}, req, {doc["document_id"]: doc}))

    def test_existing_output_is_rejected_before_any_model_load(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileExistsError):
                RUNNER.run(Path(td), Path(td), Path(td), Path("never-loaded"))


if __name__ == "__main__":
    unittest.main()
