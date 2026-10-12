"""Synthetic contracts for composition evidence aggregation and policies."""
from copy import deepcopy
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKU_DIR = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(SKU_DIR))
import trial_composition_evidence_v1 as RUNNER


OPTIONS = ["本体1個＋ケース", "本体2個＋替え芯"]
UNKNOWN = "不明（診断用）"


def make_doc():
    text = "同梱内容：本体1個とケース。"
    return {"doc-au": {"document_id": "doc-au", "title": {"text": "無関係な商品名"},
                        "description": {"text": text}}}


def unit(kind="shared"):
    return {"group_id": "unit-1", "group_sha256": "a" * 64, "kind": kind,
            "option_values": deepcopy(OPTIONS), "target_document_id": "doc-au",
            "target_selected_axes_text": "色：青\n数量：1",
            "source_request": {"target_document_id": "doc-au",
                               "target_selected_axes_text": "色：青\n数量：1"}}


def quote(text="同梱内容：本体1個とケース。"):
    return {"document_id": "doc-au", "field": "description", "start": 0,
            "end": len(text), "quote": text}


def block(block_id):
    return {"block_id": block_id, "target_document_id": "doc-au", "quote_refs": [quote()]}


def record(block_id, rank, label, logits=None, status="ok", group_sha="a" * 64):
    return {"group_id": "unit-1", "group_sha256": group_sha, "block_id": block_id,
            "block_rank": rank, "status": status,
            "logits": logits if logits is not None else {OPTIONS[0]: 0.9, OPTIONS[1]: 0.2, UNKNOWN: 0.1},
            "argmax_label": label}


def prediction(rid, state, selected="x", reason="baseline"):
    return {"request_id": rid, "request_sha256": "sha-" + rid, "selected_value": selected,
            "direction": "rakuten_to_au", "source_request": {"request_id": rid},
            "source_task_bindings": [{"task_id": "task-" + rid}], "state": state,
            "winning_value": selected if state == "support" else None,
            "quote_refs": [], "reason": reason, "scope_status": "unproved",
            "production_adoption_allowed": False}


class CompositionTrialContracts(unittest.TestCase):
    def test_two_whole_value_votes_from_distinct_blocks_support(self):
        units = {"unit-1": unit()}
        records = [record("block-1", 1, OPTIONS[0]), record("block-2", 2, OPTIONS[0])]
        result = RUNNER.aggregate(units, records,
                                  {"block-1": block("block-1"), "block-2": block("block-2")},
                                  make_doc())["unit-1"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["winning_value"], OPTIONS[0])
        self.assertEqual(result["block_votes"], {OPTIONS[0]: 2})
        self.assertEqual(result["supporting_block_ids"], ["block-1", "block-2"])
        self.assertEqual(len(result["quote_refs"]), 1)  # duplicate exact refs are deduplicated

    def test_single_vote_and_unknown_or_error_abstain(self):
        for second_status in ("unknown", "error"):
            with self.subTest(second_status=second_status):
                result = RUNNER.aggregate({"unit-1": unit()},
                    [record("block-1", 1, OPTIONS[0]),
                     record("block-2", 2, UNKNOWN, status=second_status)],
                    {"block-1": block("block-1"), "block-2": block("block-2")}, make_doc())["unit-1"]
                self.assertEqual(result["status"], "unknown")
                self.assertIsNone(result["winning_value"])
                self.assertEqual(result["quote_refs"], [])

    def test_repeated_block_cannot_supply_two_votes(self):
        with self.assertRaisesRegex(ValueError, "duplicate or excessive"):
            RUNNER.aggregate({"unit-1": unit()},
                [record("block-1", 1, OPTIONS[0]), record("block-1", 2, OPTIONS[0])],
                {"block-1": block("block-1")}, make_doc())

    def test_raw_logit_tie_abstains_even_with_another_single_vote(self):
        tied = {OPTIONS[0]: 0.8, OPTIONS[1]: 0.8, UNKNOWN: 0.1}
        result = RUNNER.aggregate({"unit-1": unit()},
            [record("block-1", 1, OPTIONS[0], logits=tied), record("block-2", 2, OPTIONS[0])],
            {"block-1": block("block-1"), "block-2": block("block-2")}, make_doc())["unit-1"]
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["block_winners"], {"block-1": None, "block-2": OPTIONS[0]})

    def test_paired_support_carries_literal_selected_axes_context_and_binding(self):
        req = {"request_id": "paired-1", "request_sha256": "request-hash",
               "selected_value": OPTIONS[0], "direction": "au_to_rakuten",
               "target_document_id": "doc-au", "target_selected_axes_text": "色：青\n数量：1"}
        base = {"request_id": req["request_id"], "request_sha256": req["request_sha256"],
                "selected_value": req["selected_value"], "direction": req["direction"],
                "source_request": deepcopy(req), "source_task_bindings": [{"task_id": "paired-task"}]}
        model_result = RUNNER.aggregate({"unit-1": unit("paired")},
            [record("block-1", 1, OPTIONS[0]), record("block-2", 2, OPTIONS[0])],
            {"block-1": block("block-1"), "block-2": block("block-2")}, make_doc())["unit-1"]
        result = {**model_result, "reason": "two-blocks"}
        out = RUNNER.paired_prediction(req, base, result)
        selected_refs = [ref for ref in out["quote_refs"] if ref["field"] == "selected_axes"]
        self.assertEqual(out["state"], "support")
        self.assertEqual(len(selected_refs), 1)
        self.assertEqual(selected_refs[0]["quote"], req["target_selected_axes_text"])
        self.assertEqual(selected_refs[0]["quote"], req["target_selected_axes_text"][selected_refs[0]["start"]:selected_refs[0]["end"]])
        self.assertEqual(out["source_request"], req)
        self.assertEqual(out["request_sha256"], "request-hash")
        self.assertEqual(out["source_task_bindings"], base["source_task_bindings"])

    def test_shared_only_fallback_leaves_paired_prediction_unchanged(self):
        base = {"shared": prediction("shared", "unknown"),
                "paired": prediction("paired", "unknown"),
                "proof": prediction("proof", "support")}
        derived = {key: prediction(key, "support") for key in base}
        result = RUNNER.policy_predictions("fallback-shared", base, derived,
                                           eligible={"shared"}, proof_ids={"proof"})
        self.assertEqual(result["shared"], derived["shared"])
        self.assertEqual(result["paired"], base["paired"])
        self.assertEqual(result["proof"], base["proof"])

    def test_all_fallback_only_fills_unknown_and_preserves_support_and_proof(self):
        base = {"unknown": prediction("unknown", "unknown"),
                "supported": prediction("supported", "support"),
                "proof": prediction("proof", "support")}
        derived = {"unknown": prediction("unknown", "support"),
                   "supported": prediction("supported", "unknown"),
                   "proof": prediction("proof", "unknown")}
        result = RUNNER.policy_predictions("fallback-all", base, derived,
                                           eligible=set(), proof_ids={"proof"})
        self.assertEqual(result["unknown"], derived["unknown"])
        self.assertEqual(result["supported"], base["supported"])
        self.assertEqual(result["proof"], base["proof"])

    def test_alternative_winner_abstains_and_is_not_conflict(self):
        req = {"request_id": "paired-alt", "request_sha256": "h", "selected_value": OPTIONS[0],
               "direction": "au_to_rakuten", "source_request": {"request_id": "paired-alt"},
               "source_task_bindings": [{"task_id": "t"}]}
        base = {key: deepcopy(req[key]) for key in
                ("request_id", "request_sha256", "selected_value", "direction", "source_request", "source_task_bindings")}
        derived = RUNNER.paired_prediction(req, base,
            {"winning_value": OPTIONS[1], "quote_refs": [quote()], "reason": "two-blocks"})
        self.assertEqual(derived["state"], "unknown")
        self.assertEqual(derived["reason"], "composition_alternative_value_selected")
        self.assertEqual(derived["quote_refs"], [])

    def test_full_replacement_preserves_original_request_and_task_bindings(self):
        original = prediction("paired", "unknown", selected=OPTIONS[0])
        replacement = deepcopy(original)
        replacement.update(state="support", winning_value=OPTIONS[0], quote_refs=[quote()])
        proof = prediction("typed-proof", "support", selected=OPTIONS[1])
        changed_proof = deepcopy(proof)
        changed_proof.update(state="unknown", winning_value=None, quote_refs=[])
        result = RUNNER.policy_predictions("replace-model-conditions",
            {"paired": original, "typed-proof": proof},
            {"paired": replacement, "typed-proof": changed_proof},
            eligible=set(), proof_ids={"typed-proof"})
        self.assertEqual(result["paired"]["request_sha256"], original["request_sha256"])
        self.assertEqual(result["paired"]["selected_value"], original["selected_value"])
        self.assertEqual(result["paired"]["source_request"], original["source_request"])
        self.assertEqual(result["paired"]["source_task_bindings"], original["source_task_bindings"])
        self.assertEqual(result["typed-proof"], proof)


if __name__ == "__main__":
    unittest.main()
