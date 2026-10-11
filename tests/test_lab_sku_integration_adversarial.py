"""Independent, artificial adversarial contracts; never accuracy samples."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKU = ROOT / "experiments" / "sku-matching"
sys.path.insert(0, str(SKU))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FIXTURES = load("adversarial_integration_fixtures", ROOT / "tests" / "test_lab_sku_claude_description_integration.py")
GATE = FIXTURES.MOD
RUNNER = load("adversarial_integration_runner", SKU / "trial_integrated_description_models_v1.py")


class IntegrationAdversarialContracts(unittest.TestCase):
    def setUp(self):
        self.fixture = FIXTURES.IntegrationContract(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def requests(self, prepared):
        return GATE.rows(prepared / "requests.jsonl")

    def add_competing_row(self):
        fixture = self.fixture
        fixture.decisions["legacy"][0]["row_states"] = {"pending_description_check": 2}
        context = copy.deepcopy(fixture.contexts[0])
        context["au_row_key"] = "A0:1"
        fixture.contexts.append(context)
        forward = copy.deepcopy(fixture.forward[0])
        forward["au_row_key"] = "A0:1"
        fixture.forward.append(forward)
        reverse = copy.deepcopy(fixture.reverse[0])
        reverse["au_row_key"] = "A0:1"
        reverse["raw_condition"] = copy.deepcopy(fixture.product_values[0]["au"]["rows"][1]["axes"][0])
        reverse["selected_value"] = reverse["raw_condition"]["value"]
        fixture.reverse.append(reverse)
        fixture.build_sources()

    def test_sibling_document_literal_cannot_support_fixed_target(self):
        prepared, _ = self.fixture.prepared()
        predictions = [self.fixture.prediction(request) for request in self.requests(prepared)]
        predictions[0]["quote_refs"][0]["document_id"] = "fixed-au:sibling-page"
        result, decisions = self.fixture.gate(prepared, predictions)
        self.assertEqual(decisions["L0"]["decision"], "drop")
        self.assertIn("missing_or_unverified_literal_quote", result["condition_reasons"])

    def test_one_valid_quote_does_not_hide_an_invalid_extra_quote(self):
        prepared, _ = self.fixture.prepared()
        predictions = [self.fixture.prediction(request) for request in self.requests(prepared)]
        bad = copy.deepcopy(predictions[0]["quote_refs"][0])
        bad["quote"] = "外部ページの記述"
        predictions[0]["quote_refs"].append(bad)
        _, decisions = self.fixture.gate(prepared, predictions)
        self.assertEqual(decisions["L0"]["decision"], "drop")

    def test_boolean_quote_offsets_are_not_unicode_character_offsets(self):
        prepared, _ = self.fixture.prepared()
        predictions = [self.fixture.prediction(request) for request in self.requests(prepared)]
        predictions[0]["quote_refs"][0]["start"] = False
        _, decisions = self.fixture.gate(prepared, predictions)
        self.assertEqual(decisions["L0"]["decision"], "drop")

    def test_partial_composite_winner_is_rejected_not_supported(self):
        prepared, _ = self.fixture.prepared()
        request = next(r for r in self.requests(prepared) if r["direction"] == "au_to_rakuten")
        prediction = self.fixture.prediction(request)
        prediction["winning_value"] = "100×80cm"
        with self.assertRaisesRegex(ValueError, "whole original alternative"):
            self.fixture.gate(prepared, [prediction])

    def test_unknown_state_with_desired_value_and_literal_stays_unknown(self):
        prepared, _ = self.fixture.prepared()
        predictions = [self.fixture.prediction(request, state="unknown") for request in self.requests(prepared)]
        result, decisions = self.fixture.gate(prepared, predictions)
        self.assertEqual(result["condition_states"], {"unknown": 2})
        self.assertEqual(decisions["L0"]["decision"], "drop")

    def test_verified_literal_does_not_mark_applicability_as_independently_proved(self):
        prepared, _ = self.fixture.prepared()
        predictions = [self.fixture.prediction(request) for request in self.requests(prepared)]
        result, decisions = self.fixture.gate(prepared, predictions)
        self.assertEqual(decisions["L0"]["decision"], "accept")
        self.assertFalse(decisions["L0"]["production_adoption_allowed"])
        self.assertTrue(decisions["L0"]["experimental_only"])
        self.assertFalse(result["scope_applicability_independently_proved"])
        conditions = GATE.rows(self.fixture.root / "finished" / "condition_results.jsonl")
        self.assertTrue(all(not r["quote_applicability_independently_proved"] for r in conditions))

    def test_every_competing_candidate_must_be_contradicted_for_unique_adoption(self):
        self.add_competing_row()
        prepared, _ = self.fixture.prepared()
        tasks = GATE.rows(prepared / "tasks.jsonl")
        competing_reverse = next(t["request_id"] for t in tasks if t["case_id"] == "L0"
                                 and t["au_row_key"] == "A0:1" and t["direction"] == "au_to_rakuten")
        predictions = [self.fixture.prediction(request, state="conflict" if request["request_id"] == competing_reverse else "support")
                       for request in self.requests(prepared)]
        _, decisions = self.fixture.gate(prepared, predictions)
        self.assertEqual(decisions["L0"]["decision"], "accept")
        self.assertEqual(decisions["L0"]["top_row_key"], "A0:0")
        self.assertEqual(decisions["L0"]["row_states"], {"support": 1, "conflict": 1})

    def test_multiple_fully_supported_rows_do_not_select_first(self):
        self.add_competing_row()
        prepared, _ = self.fixture.prepared()
        predictions = [self.fixture.prediction(request) for request in self.requests(prepared)]
        _, decisions = self.fixture.gate(prepared, predictions)
        self.assertEqual(decisions["L0"]["decision"], "drop")
        self.assertEqual(decisions["L0"]["reason"], "multiple_supported_rows")
        self.assertIsNone(decisions["L0"]["top_row_key"])

    def test_model_dedup_does_not_feed_desired_value_or_share_final_support(self):
        prepared, _ = self.fixture.prepared()
        first = next(r for r in self.requests(prepared) if r["direction"] == "rakuten_to_au")
        other = copy.deepcopy(first)
        other["selected_value"] = other["option_values"][1]
        first_payload, other_payload = RUNNER.model_payload(first), RUNNER.model_payload(other)
        self.assertEqual(first_payload, other_payload)
        document = GATE.index(GATE.rows(prepared / "documents.jsonl"), "document_id", "fixture")[first["target_document_id"]]
        self.assertEqual(RUNNER.request_for(first_payload, document), RUNNER.request_for(other_payload, document))
        record = {"status": "ok", "chosen_value": first["selected_value"], "confidence": .9, "quote_refs": []}
        self.assertEqual(RUNNER.aggregate([record], first["selected_value"], .5)[0], "support")
        self.assertEqual(RUNNER.aggregate([record], other["selected_value"], .5)[0], "unknown")

    def test_whole_target_row_is_part_of_model_dedup_key(self):
        prepared, _ = self.fixture.prepared()
        first = next(r for r in self.requests(prepared) if r["direction"] == "rakuten_to_au")
        other = copy.deepcopy(first)
        other["target_selected_axes"][0]["value"] = "幅100×丈90cm(4枚組)"
        other["target_selected_axes_text"] = GATE.axis_text(other["target_selected_axes"])
        self.assertNotEqual(RUNNER.digest(RUNNER.model_payload(first)), RUNNER.digest(RUNNER.model_payload(other)))


if __name__ == "__main__":
    unittest.main()
