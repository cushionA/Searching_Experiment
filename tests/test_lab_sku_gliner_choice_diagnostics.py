"""Artificial contract fixtures kept separate from the real SKU probe inputs."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("gliner_choice_diagnostics", ROOT / "experiments/sku-matching/score_generic_gliner_choice_v1.py")
DIAG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DIAG)
RUNNER_SPEC = importlib.util.spec_from_file_location("gliner_choice_fixture_builder", ROOT / "experiments/sku-matching/trial_generic_gliner_choice_v1.py")
RUNNER = importlib.util.module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(RUNNER)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows), encoding="utf-8")


def sources(window="w0", kind="plain_title", task="t0"):
    values = ["21枚(パネル20＋ドア1)[P]", "13枚(パネル12＋ドア1)"]
    quote = "人工テスト商品" if kind == "plain_title" else "人工本文"
    premise = "現在のAU商品名：人工テスト商品\n現在のAU選択行：\n色：青\n引用本文：" + quote
    p = {"task_id": task, "case_id": "artificial-case", "condition_id": "artificial-condition",
         "au_row_key": "artificial-au-row", "axis_name": "枚数(内容)", "option_values": values,
         "selected_value": values[0], "source_sku_key": "artificial-rak-row",
         "fixed_au_product_ref": {"product_id": "artificial-au"},
         "selected_au_row": {"row_key": "artificial-au-row", "axes": [{"axis_name": "色", "value": "青"}]},
         "source_request_id": window, "scope": "title" if kind == "plain_title" else "all_windows",
         "window": {"field_kind": kind, "quote": quote}}
    return [{"id": window + ":alternative:" + str(index), "premise": premise,
             "provenance": {**p, "candidate_option_index": index, "candidate_option_value": value}}
            for index, value in enumerate(values)]


def inferred(request, chosen_index=0, count=128):
    labels = request["schema_labels"]
    probabilities = {label: 0.025 for label in labels}
    probabilities[labels[chosen_index]] = 0.95
    value = request["schema_label_to_original_value"][labels[chosen_index]]
    raw = {request["unknown_label"] if original is None else original: probabilities[label]
           for label, original in request["schema_label_to_original_value"].items()}
    return {**deepcopy(request), "status": "ok", "chosen_schema_label": labels[chosen_index],
            "chosen_value": value, "chosen_label": value if value is not None else request["unknown_label"],
            "confidence": 0.95, "schema_probabilities": probabilities, "probabilities": raw,
            "actual_encoder_input_token_count": count}


def fixture(path, scope="title", count=128, chosen_index=0, skip=False):
    original = sources()
    requests = RUNNER.prepare(original, scope)
    record = inferred(requests[0], chosen_index, count)
    if skip:
        record.update(status="inference_too_long", chosen_value=None,
                      chosen_label=requests[0]["unknown_label"], confidence=None, probabilities=None)
        record.pop("schema_probabilities")
        record.pop("chosen_schema_label")
    write_rows(path / "source_requests.jsonl", original)
    write_rows(path / "requests.jsonl", requests)
    write_rows(path / "predictions.jsonl", [record])
    (path / "code").mkdir()
    (path / "code" / "artificial_fixture.py").write_text("# Explicitly artificial test fixture\n")
    freeze = {"labels_read": False, "production_eligible": False, "scope_proven": False,
              "input_scope": scope, "source_input_sha256": DIAG.sha(path / "source_requests.jsonl"),
              "requests_sha256": DIAG.sha(path / "requests.jsonl"), "source_request_count": 2,
              "request_count": 1, "schema_transport": {"reserved_tokens": list(DIAG.RESERVED)},
              "code_sha256": {"artificial_fixture.py": DIAG.sha(path / "code" / "artificial_fixture.py")},
              "preflight": {"max_tokens": 512, "max_len": None, "includes_schema": True,
                            "actual_encoder_input_token_counts": [count]}}
    write_json(path / "freeze.json", freeze)
    summary = {**freeze, "status_counts": {record["status"]: 1}, "inference_seconds": 0.0,
               "output_sha256": {name: DIAG.sha(path / name) for name in
                                 ("source_requests.jsonl", "requests.jsonl", "predictions.jsonl", "freeze.json")}}
    write_json(path / "summary.json", summary)
    return original, requests, record


def rehash(path):
    """Keep file integrity consistent to expose semantic binding failures."""
    freeze = json.loads((path / "freeze.json").read_text())
    freeze["requests_sha256"] = DIAG.sha(path / "requests.jsonl")
    freeze["source_input_sha256"] = DIAG.sha(path / "source_requests.jsonl")
    write_json(path / "freeze.json", freeze)
    summary = json.loads((path / "summary.json").read_text())
    summary.update(freeze)
    summary["output_sha256"] = {name: DIAG.sha(path / name) for name in
                               ("source_requests.jsonl", "requests.jsonl", "predictions.jsonl", "freeze.json")}
    write_json(path / "summary.json", summary)


class GlinerChoiceDiagnosticTests(unittest.TestCase):
    def test_full_composite_and_delimiters_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            _, requests, _ = fixture(Path(folder))
            _, records, receipt = DIAG.load_verified(Path(folder))
            rows = DIAG.aggregate(records, 0.9)
            self.assertEqual(rows[0]["relation"], "support")
            self.assertEqual(rows[0]["selected_value"], "21枚(パネル20＋ドア1)[P]")
            self.assertIn("（", requests[0]["schema_labels"][0])
            self.assertIn("［P］", requests[0]["schema_labels"][0])
            self.assertTrue(receipt["indexed_transport_mapping_verified"])
            self.assertFalse(rows[0]["production_eligible"])

    def test_quote_input_uses_and_preserves_original_whole_premise(self):
        original = sources("w0", "description")
        requests = RUNNER.prepare(original, "all_windows")
        for source in original:
            source["provenance"]["original_relation_request_id"] = source["id"]
            source["provenance"]["original_relation_premise"] = source["premise"]
            source["premise"] = source["provenance"]["window"]["quote"]
            source["id"] += ":quote_axis"
        request = requests[0]
        request.update(text_source="original_relation_premise", source_input_premise=original[0]["premise"],
                       provenance=deepcopy(original[0]["provenance"]),
                       source_request_ids=[source["id"] for source in original],
                       source_original_relation_request_ids=[source["provenance"]["original_relation_request_id"] for source in original])
        DIAG.verify_raw_bindings(original, requests, "all_windows", "original_relation_premise")
        request["text"] = original[0]["premise"]
        with self.assertRaisesRegex(ValueError, "whole raw schema"):
            DIAG.verify_raw_bindings(original, requests, "all_windows", "original_relation_premise")

    def test_index_prefix_keeps_escape_colliding_raw_values_distinct(self):
        original = sources()
        values = ["[P]", "［P］"]
        for index, source in enumerate(original):
            source["provenance"].update(option_values=values, selected_value=values[0],
                                         candidate_option_value=values[index])
        requests = RUNNER.prepare(original)
        DIAG.verify_raw_bindings(original, requests, "title")
        mapping = requests[0]["schema_label_to_original_value"]
        self.assertEqual(mapping["選択肢1：［P］"], "[P]")
        self.assertEqual(mapping["選択肢2：［P］"], "［P］")
        self.assertEqual(len(mapping), 3)

    def test_different_winner_never_proves_conflict(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(Path(folder), chosen_index=1)
            _, records, _ = DIAG.load_verified(Path(folder))
            row = DIAG.aggregate(records, 0.5)[0]
            self.assertEqual(row["relation"], "unknown")
            self.assertEqual(row["candidate_action"], "drop")

    def test_competing_windows_abstain_without_consuming_full_condition(self):
        original = sources("w0", "description") + sources("w1", "description")
        requests = RUNNER.prepare(original, "all_windows")
        records = [inferred(request, index) for index, request in enumerate(requests)]
        for record in records:
            record["diagnostic_inference_eligible"] = True
        rows = DIAG.aggregate(records, 0.9)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["relation"], "unknown")
        self.assertEqual(rows[0]["supported_whole_alternatives"], original[0]["provenance"]["option_values"])

    def test_all_skipped_condition_remains_in_denominator(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(Path(folder), count=513, skip=True)
            _, records, receipt = DIAG.load_verified(Path(folder))
            rows = DIAG.aggregate(records, 0.9)
            self.assertEqual((len(rows), rows[0]["eligible_window_count"], rows[0]["relation"]), (1, 0, "unknown"))
            self.assertTrue(receipt["strict_512_run_eligible"])

    def test_overlong_inferred_record_is_ineligible_diagnostic(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(Path(folder), count=513)
            _, records, receipt = DIAG.load_verified(Path(folder))
            self.assertFalse(receipt["strict_512_run_eligible"])
            self.assertEqual(DIAG.aggregate(records, 0.5)[0]["relation"], "unknown")

    def test_no_accepts_does_not_claim_precision(self):
        annotation = {("c", "r", "a", "v"): {"relation": "support"}}
        metric = DIAG.metrics([{"case_id": "c", "au_row_key": "r", "axis_name": "a", "selected_value": "v", "relation": "unknown"}], annotation)
        self.assertIsNone(metric["support_precision"])
        self.assertFalse(metric["support_precision_defined"])
        self.assertEqual(metric["support_recall"], 0.0)

    def test_legacy_actual_length_audit_without_dependency_pins_is_ineligible(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            fixture(path)
            freeze = json.loads((path / "freeze.json").read_text())
            freeze["preflight"].pop("actual_encoder_input_token_counts")
            freeze["loader_dependency_sha256"] = {}
            write_json(path / "freeze.json", freeze)
            write_json(path / "encoder-input-length-audit.json", {
                "requests_sha256": freeze["requests_sha256"], "labels_read": False,
                "additional_inference": False, "actual_encoder_token_counts": [128],
                "actual_encoder_max": 128, "actual_over_512_count": 0})
            rehash(path)
            _, records, receipt = DIAG.load_verified(path)
            self.assertFalse(receipt["actual_encoder_input_lengths_verified"])
            self.assertFalse(receipt["strict_512_run_eligible"])
            self.assertEqual(DIAG.aggregate(records, 0.5)[0]["relation"], "unknown")

    def test_corrupt_prediction_rejected_before_annotation_is_opened(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            fixture(path)
            (path / "predictions.jsonl").write_text("{}\n")
            with self.assertRaisesRegex(ValueError, "output SHA"):
                DIAG.score(path, [path / "missing-annotation.jsonl"], path / "score.json")

    def test_missing_alternative_rejected_even_when_member_hashes_match(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            original, _, _ = fixture(path)
            write_rows(path / "source_requests.jsonl", original[:1])
            freeze = json.loads((path / "freeze.json").read_text())
            freeze["source_request_count"] = 1
            write_json(path / "freeze.json", freeze)
            rehash(path)
            with self.assertRaisesRegex(ValueError, "lacks a whole alternative"):
                DIAG.load_verified(path)

    def test_partial_transport_mapping_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            _, requests, record = fixture(path)
            requests[0]["schema_label_to_original_value"][requests[0]["schema_labels"][0]] = "21枚"
            record.update(requests[0])
            write_rows(path / "requests.jsonl", requests)
            write_rows(path / "predictions.jsonl", [record])
            rehash(path)
            with self.assertRaisesRegex(ValueError, "whole raw schema"):
                DIAG.load_verified(path)

    def test_fixed_au_row_changed_after_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            _, requests, record = fixture(path)
            requests[0]["provenance"]["au_row_key"] = "different-row"
            record.update(deepcopy(requests[0]))
            write_rows(path / "requests.jsonl", requests)
            write_rows(path / "predictions.jsonl", [record])
            rehash(path)
            with self.assertRaisesRegex(ValueError, "whole raw schema"):
                DIAG.load_verified(path)

    def test_wrong_decoded_winner_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            _, _, record = fixture(path)
            record["chosen_value"] = "21枚"
            write_rows(path / "predictions.jsonl", [record])
            rehash(path)
            with self.assertRaisesRegex(ValueError, "whole original value"):
                DIAG.load_verified(path)

    def test_actual_record_length_cannot_differ_from_freeze(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            _, _, record = fixture(path)
            record["actual_encoder_input_token_count"] = 129
            write_rows(path / "predictions.jsonl", [record])
            rehash(path)
            with self.assertRaisesRegex(ValueError, "actual frozen encoder"):
                DIAG.load_verified(path)

    def test_frozen_code_sha_is_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            fixture(path)
            (path / "code" / "artificial_fixture.py").write_text("changed\n")
            with self.assertRaisesRegex(ValueError, "frozen code SHA"):
                DIAG.load_verified(path)

    def test_score_is_explicitly_retrospective_and_not_production(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            original, _, _ = fixture(path)
            p = original[0]["provenance"]
            annotation = {key: p[key] for key in ("case_id", "au_row_key", "axis_name", "selected_value")}
            annotation["relation"] = "support"
            write_rows(path / "annotations.jsonl", [annotation])
            result = DIAG.score(path, [path / "annotations.jsonl"], path / "score.json")
            self.assertTrue(result["retrospective_diagnostic_grid"])
            self.assertFalse(result["gliner_operating_threshold_preregistered"])
            self.assertFalse(result["scope_proven"])
            self.assertFalse(result["production_eligible"])
            self.assertEqual(result["diagnostics"]["0.9"]["metrics"]["true_support"], 1)
            with self.assertRaises(FileExistsError):
                DIAG.score(path, [path / "annotations.jsonl"], path / "score.json")


if __name__ == "__main__":
    unittest.main()
