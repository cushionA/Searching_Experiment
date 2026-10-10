"""Structured rendering probes retain evidence and do not inject the target."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "experiments/sku-matching" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROBE = load("structured_probe", "prepare_structured_choice_probe_v1.py")
SCOPE = load("scope_packet", "prepare_generic_html_scope_packets_v1.py")


class StructuredChoiceProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input, self.packet, self.output = (self.root / name for name in ("input", "packet", "output"))
        self.input.mkdir()
        self.packet.mkdir()
        self.title = "検証商品 レースなし"
        self.raw = '<h2>他商品案内</h2><table><tr><td>区分</td><td><a href="/item/999">別商品&amp;選択' + "甲" * 80 + '</a></td></tr></table><p>現在の仕様はレースなし</p>'
        ref = {"document_id": "fixed-au:pair:101", "dossier_id": "pair", "product_id": "101"}
        row = {"row_key": "au:101:1:0:0", "sku_id": 1, "axes": [
            {"axis_name": "色と素材", "value": "ブルー（パイル）"}, {"axis_name": "サイズ", "value": "100×80cm(4枚組)"}]}
        self.document = {"document_id": ref["document_id"], "dossier_id": "pair", "au_product_id": "101",
                         "au": {"product_id": "101", "title": self.title, "rows": [row]}}
        self.task = {"task_id": "condition:0", "case_id": "case-1", "condition_id": "whole-axis:2",
                     "source_sku_key": "rakuten:sku1", "au_row_key": row["row_key"], "axis_name": "構成",
                     "selected_value": "100×80cm / ブルー / レースあり", "option_values": [
                         "100×80cm / ブルー / レースあり", "100×80cm / ブルー / レースなし"],
                     "dossier_id": "pair", "au_product_id": "101", "fixed_au_product_ref": ref,
                     "selected_au_row": deepcopy(row), "direction": "rakuten_to_au"}
        fields = [self.field("title", self.title, "plain_title"), self.field("body", self.raw), self.field("body-copy", self.raw)]
        self.packet_data = {"document_id": ref["document_id"], "dossier_id": "pair", "au_product_id": "101",
                            "fixed_au_product_ref": deepcopy(ref), "fields": fields}
        self.freeze()

    def field(self, identity, raw, kind="description_source_field"):
        ref = {"raw_file": "real-au.json", "sha256": "source-sha", "locator": {"kind": "json_leaf", "json_path": "$." + identity}}
        parser = SCOPE.ScopeParser(raw, identity, ref)
        if kind == "plain_title":
            parser.token("data", 0, len(raw), raw)
            parser.close_block(len(raw), "plain_json_leaf")
            result = {"raw_html": raw, "text": raw, "tokens": parser.tokens, "blocks": parser.blocks,
                      "coverage": {"raw_coverage_complete": True, "source_field_char_count": len(raw)}}
        else:
            result = parser.finish()
        return {"field_id": identity, "field_kind": kind, "source_ref": ref, **result}

    def freeze(self):
        for path, rows in ((self.input / "tasks.jsonl", [self.task]), (self.input / "documents.jsonl", [self.document]),
                           (self.packet / "packets.jsonl", [self.packet_data])):
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    def run_probe(self, **kwargs):
        manifest = PROBE.prepare(self.input, self.packet, self.output, **kwargs)
        return manifest, PROBE.read_jsonl(self.output / "requests.jsonl")

    def test_full_choices_are_raw_and_desired_value_is_not_in_state(self):
        manifest, requests = self.run_probe(char_budget=40, overlap=10)
        expected = [{"label": f"option:{i}", "description": value} for i, value in enumerate(self.task["option_values"])]
        expected.append({"label": "unknown", "description": PROBE.UNKNOWN})
        for request in requests:
            self.assertEqual(request["choices"], expected)
            self.assertNotIn(self.task["selected_value"], request["question"])
            self.assertNotIn(self.task["selected_value"], request["state"])
            self.assertIn("ブルー（パイル）", request["state"])
            self.assertIn("100×80cm(4枚組)", request["state"])
            self.assertEqual(request["provenance"]["selected_value"], self.task["selected_value"])
            self.assertEqual(request["provenance"]["au_row_key"], self.task["au_row_key"])
        self.assertTrue(manifest["scope_not_proven"])
        self.assertFalse(manifest["production"])
        self.assertFalse(manifest["labels_read"])

    def test_all_visible_chars_and_token_mapping_preserved(self):
        manifest, requests = self.run_probe(char_budget=40, overlap=10)
        for field in self.packet_data["fields"]:
            windows = [r["provenance"]["window"] for r in requests if r["provenance"]["arm"] == "natural_flat" and r["provenance"]["window"]["field_id"] == field["field_id"]]
            coverage = [False] * len(field["text"])
            tokens = {t["token_id"]: t for t in field["tokens"]}
            for window in windows:
                start, end = window["visible_char_start"], window["visible_char_end"]
                self.assertEqual(window["quote"], field["text"][start:end])
                coverage[start:end] = [True] * (end - start)
                self.assertEqual("".join(r["text"] for r in window["token_refs"]), window["quote"])
                for ref in window["token_refs"]:
                    token = tokens[ref["token_id"]]
                    self.assertEqual(ref["text"], token["text"][ref["decoded_token_start"]:ref["decoded_token_end"]])
                    span = ref["source_html_span"]
                    self.assertEqual(span["raw_html"], field["raw_html"][span["start"]:span["end"]])
            self.assertTrue(all(coverage))
            if field["field_kind"] == "plain_title":
                self.assertEqual(len(windows), 1)
        self.assertTrue(manifest["all_visible_field_characters_covered"])
        self.assertEqual(len(manifest["field_coverage"]), 3)

    def test_structure_retains_link_heading_and_whole_table_row(self):
        _, requests = self.run_probe(char_budget=40, overlap=10)
        structured = [r for r in requests if r["provenance"]["arm"] == "natural_structure"]
        row_text = next(b["text"] for b in self.packet_data["fields"][1]["blocks"] if b["role"] == "table_row")
        with_link = [r for r in structured if "親リンク先：/item/999" in r["state"]]
        self.assertTrue(with_link)
        self.assertTrue(all("直前見出し：他商品案内" in r["state"] for r in with_link))
        self.assertTrue(all("表の行：" + row_text in r["state"] for r in with_link))
        for request in with_link:
            flat = next(r for r in requests if r["provenance"]["arm"] == "natural_flat" and r["provenance"]["window_index"] == request["provenance"]["window_index"])
            self.assertNotIn("親リンク先：", flat["state"])
            self.assertEqual(flat["provenance"]["window"], request["provenance"]["window"])
            self.assertEqual(flat["choices"], request["choices"])

    def test_duplicate_description_fields_not_deduplicated(self):
        _, requests = self.run_probe(char_budget=40, overlap=10)
        bodies = [[r for r in requests if r["provenance"]["arm"] == "natural_flat" and r["provenance"]["window"]["field_id"] == field_id] for field_id in ("body", "body-copy")]
        self.assertEqual(len(bodies[0]), len(bodies[1]))
        self.assertTrue(bodies[0])
        self.assertEqual([r["state"] for r in bodies[0]], [r["state"] for r in bodies[1]])
        self.assertNotEqual(bodies[0][0]["id"], bodies[1][0]["id"])

    def test_fixed_identity_and_full_row_mismatches_rejected(self):
        for target, key, bad in ((self.task, "au_product_id", "999"),
                                 (self.task["fixed_au_product_ref"], "product_id", "999"),
                                 (self.task["selected_au_row"]["axes"][0], "value", "改変"),
                                 (self.packet_data, "au_product_id", "999")):
            with self.subTest(key=key):
                previous = target[key]
                target[key] = bad
                self.freeze()
                with self.assertRaises(ValueError):
                    PROBE.prepare(self.input, self.packet, self.output)
                self.assertFalse(self.output.exists())
                target[key] = previous

    def test_packet_token_text_and_raw_spans_must_match(self):
        field = self.packet_data["fields"][1]
        field["tokens"][0]["source_html_span"]["raw_html"] = "改変"
        self.freeze()
        with self.assertRaisesRegex(ValueError, "raw span"):
            self.run_probe()

    def test_overwrite_refused_without_touching_existing_output(self):
        self.run_probe()
        before = (self.output / "requests.jsonl").read_bytes()
        with self.assertRaises(FileExistsError):
            self.run_probe()
        self.assertEqual(before, (self.output / "requests.jsonl").read_bytes())

    def test_window_parameters_reject_gaps_or_infinite_stride(self):
        for budget, overlap in ((0, 0), (20, 20), (20, -1)):
            with self.subTest(budget=budget, overlap=overlap), self.assertRaises(ValueError):
                self.run_probe(char_budget=budget, overlap=overlap)


if __name__ == "__main__":
    unittest.main()
