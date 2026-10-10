from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import trial_nonllm_condition_extraction_v3 as trial  # noqa: E402


class NonLLMSpanTrialV3Tests(unittest.TestCase):
    def test_native_au_axes_join_to_verified_source_jsonpaths(self):
        full = {
            "rowNames": ["あり"], "columnNames": ["白"],
            "optionName": {"row": "天板", "column": "色"},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_path = Path(temp_dir) / "source.json"
            raw_path.write_text(json.dumps({"itemInfo": {"skuInfo": full}}, ensure_ascii=False), encoding="utf-8")
            digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            source_ref = {
                "raw_file": os.path.relpath(raw_path, ROOT), "raw_sha256": digest,
                "json_path": "$.itemInfo.skuInfo",
            }
            row = {
                "case_id": "synthetic-test-only",
                "au": {
                    "original_full_sku_array": full,
                    "original_full_sku_array_source": source_ref,
                    "sku_rows": [{"row_key": "au:123:456:0:0"}],
                },
                "rakuten": {},
            }
            axes = trial._native_au_axes(row, {})

        self.assertEqual([(a["axis_name_raw"], a["axis_value_raw"]) for a in axes], [("天板", "あり"), ("色", "白")])
        self.assertTrue(all(a["binding"]["source_join_verified"] for a in axes))
        self.assertEqual(axes[0]["binding"]["axis_value_source_ref"]["json_path"], "$.itemInfo.skuInfo.rowNames[0]")

    def test_same_presence_value_stays_bound_to_its_axis(self):
        packet = {
            "packet_id": "p1", "case_id": "c1", "au_row_key": "au:x",
            "context": {"rakuten_selected_axes": [
                {"axis_name_raw": "天板", "selected_value_raw": "あり"},
                {"axis_name_raw": "レースカーテン", "selected_value_raw": "あり"},
            ]},
            "evidence": [],
        }
        units = trial.collect_units([], [packet])
        composites = [u for u in units if u["unit_kind"] == "axis_observation"]
        raw = [u for u in units if u["unit_kind"] == "raw_status_value"]
        self.assertEqual({u["text"] for u in composites}, {"天板：あり", "レースカーテン：あり"})
        self.assertEqual(len(raw), 2)
        self.assertEqual([u["bindings"][0]["axis_name_raw"] for u in raw], ["天板", "レースカーテン"])

    def test_span_crossing_axis_label_value_boundary_is_unresolved(self):
        text = "天板：あり"
        segments = [
            {"segment_type": "axis_label", "start": 0, "end": 2, "text": "天板"},
            {"segment_type": "axis_value", "start": 3, "end": 5, "text": "あり"},
        ]
        spans = [
            {"text": "天板", "start": 0, "end": 2},
            {"text": "あり", "start": 3, "end": 5},
            {"text": "板：あ", "start": 1, "end": 4},
        ]
        mapped = trial.map_spans_to_segments(text, spans, segments)
        self.assertEqual([x["segment_mapping"] for x in mapped], ["mapped", "mapped", "unresolved_cross_segment"])
        self.assertIsNone(mapped[2]["segment_type"])

    def test_rakuten_key0_joins_to_raw_variant_selector_label(self):
        raw = '{"variantSelectors":[{"key":"Key0","label":"天板","values":[{"value":"あり","label":"あり"}]}]}'
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "page.html"
            path.write_text(raw, encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            ref = {
                "raw_file": os.path.relpath(path, ROOT), "raw_sha256": digest,
                "encoding": "utf-8", "embedded_json_path": "$.embedded_sku[0].selectorValues",
            }
            selected = {
                "selector_values_raw": ["あり"],
                "selected_sku_exact_property_spans": [{
                    "scope": "selected_sku_original_selector_values", "quote": '["あり"]',
                    "source_ref": ref,
                }],
            }
            label, binding = trial._rakuten_selector_axis_label(
                {"axis_key": "Key0", "value": "あり"}, 0, selected, {})
        self.assertEqual(label, "天板")
        label_ref = binding["axis_label_source_ref"]
        self.assertEqual(label_ref["quote"], '"label":"天板"')
        self.assertEqual(raw[label_ref["html_char_start"]:label_ref["html_char_end"]], label_ref["quote"])


if __name__ == "__main__":
    unittest.main()
