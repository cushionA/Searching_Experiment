"""Synthetic CPU-free contracts; these fixtures are not SKU evaluation data."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKU_DIR = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(SKU_DIR))
SPEC = importlib.util.spec_from_file_location("scoped_condition_models", SKU_DIR / "trial_scoped_condition_gliner_v1.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def document(text="現在の商品は青色の4枚セットです🙂。別の商品への案内はレースなし。"):
    doc = {"document_id": "fixed-au:synthetic-contract", "title": {"text": "4枚セットのテスト商品"},
           "description": {"text": text, "complete": True, "offset_unit": "unicode_characters"}}
    doc["document_sha256"] = RUNNER.digest(doc)
    return doc


def request(doc=None, value="幅100×丈80cm（4枚組） / レースあり", direction="rakuten_to_au"):
    doc = doc or document()
    axes = [{"axis_name": "カラー", "value": "青"}, {"axis_name": "サイズ", "value": "幅100×丈80cm（4枚組）"}]
    semantic = {"direction": direction, "axis_name": "販売仕様", "selected_value": value,
                "option_values": ["幅100×丈80cm（4枚組） / レースあり", "幅100×丈80cm（2枚組） / レースなし"],
                "target_document_id": doc["document_id"], "target_document_sha256": doc["document_sha256"],
                "target_selected_axes": axes, "target_selected_axes_text": "カラー：青\nサイズ：幅100×丈80cm（4枚組）"}
    sha = RUNNER.digest(semantic)
    return {**semantic, "request_id": "generic-description:" + sha, "request_sha256": sha,
            "applicability_required": True, "production_adoption_allowed": False}


def record(state="support", confidence=.8, status="ok", refs=None):
    return {"chosen_state": state, "confidence": confidence, "status": status, "quote_refs": refs or []}


def make_fixture(root):
    subset, ranked = root / "subset", root / "ranked"
    subset.mkdir()
    ranked.mkdir()
    doc = document()
    req = request(doc)
    task = {"task_id": "synthetic-contract-task", "request_id": req["request_id"],
            "direction": req["direction"], "raw_condition": {"value": req["selected_value"], "source_span": [0, 30]},
            "option_values": req["option_values"], "case_id": "synthetic-contract-case", "au_row_key": "row-blue",
            "source_sku_key": "raw-captured-contract-key", "source_ref": {"locator": "$.axis", "sha256": "b" * 64}}
    for name, rows in (("requests.jsonl", [req]), ("documents.jsonl", [doc]), ("tasks.jsonl", [task])):
        RUNNER.write_rows(subset / name, rows)
    RUNNER.write_json(subset / "manifest.json", {"synthetic_contract_fixture": True,
        "output_sha256": {n: RUNNER.sha(subset / n) for n in ("requests.jsonl", "documents.jsonl", "tasks.jsonl")}})
    payload = {k: req[k] for k in RUNNER.RANKING_FIELDS}
    group = RUNNER.digest(payload)
    ref = RUNNER.quote_ref(doc, "description", doc["description"]["text"], 0, len(doc["description"]["text"]))
    window = {"group_id": group, "window_id": doc["document_id"] + ":description:w0",
              "derived_text_start": ref["start"], "derived_text_end": ref["end"],
              "text": ref["quote"], "quote_ref": ref, "offset_unit": "unicode_characters",
              "offset_space": doc["document_id"] + ":description", "score": .4, "selected": True, "status": "ok"}
    RUNNER.write_rows(ranked / "source-requests.jsonl", [req])
    RUNNER.write_rows(ranked / "source-documents.jsonl", [doc])
    RUNNER.write_rows(ranked / "model-groups.jsonl", [{"group_id": group, "payload": payload, "request_ids": [req["request_id"]]}])
    RUNNER.write_rows(ranked / "ranking-windows.jsonl", [window])
    RUNNER.write_json(ranked / "freeze.json", {"top_k": 5, "document_window_chars": 300, "overlap_chars": 75})
    RUNNER.write_json(ranked / "summary.json", {"output_sha256": {p.name: RUNNER.sha(p) for p in ranked.iterdir() if p.is_file()}})
    return subset, ranked, doc, req, task, window


class ScopedConditionModelContracts(unittest.TestCase):
    def test_complete_hypothesis_is_retained_without_removing_parts(self):
        req = request()
        payload = RUNNER.model_payload(req)
        self.assertEqual(payload["selected_value"], req["selected_value"])
        model = RUNNER.request_for(payload, document(), "entailment")
        self.assertIn("軸名：販売仕様\n選択値：" + req["selected_value"], model["text"])
        self.assertEqual(model["hypothesis"], {"axis_name": "販売仕様", "selected_value": req["selected_value"]})

    def test_selected_hypothesis_changes_model_input_group(self):
        one = request()
        two = request(value=one["option_values"][1])
        self.assertNotEqual(RUNNER.model_payload(one), RUNNER.model_payload(two))

    def test_grouping_preserves_original_ranking_binding(self):
        req = request()
        expected = RUNNER.digest({k: req[k] for k in RUNNER.RANKING_FIELDS})
        self.assertEqual(RUNNER.model_payload(req)["received_ranking_group_id"], expected)

    def test_forward_and_reverse_source_direction_is_preserved(self):
        self.assertEqual(RUNNER.model_payload(request(direction="au_to_rakuten"))["direction"], "au_to_rakuten")

    def test_all_target_axes_are_rendered_and_checked(self):
        req = request()
        model = RUNNER.request_for(RUNNER.model_payload(req), document(), "ownership")
        self.assertIn("現在の選択行：\n" + req["target_selected_axes_text"], model["text"])
        req["target_selected_axes_text"] = "カラー：青"
        with self.assertRaisesRegex(ValueError, "rendering differs"):
            RUNNER.validate_request(req)

    def test_request_hash_binds_all_raw_options_and_order(self):
        req = request()
        req["option_values"].reverse()
        with self.assertRaisesRegex(ValueError, "identity differs"):
            RUNNER.validate_request(req)

    def test_request_hash_binds_target_document(self):
        req = request()
        req["target_document_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "identity differs"):
            RUNNER.validate_request(req)

    def test_selected_value_must_belong_to_whole_source_options(self):
        req = request()
        req["selected_value"] = "レースあり"
        with self.assertRaisesRegex(ValueError, "whole original alternatives"):
            RUNNER.validate_request(req)

    def test_hypothesis_is_a_question_not_the_expected_class(self):
        model = RUNNER.request_for(RUNNER.model_payload(request()), document(), "entailment")
        self.assertEqual(set(model["schema_label_to_state"].values()), {"support", "conflict", "unknown"})
        self.assertNotIn(request()["selected_value"], model["schema_labels"])
        self.assertIn("正解ではありません", model["instruction"])

    def test_whole_source_options_not_used_as_relation_class_labels(self):
        req = request()
        original = copy.deepcopy(req)
        model = RUNNER.request_for(RUNNER.model_payload(req), document(), "ownership")
        self.assertEqual(req, original)
        self.assertEqual(model["schema_labels"], list(RUNNER.LABEL_TO_STATE))
        self.assertNotIn(req["option_values"][1], model["text"])

    def test_two_variants_keep_same_text_hypothesis_classes(self):
        payload = RUNNER.model_payload(request())
        one = RUNNER.request_for(payload, document(), "entailment", "元の本文🙂")
        two = RUNNER.request_for(payload, document(), "ownership", "元の本文🙂")
        for key in ("text", "hypothesis", "schema_labels", "schema_task_name"):
            self.assertEqual(one[key], two[key])
        self.assertNotEqual(one["instruction"], two["instruction"])
        self.assertIn("別商品への案内", two["instruction"])

    def test_original_title_row_body_are_present(self):
        req, doc = request(), document()
        model = RUNNER.request_for(RUNNER.model_payload(req), doc, "ownership", doc["description"]["text"])
        for value in (doc["title"]["text"], req["target_selected_axes_text"], doc["description"]["text"]):
            self.assertIn(value, model["text"])

    def test_unicode_quote_offsets_bind_to_exact_original_slice(self):
        doc = document("A仕様🙂Z")
        self.assertEqual(RUNNER.quote_ref(doc, "description", doc["description"]["text"], 1, 4),
                         {"document_id": doc["document_id"], "field": "description", "start": 1, "end": 4, "quote": "仕様🙂"})

    def test_invalid_quote_spans_are_rejected(self):
        for start, end in ((True, 2), (-1, 2), (0, 0), (1, 99)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                RUNNER.quote_ref(document(), "title", "商品", start, end)

    def test_unknown_does_not_automatically_contradict(self):
        self.assertEqual(RUNNER.aggregate([record("unknown", .99)]), ("unknown", []))

    def test_no_or_low_confidence_records_are_unknown(self):
        for records in ([], [record("support", .49)], [record("conflict", .49)]):
            with self.subTest(records=records):
                self.assertEqual(RUNNER.aggregate(records), ("unknown", []))

    def test_conflict_vetoes_high_confidence_support(self):
        ref = RUNNER.quote_ref(document(), "title", "商品")
        result = RUNNER.aggregate([record("support", .99), record("conflict", .5, refs=[ref])])
        self.assertEqual(result, ("conflict", [ref]))

    def test_unknown_does_not_veto_one_direct_support(self):
        self.assertEqual(RUNNER.aggregate([record("support", .5), record("unknown", .99)])[0], "support")

    def test_non_ok_output_never_supports_or_conflicts(self):
        for state in ("support", "conflict"):
            self.assertEqual(RUNNER.aggregate([record(state, .99, "input_too_long")]), ("unknown", []))

    def test_only_decisive_state_refs_are_retained_and_deduplicated(self):
        ref = RUNNER.quote_ref(document(), "title", "商品")
        self.assertEqual(RUNNER.aggregate([record("support", .8, refs=[ref, dict(ref)]), record("unknown", .9)]), ("support", [ref]))

    def test_overlong_complete_model_input_is_not_truncated(self):
        req = RUNNER.request_for(RUNNER.model_payload(request()), document(), "ownership", "長い原文" * 300)
        row = {"request": req, "quote_refs": [{"quote": "全文"}]}
        original = copy.deepcopy(row)
        self.assertFalse(RUNNER.length_status(row, 513))
        self.assertEqual(row["request"], original["request"])
        self.assertEqual(row["quote_refs"], original["quote_refs"])
        self.assertEqual((row["status"], row["chosen_state"], row["confidence"]), ("input_too_long", "unknown", None))

    def test_exact_token_boundary_is_eligible(self):
        self.assertTrue(RUNNER.length_status({}, 512))

    def test_classifier_unknown_label_remains_unknown(self):
        req = RUNNER.request_for(RUNNER.model_payload(request()), document(), "entailment")
        labels = req["schema_labels"]
        result = SimpleNamespace(label=labels[2], confidence=.8, probabilities=dict(zip(labels, [.1, .1, .8])))
        self.assertEqual(RUNNER.validate_result(result, req)["chosen_state"], "unknown")

    def test_classifier_cannot_be_forced_to_support_desired_value(self):
        req = RUNNER.request_for(RUNNER.model_payload(request()), document(), "entailment")
        labels = req["schema_labels"]
        result = SimpleNamespace(label=labels[1], confidence=.8, probabilities=dict(zip(labels, [.1, .8, .1])))
        self.assertEqual(RUNNER.validate_result(result, req)["chosen_state"], "conflict")

    def test_invalid_classifier_probabilities_are_rejected(self):
        req = RUNNER.request_for(RUNNER.model_payload(request()), document(), "entailment")
        labels = req["schema_labels"]
        result = SimpleNamespace(label=labels[0], confidence=.8, probabilities=dict(zip(labels, [.8, .8, .8])))
        with self.assertRaisesRegex(ValueError, "sum to one"):
            RUNNER.validate_result(result, req)

    def test_inference_exception_is_unknown_and_preserves_entire_input(self):
        req = RUNNER.request_for(RUNNER.model_payload(request()), document(), "ownership", "原本本文")
        record = {"request": req, "quote_refs": [{"quote": "原本本文"}]}
        def fail():
            raise RuntimeError("test model failure")
        RUNNER.apply_model_results([(record, req)], fail)
        self.assertEqual((record["status"], record["chosen_state"], record["error_type"]),
                         ("inference_error", "unknown", "RuntimeError"))
        self.assertEqual(record["request"], req)
        self.assertEqual(RUNNER.aggregate([record]), ("unknown", []))

    def test_malformed_model_result_cannot_leave_partial_support(self):
        req = RUNNER.request_for(RUNNER.model_payload(request()), document(), "ownership")
        labels = req["schema_labels"]
        good = SimpleNamespace(label=labels[0], confidence=.8, probabilities=dict(zip(labels, [.8, .1, .1])))
        one, two = {}, {}
        RUNNER.apply_model_results([(one, req), (two, req)], lambda: [{req["schema_task_name"]: good}, {}])
        self.assertEqual([one["chosen_state"], two["chosen_state"]], ["unknown", "unknown"])
        self.assertEqual([one["status"], two["status"]], ["inference_error", "inference_error"])

    def test_result_count_mismatch_is_an_explicit_model_error(self):
        req = RUNNER.request_for(RUNNER.model_payload(request()), document(), "ownership")
        record = {}
        RUNNER.apply_model_results([(record, req)], lambda: [])
        self.assertEqual(record["status"], "inference_error")
        self.assertIn("count differs", record["error_message"])

    def test_exact_frozen_sources_and_whole_bindings_load_without_models(self):
        with tempfile.TemporaryDirectory() as td:
            subset, ranked, doc, req, task, window = make_fixture(Path(td))
            requests, docs, bindings, windows, *_ = RUNNER.load_sources(subset, ranked)
            self.assertEqual(requests, [req])
            self.assertEqual(docs[doc["document_id"]], doc)
            self.assertEqual(bindings[req["request_id"]], [task])
            self.assertEqual(windows[window["group_id"]], [window])

    def test_received_window_cannot_quote_a_sibling_document(self):
        with tempfile.TemporaryDirectory() as td:
            _, _, doc, _, _, window = make_fixture(Path(td))
            window["quote_ref"]["document_id"] = "fixed-au:sibling"
            with self.assertRaisesRegex(ValueError, "bind to full target"):
                RUNNER.verify_window(window, doc)

    def test_subset_source_bytes_are_hash_verified(self):
        with tempfile.TemporaryDirectory() as td:
            subset, ranked, *_ = make_fixture(Path(td))
            with (subset / "requests.jsonl").open("a") as stream:
                stream.write("\n")
            with self.assertRaisesRegex(ValueError, "subset source SHA"):
                RUNNER.load_sources(subset, ranked)

    def test_received_ranking_bytes_are_hash_verified(self):
        with tempfile.TemporaryDirectory() as td:
            subset, ranked, *_ = make_fixture(Path(td))
            with (ranked / "ranking-windows.jsonl").open("a") as stream:
                stream.write("\n")
            with self.assertRaisesRegex(ValueError, "ranking source SHA"):
                RUNNER.load_sources(subset, ranked)

    def test_completed_original_top5_settings_are_required(self):
        with tempfile.TemporaryDirectory() as td:
            subset, ranked, *_ = make_fixture(Path(td))
            (ranked / "freeze.json").write_text(json.dumps({"top_k": 3, "document_window_chars": 300, "overlap_chars": 75}))
            summary = json.loads((ranked / "summary.json").read_text())
            summary["output_sha256"]["freeze.json"] = RUNNER.sha(ranked / "freeze.json")
            (ranked / "summary.json").write_text(json.dumps(summary))
            with self.assertRaisesRegex(ValueError, "frozen top5"):
                RUNNER.load_sources(subset, ranked)

    def test_output_directory_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileExistsError):
                RUNNER.run(Path(td), Path(td), Path(td), "ownership", Path("not-loaded"))


if __name__ == "__main__":
    unittest.main()
