"""Tests for generic, fail-closed complete composition policy."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location(
    "composition_context_policy_v1", HERE / "composition_context_policy_v1.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def prediction(rid, selected, state, *, winner=None, marker=None):
    return {
        "request_id": rid, "request_sha256": f"sha-{rid}",
        "selected_value": selected, "direction": "target_to_source",
        "source_request": {"request_id": rid, "raw": f"raw-{rid}"},
        "source_task_bindings": [{"task_id": f"task-{rid}", "row": "fixed"}],
        "state": state, "winning_value": winner,
        "reason": marker or state, "quote_refs": [],
        "raw_extension": {"preserve": rid},
    }


def fixture(states=None, winners=None, row_specs=None, decisions=None):
    states = states or {"a": "unknown", "b": "unknown"}
    winners = winners or {}
    requests = {rid: {"request_id": rid, "selected_value": f"selected-{rid}"}
                for rid in states}
    baseline = {rid: prediction(rid, req["selected_value"], "unknown")
                for rid, req in requests.items()}
    derived = {
        rid: prediction(rid, req["selected_value"], states[rid],
                        winner=winners.get(rid, req["selected_value"] if states[rid] == "support" else None))
        for rid, req in requests.items()
    }
    row_specs = row_specs if row_specs is not None else [("case", "row", ["a", "b"], True)]
    rows = [{"case_id": case, "au_row_key": row,
             "all_original_axes_covered": covered,
             "condition_bindings": [({"kind": "request", "request_id": rid})
                                    for rid in bindings]}
            for case, row, bindings, covered in row_specs]
    decisions = decisions or {case: {"decision": "pending"}
                              for case, *_ in row_specs}
    return requests, baseline, derived, rows, decisions


class CompositionContextPolicyTests(unittest.TestCase):
    def test_separate_attribute_support_cannot_be_mixed_into_one_row(self):
        req, base, model, rows, gates = fixture(
            states={"a": "support", "b": "unknown"},
            row_specs=[("case", "row", ["a", "b"], True)])
        result, audit = M.complete_predictions(base, model, req, rows, gates, set())
        self.assertEqual(result["a"], base["a"])
        self.assertEqual(result["b"], base["b"])
        self.assertEqual(audit["reverted_request_ids"], ["a"])
        self.assertFalse(audit["full_model_supported_rows"])

    def test_all_new_conditions_supported_on_same_row_are_accepted(self):
        req, base, model, rows, gates = fixture(states={"a": "support", "b": "support"})
        result, audit = M.complete_predictions(base, model, req, rows, gates, set())
        self.assertEqual(result, model)
        self.assertEqual(audit["reverted_request_ids"], [])
        self.assertEqual(audit["full_model_supported_rows"], [("case", "row")])

    def test_preaccepted_support_survives_model_unknown(self):
        req, base, model, rows, gates = fixture(
            states={"a": "unknown"},
            row_specs=[("case", "row", ["a"], True)],
            decisions={"case": {"decision": "accept"}})
        base["a"]["state"] = "support"
        base["a"]["winning_value"] = req["a"]["selected_value"]
        result, _ = M.complete_predictions(base, model, req, rows, gates, set())
        self.assertEqual(result["a"], base["a"])

    def test_competing_row_cannot_be_added_to_a_previously_accepted_case(self):
        req, base, model, rows, gates = fixture(
            states={"accepted": "support", "competitor": "support"},
            row_specs=[("case", "accepted-row", ["accepted"], True),
                       ("case", "competing-row", ["competitor"], True)],
            decisions={"case": {"decision": "accept"}})
        base["accepted"]["state"] = "support"
        base["accepted"]["winning_value"] = req["accepted"]["selected_value"]
        # The other row was not accepted before this evaluation.
        base["competitor"]["state"] = "unknown"
        base["competitor"]["winning_value"] = None
        result, audit = M.complete_predictions(base, model, req, rows, gates, set())
        self.assertEqual(result["accepted"], base["accepted"])
        self.assertEqual(result["competitor"], base["competitor"])
        self.assertIn("competitor", audit["reverted_request_ids"])

    def test_explicit_alternative_still_abstains_in_previously_accepted_case(self):
        req, base, model, rows, gates = fixture(
            states={"accepted": "support", "competitor": "unknown"},
            winners={"accepted": "other-whole-option"},
            row_specs=[("case", "accepted-row", ["accepted"], True),
                       ("case", "competing-row", ["competitor"], True)],
            decisions={"case": {"decision": "accept"}})
        base["accepted"]["state"] = "support"
        base["accepted"]["winning_value"] = req["accepted"]["selected_value"]
        result, audit = M.complete_predictions(base, model, req, rows, gates, set())
        self.assertEqual(result["accepted"]["state"], "unknown")
        self.assertEqual(result["accepted"]["reason"], "coherent_model_alternative_abstention")
        self.assertEqual(audit["alternative_request_ids"], ["accepted"])

    def test_explicit_whole_alternative_abstains_without_calling_it_conflict(self):
        req, base, model, rows, gates = fixture(
            states={"a": "unknown"}, winners={"a": "another-whole-option"},
            row_specs=[("case", "row", ["a"], True)],
            decisions={"case": {"decision": "accept"}})
        base["a"]["state"] = "support"
        result, audit = M.complete_predictions(base, model, req, rows, gates, set())
        self.assertEqual(result["a"]["state"], "unknown")
        self.assertEqual(result["a"]["reason"], "coherent_model_alternative_abstention")
        self.assertEqual(result["a"]["winning_value"], "another-whole-option")
        self.assertEqual(audit["alternative_request_ids"], ["a"])

    def test_verified_proof_prediction_is_exactly_unchanged(self):
        req, base, model, rows, gates = fixture(
            states={"proof": "unknown"}, winners={"proof": "other"},
            row_specs=[("case", "row", ["proof"], True)])
        base["proof"]["verified_value_equivalence"] = {"proof": "frozen"}
        before = deepcopy(base["proof"])
        result, audit = M.complete_predictions(base, model, req, rows, gates, {"proof"})
        self.assertEqual(result["proof"], before)
        self.assertEqual(audit["alternative_request_ids"], [])

    def test_shared_request_retracted_if_any_reused_row_is_incomplete(self):
        req, base, model, rows, gates = fixture(
            states={"shared": "support", "other": "support"},
            row_specs=[("case-one", "row-one", ["shared"], True),
                       ("case-two", "row-two", ["shared", "other"], True)])
        model["other"]["state"] = "unknown"
        model["other"]["winning_value"] = None
        gates["case-two"] = {"decision": "pending"}
        result, audit = M.complete_predictions(base, model, req, rows, gates, set())
        self.assertEqual(result["shared"], base["shared"])
        self.assertEqual(audit["reverted_request_ids"], ["shared"])

    def test_uncovered_axis_fails_closed(self):
        req, base, model, rows, gates = fixture(
            states={"a": "support"},
            row_specs=[("case", "row", ["a"], False)])
        result, audit = M.complete_predictions(base, model, req, rows, gates, set())
        self.assertEqual(result["a"], base["a"])
        self.assertEqual(audit["full_model_supported_rows"], [])

    def test_invalid_request_coverage_is_rejected(self):
        req, base, model, rows, gates = fixture(states={"a": "support"})
        del model["a"]
        with self.assertRaisesRegex(ValueError, "identical coverage"):
            M.complete_predictions(base, model, req, rows, gates, set())

    def test_raw_request_bindings_cannot_be_rewritten(self):
        req, base, model, rows, gates = fixture(states={"a": "support"})
        model["a"]["source_task_bindings"][0]["row"] = "different"
        with self.assertRaisesRegex(ValueError, "changed original source_task_bindings"):
            M.complete_predictions(base, model, req, rows, gates, set())


if __name__ == "__main__":
    unittest.main()
