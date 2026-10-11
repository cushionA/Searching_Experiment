"""Synthetic CPU-free contracts for the integrated description runner."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKU_DIR = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(SKU_DIR))
SPEC = importlib.util.spec_from_file_location(
    "integrated_description_models",
    SKU_DIR / "trial_integrated_description_models_v1.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def request(selected="red"):
    axes = [{"axis_name": "色", "value": "赤"}, {"axis_name": "容量", "value": "500 ml"}]
    return {
        "direction": "selected-value-check", "axis_name": "色", "option_values": ["red", "blue"],
        "selected_value": selected, "target_document_id": "doc-1",
        "target_document_sha256": "a" * 64, "target_selected_axes": axes,
        "target_selected_axes_text": "色：赤\n容量：500 ml",
    }


def document(text="赤色のボトル。容量は500 ml。"):
    return {"document_id": "doc-1", "document_sha256": "a" * 64,
            "title": {"text": "テスト商品"}, "description": {"text": text}}


def record(value="red", confidence=.8, status="ok", refs=None):
    return {"status": status, "chosen_value": value, "confidence": confidence,
            "quote_refs": [] if refs is None else refs}


class IntegratedDescriptionModelContracts(unittest.TestCase):
    def test_payload_keeps_every_target_selected_axis_in_its_rendering(self):
        payload = RUNNER.model_payload(request())
        self.assertEqual(payload["target_selected_axes_text"], "色：赤\n容量：500 ml")

    def test_payload_keeps_whole_option_values_and_order(self):
        payload = RUNNER.model_payload(request())
        self.assertEqual(payload["option_values"], ["red", "blue"])

    def test_desired_selected_value_is_excluded_from_payload(self):
        self.assertEqual(RUNNER.model_payload(request("red")), RUNNER.model_payload(request("blue")))

    def test_selected_value_must_belong_to_whole_options(self):
        with self.assertRaisesRegex(ValueError, "whole options"):
            RUNNER.model_payload(request("green"))

    def test_render_must_match_all_selected_axes(self):
        data = request()
        data["target_selected_axes_text"] = "色：赤"
        with self.assertRaisesRegex(ValueError, "rendering differs"):
            RUNNER.model_payload(data)

    def test_options_must_be_unique_and_nonempty_strings(self):
        for options in (["red", "red"], ["red", ""], ["red", 3]):
            with self.subTest(options=options):
                data = request()
                data["option_values"] = options
                with self.assertRaisesRegex(ValueError, "whole options"):
                    RUNNER.model_payload(data)

    def test_request_text_uses_target_axes_title_and_quote(self):
        payload = RUNNER.model_payload(request())
        built = RUNNER.request_for(payload, document(), "引用する説明")
        self.assertIn("商品名：テスト商品", built["text"])
        self.assertIn("色：赤\n容量：500 ml", built["text"])
        self.assertIn("引用する説明", built["text"])
        self.assertNotIn("red", built["text"])

    def test_schema_maps_every_original_option_as_a_whole_value(self):
        payload = RUNNER.model_payload(request())
        built = RUNNER.request_for(payload, document())
        mapping = built["schema_label_to_original_value"]
        self.assertEqual(set(mapping.values()), {"red", "blue", None})

    def test_quote_ref_uses_exact_unicode_character_offsets(self):
        doc = document("A赤い瓶Z")
        ref = RUNNER.quote_ref(doc, "description", doc["description"]["text"], 1, 4)
        self.assertEqual(ref, {"document_id": "doc-1", "field": "description",
                               "start": 1, "end": 4, "quote": "赤い瓶"})

    def test_quote_ref_defaults_to_full_text(self):
        text = "商品説明🙂"
        self.assertEqual(RUNNER.quote_ref(document(text), "title", text)["quote"], text)

    def test_windows_cover_complete_description_and_offsets_match_quotes(self):
        text = "0123456789" * 8 + "最後🙂"
        windows = RUNNER.windows_for(document(text), char_budget=17, overlap=5)
        self.assertEqual("".join(w["text"] if i == 0 else w["text"][5:]
                                  for i, w in enumerate(windows)), text)
        for window in windows:
            ref = window["quote_ref"]
            self.assertEqual(ref["quote"], text[ref["start"]:ref["end"]])
            self.assertEqual(ref["quote"], window["text"])

    def test_windows_preserve_adjacent_character_boundaries(self):
        text = "abcdefghij"
        windows = RUNNER.windows_for(document(text), char_budget=4, overlap=1)
        self.assertEqual([(w["quote_ref"]["start"], w["quote_ref"]["end"])
                          for w in windows], [(0, 4), (3, 7), (6, 10)])

    def test_no_records_is_unknown(self):
        self.assertEqual(RUNNER.aggregate([], "red", .5), ("unknown", None, []))

    def test_unknown_model_choice_does_not_create_conflict(self):
        self.assertEqual(RUNNER.aggregate([record(None)], "red", .5), ("unknown", None, []))

    def test_alternate_winner_is_unknown_not_conflict(self):
        self.assertEqual(RUNNER.aggregate([record("blue")], "red", .5), ("unknown", "blue", []))

    def test_multiple_high_confidence_winners_are_unknown(self):
        state, winner, refs = RUNNER.aggregate([record("red", .9), record("blue", .95)], "red", .5)
        self.assertEqual((state, winner, refs), ("unknown", None, []))

    def test_only_ok_whole_selected_value_at_threshold_supports(self):
        ref = {"document_id": "doc-1", "field": "description", "start": 0, "end": 2, "quote": "赤色"}
        result = RUNNER.aggregate([record("red", .5, refs=[ref])], "red", .5)
        self.assertEqual(result, ("support", "red", [ref]))

    def test_low_confidence_and_non_ok_records_do_not_support(self):
        for row in (record("red", .49), record("red", .99, status="input_too_long")):
            with self.subTest(row=row):
                self.assertEqual(RUNNER.aggregate([row], "red", .5), ("unknown", None, []))

    def test_repeated_identical_quotes_are_deduplicated(self):
        ref = {"document_id": "doc-1", "field": "title", "start": 0, "end": 2, "quote": "商品"}
        state, winner, refs = RUNNER.aggregate([record("red", .8, refs=[ref, dict(ref)])], "red", .5)
        self.assertEqual((state, winner, refs), ("support", "red", [ref]))


if __name__ == "__main__":
    unittest.main()
