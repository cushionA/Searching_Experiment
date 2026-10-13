"""Synthetic proof-coverage checks for route DEV; no cohort labels or responses."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("indexed_route_dev_runner", HERE / "run_next_body_indexed_route_dev_v1.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def task(tid: str, value: str = "M") -> dict:
    return {"task_id": tid, "case_id": "case-synthetic", "request_id": "request-" + tid,
            "au_row_key": "row-synthetic", "direction": "forward",
            "raw_condition_ref": {"axis_name": "route", "value": value}}


def receipt(tid: str, family: list[str] | None = None) -> dict:
    fam = family or ["S", "M"]
    selected = "S" if tid in {"b", "c"} else "M"
    return {"task_id": tid, "status": "verified_complete_ordered_raw_family",
            "selected_value": selected, "ordered_family": fam}


def proof_state(tid: str, state: str, pair_id: str | None = None) -> dict:
    data = {"task_id": tid, "status": state}
    if pair_id:
        data["proof_pair_id"] = pair_id
    return data


def pairproof(a: str, b: str, state: str = "support", family: list[str] | None = None) -> dict:
    return {"forward_task_id": a, "reverse_task_id": b, "status": state,
            "ordered_family": family or ["S", "M"]}


def adapt(tasks, states, receipts, proofs):
    return RUNNER.adapt_literal_proofs(tasks, states, receipts, proofs)


class RouteLiteralCoverageTests(unittest.TestCase):
    def test_unpaired_unknown_with_exact_source_receipt_is_retained(self):
        result = adapt([task("u1")], [proof_state("u1", "unknown")], [receipt("u1")], [])
        self.assertEqual(result, [proof_state("u1", "unknown")])

    def test_decisive_task_without_pair_proof_fails(self):
        with self.assertRaises(ValueError):
            adapt([task("s1")], [proof_state("s1", "support", "s1|s2")], [receipt("s1")], [])

    def test_foreign_or_duplicate_pairproof_members_fail(self):
        tasks = [task("a"), task("b")]
        states = [proof_state("a", "support", "a|b"), proof_state("b", "support", "a|b")]
        receipts = [receipt("a"), receipt("b")]
        with self.assertRaises(ValueError):
            adapt(tasks, states, receipts, [pairproof("a", "foreign")])
        with self.assertRaises(ValueError):
            adapt(tasks + [task("c")], states + [proof_state("c", "unknown")],
                  receipts + [receipt("c")], [pairproof("a", "b"), pairproof("b", "c")])

    def test_incomplete_state_or_receipt_coverage_fails(self):
        tasks = [task("a"), task("b")]
        with self.assertRaises(ValueError):
            adapt(tasks, [proof_state("a", "unknown")], [receipt("a"), receipt("b")], [])
        with self.assertRaises(ValueError):
            adapt(tasks, [proof_state("a", "unknown"), proof_state("b", "unknown")], [receipt("a")], [])

    def test_verified_support_and_conflict_pairs_are_preserved(self):
        tasks = [task("a", "M"), task("b", "S"), task("c", "S"), task("d", "M")]
        receipts = [receipt("a"), receipt("b"), receipt("c"), receipt("d")]
        proofs = [pairproof("a", "b", "support"), pairproof("c", "d", "conflict")]
        states = [proof_state("a", "support", "a|b"), proof_state("b", "support", "a|b"),
                  proof_state("c", "conflict", "c|d"), proof_state("d", "conflict", "c|d")]
        result = adapt(tasks, states, receipts, proofs)
        self.assertEqual([x["status"] for x in result], ["support", "support", "conflict", "conflict"])
        self.assertTrue(all(x.get("literal_proof_verified") is True for x in result))

    def test_unknown_task_with_a_proof_is_rejected_not_promoted(self):
        with self.assertRaises(ValueError):
            adapt([task("a"), task("b")],
                  [proof_state("a", "unknown"), proof_state("b", "support", "a|b")],
                  [receipt("a"), receipt("b")], [pairproof("a", "b")])

    def test_decisive_pair_requires_receipt_and_pairproof_family_match(self):
        tasks = [task("a"), task("b")]
        states = [proof_state("a", "support", "a|b"), proof_state("b", "support", "a|b")]
        proofs = [pairproof("a", "b", family=["S", "M"])]
        with self.assertRaises(ValueError):
            adapt(tasks, states, [receipt("a", ["S", "L"]), receipt("b")], proofs)
        bad_proof = [pairproof("a", "b", family=["S", "L"])]
        with self.assertRaises(ValueError):
            adapt(tasks, states, [receipt("a"), receipt("b")], bad_proof)


if __name__ == "__main__":
    unittest.main()
