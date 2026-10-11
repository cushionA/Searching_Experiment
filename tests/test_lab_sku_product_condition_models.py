"""Synthetic structure checks only; independent labels are never read here."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("product_condition_models", HERE / "trial_product_condition_models_v1.py")
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)
import test_lab_sku_scoped_html_evidence as F


def fixtures():
    data = F.fixtures()
    requests, documents, tasks, baseline, blocks, retrieval = data
    document = documents[0]
    document.update(side="au", fixed_page=True, product_id="123456", url=None)
    document["document_sha256"] = R.digest({k: v for k, v in document.items() if k != "document_sha256"})
    for index, (request, task, old) in enumerate(zip(requests, tasks, baseline, strict=True)):
        request["target_document_sha256"] = document["document_sha256"]
        request["target_selected_axes"] = [{"axis_name": "色", "value": "青" if index == 0 else "赤"}]
        request["target_selected_axes_text"] = "色：" + request["target_selected_axes"][0]["value"]
        identity = R.digest({key: request[key] for key in R.SHARING.SEMANTIC_FIELDS})
        request.update(request_sha256=identity, request_id="generic-description:" + identity)
        task["request_id"] = request["request_id"]
        task["origin"] = "one_sided" if index < 2 else "model_candidate"
        old.update(request_id=request["request_id"], request_sha256=identity)
    blocks[:] = [{**deepcopy(blocks[0]), "block_id": "synthetic-block-" + str(i),
                 "model_passage": blocks[0]["model_passage"] + "\n見出し" + str(i)} for i in range(3)]
    retrieval[:] = [{"id": "synthetic-retrieval-" + str(i) + "-" + str(j), "request_id": request["request_id"],
                    "block_id": block["block_id"], "target_document_id": document["document_id"],
                    "query": R.V1.query_for(request, document), "passage": block["model_passage"],
                    "quote_refs": block["quote_refs"]} for i, request in enumerate(requests[:2]) for j, block in enumerate(blocks)]
    contract = {"basis": "user_asserted", "statement": "この商品の余剰条件は全サイズと色で共通です。",
                "eligible_forward_request_ids": [request["request_id"] for request in requests[:2]],
                "sold_out_rows_excluded_upstream": True}
    return data, contract


def plan_for(data, contract):
    requests, documents, _, baseline, _, _ = data
    bindings = {row["request_id"]: row["source_task_bindings"] for row in baseline}
    return R.SHARING.build_groups(requests, documents, bindings, model_identity=R.model_identity(),
                                   content_version=R.CONTENT_VERSION, invariance_contract=contract)


def candidate_record(group, block_id, rank, winner=None, *, tie=False, error=False):
    labels = group["option_values"] + [R.SHARING.UNKNOWN]
    winner = winner or labels[0]
    logits = {label: 2 if label == winner else 0 for label in labels}
    if tie:
        logits[labels[1]] = logits[labels[0]] = 2
    return {"group_id": group["group_id"], "block_id": block_id, "block_rank": rank,
            "status": "input_too_long" if error else "ok", "logits": logits, "argmax_label": winner}


class ProductModelContracts(unittest.TestCase):
    def test_state_and_query_are_product_only_and_desired_values_are_not_separately_injected(self):
        data, contract = fixtures()
        plan = plan_for(data, contract)
        self.assertEqual(len(plan["groups"]), 1)
        group, block = plan["groups"][0], data[4][0]
        state, query = R.state_for(group, block), R.query_for(group)
        self.assertIn(block["model_passage"], state)
        self.assertNotIn("色：青", state)
        self.assertNotIn("色：赤", state)
        self.assertNotIn("現在の選択行", state)
        self.assertNotIn("現在の選択行", query)
        self.assertTrue(query.endswith("\n".join(group["option_values"])))
        self.assertEqual(query.count(group["option_values"][0]), 1)

    def test_product_quotes_exclude_selected_row_and_reject_fabricated_body_spans(self):
        data, contract = fixtures()
        group = plan_for(data, contract)["groups"][0]
        documents = {d["document_id"]: d for d in data[1]}
        refs = R.product_refs(group, documents, data[4][0])
        self.assertEqual({r["field"] for r in refs}, {"title", "description"})
        block = deepcopy(data[4][0])
        block["quote_refs"][0]["quote"] = "偽の説明"
        self.assertIsNone(R.product_refs(group, documents, block))
        block["quote_refs"] = [R.FULL.quote_ref(data[1][0], "selected_axes", "色：青")]
        self.assertIsNone(R.product_refs(group, documents, block))

    def test_every_raw_option_and_unknown_are_classified_for_every_top_three_block(self):
        data, contract = fixtures()
        groups = plan_for(data, contract)["groups"]
        blocks = {b["block_id"]: b for b in data[4]}
        rankings = {groups[0]["group_id"]: {"ordered_block_ids": list(blocks)}}
        choices = R.classification_requests(groups, blocks, rankings)
        self.assertEqual(len(choices), 3)
        for choice in choices:
            self.assertEqual([c["label"] for c in choice["choices"]], groups[0]["option_values"] + [R.SHARING.UNKNOWN])
            self.assertNotIn("選択行", choice["choices"][-1]["description"])
        pairs = R.pairs_for(choices, {g["group_id"]: g for g in groups}, {d["document_id"]: d for d in data[1]}, blocks)
        self.assertEqual(len(pairs), 6)
        self.assertEqual({p["option_value"] for p in pairs}, set(groups[0]["option_values"]))
        self.assertTrue(all("selected_axes" not in {r["field"] for r in p["quote_refs"]} for p in pairs))

    def test_majority_ignores_unknown_tie_and_errors_without_fallback_to_top1(self):
        data, contract = fixtures()
        groups = plan_for(data, contract)["groups"]
        group = groups[0]
        blocks = {b["block_id"]: b for b in data[4]}
        documents = {d["document_id"]: d for d in data[1]}
        bids = list(blocks)
        for third in (candidate_record(group, bids[2], 3, R.SHARING.UNKNOWN), candidate_record(group, bids[2], 3, tie=True),
                      candidate_record(group, bids[2], 3, error=True)):
            with self.subTest(third=third):
                records = [candidate_record(group, bids[0], 1), candidate_record(group, bids[1], 2), third]
                result = R.group_results(groups, records, [], documents, blocks, "product-top3-jev")[group["group_id"]]
                self.assertEqual(result["winning_value"], group["option_values"][0])
                self.assertEqual(len(result["supporting_block_ids"]), 2)
                records[1] = candidate_record(group, bids[1], 2, group["option_values"][1])
                result = R.group_results(groups, records, [], documents, blocks, "product-top3-jev")[group["group_id"]]
                self.assertIsNone(result["winning_value"])

    def test_verified_majority_requires_two_same_block_joint_supports_without_relative_normalization(self):
        data, contract = fixtures()
        groups = plan_for(data, contract)["groups"]
        group, winner = groups[0], groups[0]["option_values"][0]
        blocks = {b["block_id"]: b for b in data[4]}
        documents = {d["document_id"]: d for d in data[1]}
        records = [candidate_record(group, bid, i + 1) for i, bid in enumerate(blocks)]
        nli = [{"group_id": group["group_id"], "block_id": bid, "option_value": winner,
                "token_count_untruncated": 30, "probabilities": {"support": .4, "conflict": .3, "unknown": .3}}
               for bid in list(blocks)[:2]]
        result = R.group_results(groups, records, nli, documents, blocks, "product-top3-verified")[group["group_id"]]
        self.assertEqual(result["winning_value"], winner)
        nli[1]["probabilities"] = {"support": .08, "conflict": .76, "unknown": .16}
        result = R.group_results(groups, records, nli, documents, blocks, "product-top3-verified")[group["group_id"]]
        self.assertIsNone(result["winning_value"])

    def test_positive_and_negative_fanout_preserves_reverse_exactly(self):
        data, contract = fixtures()
        plan = plan_for(data, contract)
        group = plan["groups"][0]
        blocks = {b["block_id"]: b for b in data[4]}
        records = [candidate_record(group, bid, i + 1) for i, bid in enumerate(blocks)]
        result = R.group_results(plan["groups"], records, [], plan["documents"], blocks, "product-top3-jev")
        rows = R.SHARING.fan_out(plan, result, {p["request_id"]: p for p in data[3]})
        self.assertEqual([row["state"] for row in rows], ["support", "unknown", "support"])
        self.assertEqual(rows[-1], data[3][-1])
        self.assertEqual(rows[0]["source_request"], data[0][0])
        self.assertEqual(rows[1]["source_task_bindings"], data[3][1]["source_task_bindings"])
        self.assertEqual(rows[1]["quote_refs"], [])

    def test_empty_eligibility_leaves_all_original_decisions_unchanged(self):
        data, contract = fixtures()
        contract["eligible_forward_request_ids"] = []
        plan = plan_for(data, contract)
        rows = R.SHARING.fan_out(plan, {}, {p["request_id"]: p for p in data[3]})
        self.assertEqual(rows, data[3])

    def test_end_to_end_shares_two_different_rows_and_persists_costs_without_labels(self):
        data, contract = fixtures()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared, eligibility, output = root / "prepared", root / "eligible.json", root / "out"
            F.prepare(prepared, data)
            contract["source_sha256"] = {"prepared": {name: R.sha(prepared / name)
                for name in ("manifest.json", "requests.jsonl", "documents.jsonl", "tasks.jsonl")}}
            R.write_json(eligibility, contract)
            with patch.object(R.JEV, "_infer", side_effect=F.fake_jev_infer) as ji, patch.object(R.NLI, "_infer", side_effect=F.fake_nli_infer) as ni:
                summary = R.run(prepared, eligibility, output, reranker_factory=F.FakeReranker,
                                jev_runner=R.JEV.run_choices, nli_runner=R.NLI.run_pairs)
            self.assertEqual(ji.call_count, 1)
            self.assertEqual(ni.call_count, 1)
            self.assertEqual(summary["group_count"], 1)
            self.assertEqual(summary["actual_top3_jev_block_group_count"], 3)
            self.assertEqual(summary["actual_nli_pair_count"], 6)
            costs = summary["old_actual_row_cost_baseline"]
            self.assertEqual(costs["old_eligible_unique_actual_row_jev_count"], 2)
            self.assertEqual(costs["shared_product_group_count"], 1)
            self.assertEqual(costs["old_eligible_unique_retrieval_pair_count"], 6)
            self.assertEqual(costs["shared_unique_retrieval_pair_count"], 3)
            self.assertFalse(summary["labels_read"])
            self.assertFalse(summary["selected_axes_model_input"])
            self.assertTrue(summary["baseline_exact_file_copy"])
            self.assertEqual((output / "sharing-contract.json").read_bytes(), eligibility.read_bytes())
            for variant in R.VARIANTS:
                rows = R.read(output / (variant + "-predictions.jsonl"))
                self.assertEqual([row["state"] for row in rows], ["support", "unknown", "support"])
                self.assertEqual(rows[-1], data[3][-1])
                self.assertTrue(all(ref["field"] in {"title", "description"} for ref in rows[0]["quote_refs"]))
                self.assertEqual(summary["output_sha256"][variant + "-predictions.jsonl"], R.sha(output / (variant + "-predictions.jsonl")))

    def test_eligibility_contract_source_hashes_are_checked_before_grouping(self):
        data, contract = fixtures()
        with tempfile.TemporaryDirectory() as td:
            prepared = Path(td) / "prepared"
            F.prepare(prepared, data)
            with self.assertRaisesRegex(ValueError, "requires pinned"):
                R.verify_contract_sources(contract, prepared)
            contract["source_sha256"] = {"prepared": {name: R.sha(prepared / name)
                for name in ("manifest.json", "requests.jsonl", "documents.jsonl", "tasks.jsonl")}}
            R.verify_contract_sources(contract, prepared)
            contract["source_sha256"]["prepared"]["tasks.jsonl"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "source SHA256 differs"):
                R.verify_contract_sources(contract, prepared)

    def test_symmetric_and_reverse_requests_cannot_be_silently_added_to_shared_scope(self):
        data, contract = fixtures()
        contract["eligible_forward_request_ids"].append(data[0][-1]["request_id"])
        with self.assertRaisesRegex(ValueError, "reverse"):
            plan_for(data, contract)
        contract["eligible_forward_request_ids"] = []
        plan = plan_for(data, contract)
        self.assertEqual(set(plan["passthrough_request_ids"]), {r["request_id"] for r in data[0]})

    def test_existing_output_and_nonfrozen_settings_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(FileExistsError):
                R.run(root, root, root)
            with self.assertRaisesRegex(ValueError, "frozen CPU"):
                R.run(root, root, root / "out", threads=2)


if __name__ == "__main__":
    unittest.main()
