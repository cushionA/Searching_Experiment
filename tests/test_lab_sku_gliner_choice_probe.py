"""Artificial contract fixtures; no real SKU labels or model inference."""
from __future__ import annotations
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

PATH = Path(__file__).resolve().parents[1] / "experiments/sku-matching/trial_generic_gliner_choice_v1.py"
SPEC = importlib.util.spec_from_file_location("generic_gliner_choice_probe", PATH)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def fixture(task_id="artificial:1", request_id="artificial:1:option:0", index=0):
    alternatives = ["甲 / 幅120cm", "乙 / 幅120cm"]
    row = {"row_key": "au:artificial:row:1", "axes": [{"axis_name": "色", "value": "乙"}]}
    title = "架空の検査用タイトル"
    return {"id": request_id,
            "premise": "現在のAU商品名：" + title + "\n現在のAU選択行：\n色：乙\n引用本文：" + title,
            "provenance": {"task_id": task_id, "case_id": "artificial-case",
                "condition_id": "artificial-condition", "au_row_key": row["row_key"],
                "axis_name": "型", "option_values": alternatives, "selected_value": alternatives[0],
                "source_sku_key": "artificial-sku", "fixed_au_product_ref": {"product_id": "artificial"},
                "selected_au_row": row, "source_request_id": task_id + ":window:0",
                "candidate_option_index": index, "candidate_option_value": alternatives[index],
                "scope": "title", "window": {"field_kind": "plain_title", "quote": title}}}


class GenericChoiceProbeTests(unittest.TestCase):
    def test_dedup_preserves_original_order_composite_values_and_sources(self):
        one = fixture()
        two = fixture(request_id="artificial:1:option:1", index=1)
        original = copy.deepcopy([one, two])
        requests = MOD.prepare([one, two])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["candidate_labels"], ["甲 / 幅120cm", "乙 / 幅120cm", "不明"])
        self.assertEqual(requests[0]["source_request_ids"], [one["id"], two["id"]])
        self.assertNotIn("甲 / 幅120cm", requests[0]["text"])
        self.assertNotIn("引用本文", requests[0]["text"])
        self.assertEqual([one, two], original)

    def test_first_window_retained_but_binding_changes_refused(self):
        one = fixture()
        later = fixture(request_id="artificial:1:other-window")
        later["provenance"]["source_request_id"] = "different-window"
        self.assertEqual(MOD.prepare([one, later])[0]["provenance"], one["provenance"])
        later["provenance"]["fixed_au_product_ref"] = {"product_id": "different"}
        with self.assertRaises(ValueError):
            MOD.prepare([one, later])

    def test_unknown_label_cannot_alias_a_real_raw_value(self):
        values = ["不明", "不明（情報不足）#1", "なし"]
        self.assertEqual(MOD.unknown_label(values), "不明（情報不足）#2")

    def test_reserved_delimiters_escape_without_whole_value_loss_or_collisions(self):
        values = ["21枚(20枚＋1枚)", "21枚（20枚＋1枚）", "[P]指定"]
        labels, mapping = MOD.schema_choices(values, "不明")
        self.assertEqual(len(set(labels)), 4)
        self.assertEqual(list(mapping.values()), [*values, None])
        self.assertIn("21枚（20枚＋1枚）", labels[0])
        for label in labels:
            self.assertFalse(any(token in label for token in MOD.RESERVED_TOKENS))

    def test_mutated_title_row_and_options_are_rejected(self):
        for field in ("title", "row", "option"):
            row = fixture()
            if field == "title":
                row["provenance"]["window"]["quote"] = "different"
            elif field == "row":
                row["provenance"]["selected_au_row"]["row_key"] = "wrong"
            else:
                row["provenance"]["candidate_option_value"] = "wrong"
            with self.subTest(field=field), self.assertRaises(ValueError):
                MOD.prepare([row])

    def test_duplicate_source_id_and_duplicate_choices_rejected(self):
        with self.assertRaises(ValueError):
            MOD.prepare([fixture(), fixture()])
        row = fixture()
        row["provenance"]["option_values"] = ["same", "same"]
        with self.assertRaises(ValueError):
            MOD.prepare([row])

    def test_classifier_output_must_preserve_full_schema(self):
        request = MOD.prepare([fixture()])[0]
        labels = request["schema_labels"]
        result = SimpleNamespace(label=labels[1], confidence=0.8,
                                 probabilities=dict(zip(labels, [0.1, 0.8, 0.1], strict=True)))
        self.assertEqual(MOD.validate_result(result, request)["chosen_value"], "乙 / 幅120cm")
        result.probabilities.pop(labels[0])
        with self.assertRaises(ValueError):
            MOD.validate_result(result, request)

    def test_probabilities_cannot_hide_invalid_confidence(self):
        request = MOD.prepare([fixture()])[0]
        labels = request["schema_labels"]
        result = SimpleNamespace(label=labels[2], confidence=0.8,
                                 probabilities=dict(zip(labels, [0.1, 0.8, 0.1], strict=True)))
        with self.assertRaises(ValueError):
            MOD.validate_result(result, request)

    def test_existing_output_refused_without_reading_inputs_or_models(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileExistsError):
                MOD.run(Path(directory) / "missing-input", Path(directory), Path(directory) / "missing-model")

    def test_all_windows_keeps_whole_premise_and_all_provenance(self):
        one = fixture()
        duplicate = fixture(request_id="artificial:1:option:1", index=1)
        body = fixture(request_id="artificial:1:body:option:0")
        body["provenance"]["scope"] = "all_windows"
        body["provenance"]["source_request_id"] = "artificial:1:body"
        body["provenance"]["window"] = {"field_kind": "html_description", "quote": "仕様本文",
            "source_ref": {"sha256": "artificial-source-digest"}, "structural_context": [{"tag": "table"}]}
        body["premise"] = body["premise"].split("\n引用本文：")[0] + "\n引用本文：仕様本文\n表の文脈：改行\n<完全な原文>"
        original = copy.deepcopy([one, duplicate, body])
        requests = MOD.prepare([one, duplicate, body], scope="all_windows")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["id"], one["provenance"]["source_request_id"])
        self.assertEqual(requests[1]["id"], "artificial:1:body")
        self.assertEqual(requests[1]["text"], body["premise"])
        self.assertEqual(requests[1]["provenance"], body["provenance"])
        self.assertEqual(requests[1]["option_values"], body["provenance"]["option_values"])
        self.assertEqual([one, duplicate, body], original)

    def test_all_windows_refuses_same_window_with_changed_premise_or_reference(self):
        for kind in ("premise", "window"):
            first = fixture()
            changed = fixture(request_id="artificial:1:option:1", index=1)
            if kind == "premise":
                changed["premise"] += "\n追加文"
            else:
                changed["provenance"]["window"]["source_ref"] = {"sha256": "different"}
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                MOD.prepare([first, changed], scope="all_windows")

    def test_loader_pin_reader_does_not_execute_unrelated_code_or_fixtures(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pins.py"
            path.write_text("REPO = 'test'\nREVISION = 'rev'\nFILES = {'file': {'sha256': 'hash'}}\n"
                            "raise AssertionError('must not execute')\nCASES = unavailable_function()\n")
            self.assertEqual(MOD.snapshot_pins(path)["FILES"], {"file": {"sha256": "hash"}})

    def test_self_attested_manifest_cannot_change_independent_model_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            file = path / "tiny.bin"
            file.write_bytes(b"modified model")
            actual = {"tiny.bin": {"size_bytes": file.stat().st_size, "sha256": MOD.sha(file)}}
            (path / "source-manifest.json").write_text(json.dumps(
                {"repo": MOD.MODEL_ID, "revision": MOD.REVISION, "files": actual}))
            independent = {"REPO": MOD.MODEL_ID, "REVISION": MOD.REVISION,
                           "FILES": {"tiny.bin": {"size_bytes": 5, "sha256": "original-pin"}},
                           "source_sha256": "loader-pin"}
            with mock.patch.object(MOD, "snapshot_pins", return_value=independent), self.assertRaises(ValueError):
                MOD.verify_snapshot(path)

    def test_preflight_uses_actual_compiled_schema_without_truncation_or_fallback(self):
        request = MOD.prepare([fixture()])[0]
        compiled = SimpleNamespace(build=lambda: {"full_original_schema": True})
        classifier = SimpleNamespace(compile_schema=mock.Mock(return_value=compiled))
        processor = SimpleNamespace(collate_fn_inference=mock.Mock(return_value=
            SimpleNamespace(attention_mask=[SimpleNamespace(sum=lambda: 513)])))
        counts = MOD.complete_input_token_counts([request], ["artificial-schema"], classifier, processor)
        self.assertEqual(counts, [513])
        classifier.compile_schema.assert_called_once_with("artificial-schema")
        processor.collate_fn_inference.assert_called_once_with(
            [(request["text"], {"full_original_schema": True})], max_len=None, error_policy="raise")

    def test_actual_over_limit_skipped_even_when_estimate_is_below_limit(self):
        first = MOD.prepare([fixture()])[0]
        first["estimated_input_token_count_diagnostic_only"] = 220
        second = copy.deepcopy(first)
        second["id"] = "other-window"
        second["estimated_input_token_count_diagnostic_only"] = 900
        self.assertEqual(MOD.plan_batches([first, second], [513, 512], 8, "all_windows"), [[1]])

    def test_original_relation_text_preserves_full_frame_and_candidate_source_ids(self):
        first = fixture()
        other = fixture(request_id="artificial:1:option:1", index=1)
        original_full = first["premise"] + "\n表の文脈：<省略してはいけない文>"
        for index, row in enumerate((first, other)):
            row["premise"] = row["provenance"]["window"]["quote"]
            row["provenance"]["original_relation_premise"] = original_full
            row["provenance"]["original_relation_request_id"] = f"original-option-{index}"
        original = copy.deepcopy([first, other])
        requests = MOD.prepare([first, other], "all_windows", "original_relation_premise")
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request["text"], original_full)
        self.assertEqual(request["source_premise"], original_full)
        self.assertEqual(request["source_input_premise"], first["premise"])
        self.assertEqual(request["source_original_relation_request_ids"], ["original-option-0", "original-option-1"])
        self.assertEqual(request["text_source"], "original_relation_premise")
        self.assertEqual(request["provenance"], first["provenance"])
        self.assertNotIn("甲 / 幅120cm", request["text"])
        self.assertEqual([first, other], original)

    def test_original_relation_text_missing_or_changed_quote_or_row_rejected(self):
        for kind in ("missing", "empty", "quote", "row"):
            row = fixture()
            original_full = row["premise"]
            row["premise"] = row["provenance"]["window"]["quote"]
            if kind == "missing":
                pass
            elif kind == "empty":
                row["provenance"]["original_relation_premise"] = " "
            elif kind == "quote":
                row["provenance"]["original_relation_premise"] = original_full.replace("引用本文：架空", "引用本文：別物")
            else:
                row["provenance"]["original_relation_premise"] = original_full.replace("色：乙", "色：甲")
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                MOD.prepare([row], "all_windows", "original_relation_premise")

    def test_original_relation_text_cannot_change_within_one_window(self):
        first = fixture()
        second = fixture(request_id="artificial:1:option:1", index=1)
        for row in (first, second):
            row["provenance"]["original_relation_premise"] = row["premise"]
        second["provenance"]["original_relation_premise"] += "\n異なる注記"
        with self.assertRaises(ValueError):
            MOD.prepare([first, second], "all_windows", "original_relation_premise")


if __name__ == "__main__":
    unittest.main()
