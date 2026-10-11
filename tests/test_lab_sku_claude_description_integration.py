"""Artificial contract fixtures; never population or model-accuracy samples."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

SCRIPTS = Path(__file__).resolve().parents[1] / "experiments" / "sku-matching"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("claude_description_integration_contract", SCRIPTS / "integrate_claude_description_v1.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def raw_axis(key, value, options, rak=True):
    span = {"quote": value, "start": 0, "end": len(value), "raw_file": "artificial-contract-fixture", "sha256": "a" * 64}
    return ({"axis_key": key, "axis_index": 0, "axis_label": key, "value": value, "family_values": options, "value_span": span}
            if rak else {"axis_name": key, "value": value, "value_span": span})


class IntegrationContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.generic, self.handoff, self.archive = self.root / "generic", self.root / "handoff", self.root / "archive.zip"
        self.generic.mkdir()
        self.handoff.mkdir()
        self.rak_axis = raw_axis("サイズ", "100×80cm", ["100×80cm", "100×90cm"])
        self.au_axis = raw_axis("サイズ", "幅100×丈80cm(4枚組)", ["幅100×丈80cm(4枚組)"], False)
        self.family_axis = raw_axis("カラー", "緑", ["緑", "赤"])
        self.case_values = []
        for identity, cohort, dossier, product, axis in (("L0", "legacy", "D0", "A0", self.rak_axis),
                                                       ("L1", "legacy", "D0", "A0", self.rak_axis),
                                                       ("L2", "legacy", "D0", "A0", self.rak_axis),
                                                       ("F0", "family", "D1", "A1", self.family_axis)):
            self.case_values.append({"case_id": identity, "cohort": cohort, "dossier_id": dossier, "au_product_id": product,
                                     "rakuten": {"source_sku_key": "R:" + identity, "axes": [copy.deepcopy(axis)]}})
        self.product_values = [{"dossier_id": "D0", "au": {"product_id": "A0", "title": "人工fixture・4枚組", "descriptions": [{"text": "人工fixture・幅100、丈80、レース2枚付き。"}],
                                                         "rows": [{"row_key": "A0:0", "axes": [copy.deepcopy(self.au_axis)]},
                                                                  {"row_key": "A0:1", "axes": [raw_axis("サイズ", "幅100×丈90cm(4枚組)", [], False)]}]},
                                "rakuten": {"title": "人工fixture・カーテン", "url": "fixture:R0", "descriptions": [{"text": "人工fixture・4枚セット。"}]}},
                               {"dossier_id": "D1", "au": {"product_id": "A1", "title": "人工fixture・色", "descriptions": [],
                                                         "rows": [{"row_key": "A1:0", "axes": [raw_axis("カラー", "緑", [], False)]},
                                                                  {"row_key": "A1:1", "axes": [raw_axis("カラー", "赤", [], False)]}]},
                                "rakuten": {"title": "人工fixture・色", "url": "fixture:R1", "descriptions": []}}]
        self.contexts = [{"case_id": identity, "au_row_key": "A0:0", "source_sku_key": "R:" + identity, "au_product_id": "A0", "aligned_conditions": []}
                         for identity in ("L0", "L1")]
        self.forward = [{"case_id": identity, "au_row_key": "A0:0", "direction": "rakuten_to_au", "source_sku_key": "R:" + identity,
                         "raw_condition": copy.deepcopy(self.rak_axis), "option_values": self.rak_axis["family_values"], "selected_value": self.rak_axis["value"], "producer_status": "model_candidate"}
                        for identity in ("L0", "L1")]
        self.reverse = [{"case_id": identity, "au_row_key": "A0:0", "direction": "au_to_rakuten", "source_sku_key": "R:" + identity,
                         "raw_condition": copy.deepcopy(self.au_axis), "option_values": ["幅100×丈80cm(4枚組)", "幅100×丈90cm(4枚組)"], "selected_value": self.au_axis["value"], "origin": "model_candidate"}
                        for identity in ("L0", "L1")]
        self.decisions = {"legacy": [{"case_id": identity, "dossier_id": "D0", "decision": "unmatched", "top_row_key": None,
                                       "reason": "pending_description_check", "row_states": {"contradiction": 1, "pending_description_check": 1}}
                                      for identity in ("L0", "L1")] + [{"case_id": "L2", "dossier_id": "D0", "decision": "unmatched", "top_row_key": None,
                                                                        "reason": "symmetric_condition_unresolved", "row_states": {"symmetric_unresolved": 2}}],
                          "family": [{"case_id": "F0", "dossier_id": "D1", "decision": "matched", "top_row_key": "A1:0",
                                       "reason": "all_conditions_aligned_on_one_row", "row_states": {"aligned": 1, "contradiction": 1}}]}
        self.pairs = {"legacy": [], "family": [{"dossier_id": "D1", "axis_key": "カラー", "value": "緑", "au_axis": "カラー", "au_value": "緑", "paired": True, "matched_by": "identical"}]}
        self.axes = {"legacy": [{"dossier_id": "D0", "symmetric": {}, "au_only": ["サイズ"]}],
                     "family": [{"dossier_id": "D1", "symmetric": {"カラー": "カラー"}, "au_only": []}]}
        self.original = {"legacy": b"{\"fixture\":true}\n", "family": b""}
        self.build_sources()

    def tearDown(self):
        self.temp.cleanup()

    def build_sources(self):
        def data(values):
            return b"".join(MOD.encoded(row) + b"\n" for row in values)
        for name, values in (("cases.jsonl", self.case_values), ("products.jsonl", self.product_values)):
            (self.generic / name).write_bytes(data(values))
        generic_sha = {name: MOD.sha((self.generic / name).read_bytes()) for name in ("cases.jsonl", "products.jsonl")}
        (self.generic / "manifest.json").write_bytes(MOD.encoded({"output_sha256": generic_sha, "contract_fixture_only": True}))
        payload, sources = {}, []
        for cohort in ("legacy", "family"):
            parent = "fixture/" + cohort + "/align"
            payload[parent + "/handoff.jsonl"] = self.original[cohort]
            for name, values in (("decisions.jsonl", self.decisions[cohort]), ("axes.jsonl", self.axes[cohort]), ("value_pairs.jsonl", self.pairs[cohort])):
                payload[parent + "/" + name] = data(values)
            counts = {}
            for decision in self.decisions[cohort]:
                counts[decision["decision"]] = counts.get(decision["decision"], 0) + 1
            sources.append({"member": parent + "/handoff.jsonl", "producer_summary": {"decisions": counts}})
        checkpoint = {"entries": {name: {"size_bytes": len(body), "sha256": MOD.sha(body)} for name, body in payload.items()},
                      "payload_file_count": len(payload), "uncompressed_payload_bytes": sum(map(len, payload.values()))}
        with ZipFile(self.archive, "w") as zipped:
            for name, body in payload.items():
                zipped.writestr(name, body)
            zipped.writestr("SKU_GATE_CHECKPOINT.json", MOD.encoded(checkpoint))
        for name, values in (("cards.jsonl", self.forward), ("reverse_conditions.jsonl", self.reverse), ("upstream_context.jsonl", self.contexts)):
            (self.handoff / name).write_bytes(data(values))
        (self.handoff / "original_handoff.jsonl").write_bytes(self.original["legacy"] + self.original["family"])
        manifest = {"output_sha256": {name: MOD.sha((self.handoff / name).read_bytes()) for name in
                                     ("cards.jsonl", "reverse_conditions.jsonl", "upstream_context.jsonl", "original_handoff.jsonl")},
                    "archive_sha256": MOD.sha(self.archive.read_bytes()), "input_sha256": generic_sha, "upstream_head": "fixture-v5",
                    "selected_handoff_sources": sources, "handoff_row_count": len(self.contexts)}
        (self.handoff / "manifest.json").write_bytes(MOD.encoded(manifest))

    def prepared(self):
        output = self.root / "prepared"
        result = MOD.prepare(self.generic, self.handoff, self.archive, output)
        return output, result

    def prediction(self, request, winner=None, state="support"):
        text = request["target_selected_axes_text"]
        return {"request_id": request["request_id"], "request_sha256": request["request_sha256"], "selected_value": request["selected_value"],
                "state": state, "winning_value": request["selected_value"] if winner is None else winner,
                "scope_status": "unproved", "quote_refs": [{"document_id": request["target_document_id"], "field": "selected_axes", "start": 0, "end": len(text), "quote": text}]}

    def gate(self, prepared, predictions):
        path = self.root / "predictions.jsonl"
        path.write_bytes(b"".join(MOD.encoded(value) + b"\n" for value in predictions))
        output = self.root / "finished"
        result = MOD.finish(prepared, path, output)
        return result, MOD.index(MOD.rows(output / "decisions.jsonl"), "case_id", "results")

    def test_prepare_preserves_whole_raw_values_and_reverse(self):
        path, result = self.prepared()
        self.assertEqual(result["case_count"], 4)
        self.assertEqual(result["task_count"], 4)
        self.assertEqual(result["request_count"], 2)
        tasks = MOD.rows(path / "tasks.jsonl")
        reverse = next(task for task in tasks if task["direction"] == "au_to_rakuten")
        self.assertEqual(reverse["raw_condition"], self.au_axis)
        self.assertEqual(reverse["raw_condition"]["value"], "幅100×丈80cm(4枚組)")
        self.assertEqual(reverse["option_values"], self.reverse[0]["option_values"])
        self.assertFalse(result["labels_read"])
        self.assertFalse(result["production_adoption_allowed"])

    def test_identical_model_inputs_deduplicate_without_merging_cases(self):
        path, result = self.prepared()
        self.assertEqual(result["request_count"], 2)
        self.assertEqual(len(MOD.rows(path / "case_plans.jsonl")), 4)
        self.assertEqual(len({task["source_sku_key"] for task in MOD.rows(path / "tasks.jsonl")}), 2)

    def test_both_directions_supported_unique_row_accepts_experimentally(self):
        path, _ = self.prepared()
        result, decisions = self.gate(path, [self.prediction(request) for request in MOD.rows(path / "requests.jsonl")])
        self.assertEqual(result["decision_counts"], {"accept": 3, "drop": 1})
        self.assertEqual(decisions["L0"]["top_row_key"], "A0:0")
        self.assertEqual(decisions["L0"]["upstream_contradiction_row_count"], 1)
        self.assertFalse(decisions["L0"]["production_adoption_allowed"])
        self.assertEqual(decisions["L2"]["decision"], "drop")
        self.assertNotIn("review", str(result["decision_counts"]))
        self.assertEqual(result["unproved_scope_supported_request_count"], 2)
        self.assertIn("must not be adopted in production", result["diagnostic_scope_policy"])

    def test_missing_reverse_drops_even_with_forward_support(self):
        path, _ = self.prepared()
        forward = next(request for request in MOD.rows(path / "requests.jsonl") if request["direction"] == "rakuten_to_au")
        result, decisions = self.gate(path, [self.prediction(forward)])
        self.assertEqual(decisions["L0"]["decision"], "drop")
        self.assertEqual(result["missing_prediction_count"], 1)
        self.assertEqual(decisions["F0"]["decision"], "accept")

    def test_alternative_winner_is_unknown_even_if_model_calls_conflict(self):
        path, _ = self.prepared()
        predictions = [self.prediction(request, winner=request["option_values"][1], state="conflict") for request in MOD.rows(path / "requests.jsonl")]
        result, decisions = self.gate(path, predictions)
        self.assertEqual(result["condition_states"], {"unknown": 2})
        self.assertEqual(decisions["L0"]["decision"], "drop")

    def test_invalid_quote_does_not_prove_support(self):
        path, _ = self.prepared()
        predictions = [self.prediction(request) for request in MOD.rows(path / "requests.jsonl")]
        predictions[0]["quote_refs"][0]["quote"] = "not in the source"
        result, decisions = self.gate(path, predictions)
        self.assertIn("missing_or_unverified_literal_quote", result["condition_reasons"])
        self.assertEqual(decisions["L0"]["decision"], "drop")

    def test_changed_prediction_condition_hash_rejected(self):
        path, _ = self.prepared()
        prediction = self.prediction(MOD.rows(path / "requests.jsonl")[0])
        prediction["request_sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "binding differs"):
            self.gate(path, [prediction])

    def test_changed_prediction_selected_composite_rejected(self):
        path, _ = self.prepared()
        request = next(request for request in MOD.rows(path / "requests.jsonl") if request["direction"] == "au_to_rakuten")
        prediction = self.prediction(request)
        prediction["selected_value"] = "100×80cm"
        with self.assertRaisesRegex(ValueError, "binding differs"):
            self.gate(path, [prediction])

    def test_singleton_au_axis_missing_from_direct_is_preserved_as_duty(self):
        for row in self.product_values[1]["au"]["rows"]:
            row["axes"].append(raw_axis("構成", "バー付き", [], False))
        self.build_sources()
        path, result = self.prepared()
        self.assertEqual(result["counts"]["unrepresented_singleton_au_axes"], 1)
        self.assertEqual(result["counts"]["upstream_direct_missing_au_axis"], 1)
        duty = next(task for task in MOD.rows(path / "tasks.jsonl") if task["origin"] == "unrepresented_au_axis")
        self.assertEqual(duty["option_values"], ["バー付き"])
        self.assertEqual(duty["direction"], "au_to_rakuten")
        _, decisions = self.gate(path, [])
        self.assertEqual(decisions["F0"]["decision"], "drop")

    def test_pending_missing_singleton_au_axis_also_preserved(self):
        for row in self.product_values[0]["au"]["rows"]:
            row["axes"].append(raw_axis("構成", "レース付き", [], False))
        self.build_sources()
        path, result = self.prepared()
        self.assertEqual(result["counts"]["unrepresented_singleton_au_axes"], 2)
        duties = [task for task in MOD.rows(path / "tasks.jsonl") if task["origin"] == "unrepresented_au_axis"]
        self.assertEqual(len(duties), 2)
        self.assertTrue(all(task["raw_condition"]["value"] == "レース付き" for task in duties))

    def test_pending_row_count_mismatch_rejected(self):
        self.decisions["legacy"][0]["row_states"] = {"pending_description_check": 2}
        self.build_sources()
        with self.assertRaisesRegex(ValueError, "pending count"):
            self.prepared()

    def test_fixed_product_row_count_mismatch_rejected(self):
        self.decisions["legacy"][0]["row_states"] = {"contradiction": 2, "pending_description_check": 1}
        self.build_sources()
        with self.assertRaisesRegex(ValueError, "fixed AU row array"):
            self.prepared()

    def test_card_partial_value_cannot_consume_whole_composite(self):
        self.reverse[0]["raw_condition"]["value"] = "100×80cm"
        self.build_sources()
        with self.assertRaisesRegex(ValueError, "partially consumed"):
            self.prepared()

    def test_card_omitted_alternatives_rejected(self):
        self.forward[0]["option_values"] = ["100×80cm"]
        self.build_sources()
        with self.assertRaisesRegex(ValueError, "partially consumed"):
            self.prepared()

    def test_external_row_routing_rejected(self):
        self.contexts[0]["au_row_key"] = "foreign:row"
        self.forward[0]["au_row_key"] = "foreign:row"
        self.reverse[0]["au_row_key"] = "foreign:row"
        self.build_sources()
        with self.assertRaises(KeyError):
            self.prepared()

    def test_direct_model_candidate_not_accepted_as_identical(self):
        self.pairs["family"][0]["matched_by"] = "model"
        self.build_sources()
        path, result = self.prepared()
        self.assertEqual(result["counts"]["origin:upstream_direct_unresolved"], 1)
        _, decisions = self.gate(path, [])
        self.assertEqual(decisions["F0"]["decision"], "drop")

    def test_missing_original_rakuten_axis_in_pending_handoff_rejected(self):
        self.forward.pop(0)
        self.build_sources()
        with self.assertRaisesRegex(ValueError, "lost an original Rakuten"):
            self.prepared()

    def test_source_checksum_change_rejected(self):
        (self.generic / "products.jsonl").write_bytes(b"{}\n")
        with self.assertRaisesRegex(ValueError, "SHA differs"):
            self.prepared()

    def test_refuses_output_overwrite(self):
        path, _ = self.prepared()
        with self.assertRaises(FileExistsError):
            MOD.prepare(self.generic, self.handoff, self.archive, path)

    def test_unreceived_cohort_is_not_silently_scored_or_adopted(self):
        extra = copy.deepcopy(self.case_values[0])
        extra.update(case_id="other", cohort="candidate")
        self.case_values.append(extra)
        self.build_sources()
        _, result = self.prepared()
        self.assertEqual(result["input_case_count"], 5)
        self.assertEqual(result["case_count"], 4)
        self.assertEqual(result["input_cases_outside_received_cohorts"], 1)

    def test_unknown_competing_row_blocks_unique_supported_row(self):
        self.decisions["legacy"][0]["row_states"] = {"pending_description_check": 2}
        other_context = copy.deepcopy(self.contexts[0])
        other_context["au_row_key"] = "A0:1"
        self.contexts.append(other_context)
        other_forward = copy.deepcopy(self.forward[0])
        other_forward["au_row_key"] = "A0:1"
        self.forward.append(other_forward)
        other_reverse = copy.deepcopy(self.reverse[0])
        other_reverse["au_row_key"] = "A0:1"
        other_reverse["raw_condition"] = self.product_values[0]["au"]["rows"][1]["axes"][0]
        other_reverse["selected_value"] = other_reverse["raw_condition"]["value"]
        self.reverse.append(other_reverse)
        self.build_sources()
        path, _ = self.prepared()
        wanted = {task["request_id"] for task in MOD.rows(path / "tasks.jsonl") if task["case_id"] == "L0" and task["au_row_key"] == "A0:0"}
        predictions = [self.prediction(request) for request in MOD.rows(path / "requests.jsonl") if request["request_id"] in wanted]
        _, decisions = self.gate(path, predictions)
        self.assertEqual(decisions["L0"]["decision"], "drop")
        self.assertEqual(decisions["L0"]["reason"], "unknown_competing_row")

    def test_scoring_separate_machine_labels_only_after_frozen_decisions(self):
        path, _ = self.prepared()
        self.gate(path, [])
        labels = self.root / "labels.jsonl"
        labels.write_bytes(b"\n".join(MOD.encoded(row) for row in [
            {"case_id": "L0", "label": "matched", "matched_au_row_keys": ["A0:0"]},
            {"case_id": "L1", "label": "unmatched"}, {"case_id": "L2", "label": "review"},
            {"case_id": "F0", "label": "matched", "matched_au_row_keys": ["A1:0"]}]) + b"\n")
        result = MOD.score(self.root / "finished", labels, self.root / "score.json")
        self.assertEqual(result["metrics"]["tp"], 1)
        self.assertEqual(result["metrics"]["fn"], 1)
        self.assertEqual(result["metrics"]["tn"], 1)
        self.assertEqual(result["metrics"]["precision"], 1.0)
        self.assertEqual(result["metrics"]["recall"], .5)
        self.assertEqual(result["row_metrics"], {"correct_accepted_row": 1})
        self.assertEqual(len(result["excluded"]), 1)
        self.assertFalse(result["decisions_changed"])
        self.assertTrue(result["labels_read_after_decisions_frozen"])

    def test_zero_adoptions_do_not_report_perfect_precision(self):
        path, _ = self.prepared()
        self.gate(path, [])
        labels = self.root / "labels.jsonl"
        labels.write_bytes(MOD.encoded({"case_id": "L0", "label": "matched"}) + b"\n")
        result = MOD.score(self.root / "finished", labels, self.root / "score.json")
        self.assertIsNone(result["metrics"]["precision"])
        self.assertEqual(result["metrics"]["recall"], 0)

    def test_matched_row_error_separate_from_binary_score(self):
        path, _ = self.prepared()
        self.gate(path, [])
        labels = self.root / "labels.jsonl"
        labels.write_bytes(MOD.encoded({"case_id": "F0", "label": "matched", "matched_au_row_keys": ["wrong:row"]}) + b"\n")
        result = MOD.score(self.root / "finished", labels, self.root / "score.json")
        self.assertEqual(result["metrics"]["tp"], 1)
        self.assertEqual(result["row_metrics"], {"wrong_accepted_row": 1})
        self.assertEqual(len(result["differences"]), 1)

    def test_existing_machine_reference_aliases_are_supported(self):
        path, _ = self.prepared()
        self.gate(path, [])
        labels = self.root / "labels.jsonl"
        labels.write_bytes(MOD.encoded({"case_id": "F0", "decision": "matched", "matching_au_row_keys": ["A1:0"]}) + b"\n")
        result = MOD.score(self.root / "finished", labels, self.root / "score.json")
        self.assertEqual(result["row_metrics"], {"correct_accepted_row": 1})
        self.assertEqual(result["metrics"]["tp"], 1)
        self.assertEqual(len(result["excluded"]), 3)

    def test_conflicting_machine_reference_label_aliases_rejected(self):
        path, _ = self.prepared()
        self.gate(path, [])
        labels = self.root / "labels.jsonl"
        labels.write_bytes(MOD.encoded({"case_id": "F0", "label": "matched", "decision": "unmatched"}) + b"\n")
        with self.assertRaisesRegex(ValueError, "aliases conflict"):
            MOD.score(self.root / "finished", labels, self.root / "score.json")

    def test_conflicting_machine_reference_row_aliases_rejected(self):
        path, _ = self.prepared()
        self.gate(path, [])
        labels = self.root / "labels.jsonl"
        labels.write_bytes(MOD.encoded({"case_id": "F0", "label": "matched", "matched_au_row_keys": ["A1:0"], "matching_au_row_keys": ["A1:1"]}) + b"\n")
        with self.assertRaisesRegex(ValueError, "aliases conflict"):
            MOD.score(self.root / "finished", labels, self.root / "score.json")


if __name__ == "__main__":
    unittest.main()
