import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "sku-matching"))
import prepare_generic_residual_evidence_v1 as exporter


class GenericResidualEvidenceTests(unittest.TestCase):
    def fixture(self, directory):
        source = {"raw_file": "au-api.json", "sha256": "frozen-source-hash",
                  "locator": "$.itemInfo.itemComment"}
        descriptions = [
            {"text": "購入前にご確認ください。返品はお受けできません。", "text_kind": "derived_visible_html_text",
             "source_refs": [source], "source_fields": ["$.itemInfo.itemComment"]},
            {"text": "この選択肢には付属品が含まれます。" + "隣接する注意書きです。" * 40,
             "text_kind": "derived_visible_html_text", "source_refs": [source]},
            {"text": "別の選択肢には付属品が含まれません。対象は幅150cmのみです。",
             "text_kind": "derived_visible_html_text", "source_refs": [source]},
        ]
        products = [
            {"dossier_id": "d1", "au": {"product_id": "100", "title": "固定AUタイトル" * 50,
             "title_source": {"quote": "固定AUタイトル" * 50, "raw_file": "au-api.json"},
             "axis_names": {"row": "色", "column": "寸法と数量"}, "descriptions": descriptions,
             "rows": [{"row_key": "au:100:0", "axes": [{"axis_name": "寸法と数量", "value": "幅100×丈200cm(4枚組)"}]},
                      {"row_key": "au:100:1", "axes": [{"axis_name": "寸法と数量", "value": "幅150×丈200cm(2枚組)"}]}],
             "purchase_options": [{"text": "確認事項", "source_ref": {"quote": "確認事項"}}],
             "iframe_urls": ["https://example.invalid/description"]}},
            {"dossier_id": "d2", "au": {"product_id": "200", "title": "別の固定商品", "descriptions": [],
             "rows": [{"row_key": "au:200:0", "axes": []}]}}
        ]
        cases = [{"case_id": "c1", "dossier_id": "d1", "au_product_id": "100",
                  "rakuten": {"source_sku_key": "rk-fixed", "axes": [{"axis_label": "寸法と数量", "value": "幅100×丈200cm(4枚組)"}]}},
                 {"case_id": "c2", "dossier_id": "d2", "au_product_id": "200", "rakuten": {}}]
        cards = [{"case_id": "c1", "au_row_key": "au:100:0", "condition_id": "size-and-count",
                  "axis_name": "寸法と数量", "selected_value": "幅100×丈200cm(4枚組)",
                  "option_values": ["幅100×丈200cm(4枚組)", "幅150×丈200cm(2枚組)", "", "幅100×丈200cm(4枚組)"],
                  "development_origin": "unit-fixture", "legacy_prediction_line": 17,
                  "raw_condition": {"value": "幅100×丈200cm(4枚組)", "scope": "選択されたSKU"},
                  "source_refs": [{"raw_file": "rakuten.html", "locator": "selector"}]},
                 {"case_id": "c1", "au_row_key": "au:100:1", "condition_id": "plain-color",
                  "axis_name": "色", "selected_value": "青", "option_values": ["青", "赤"],
                  "condition_text": "選択色は青。"},
                 {"case_id": "c2", "au_row_key": "au:200:0", "condition_id": "anything",
                  "axis_name": "任意", "selected_value": "なし", "option_values": ["あり", "なし"]}]
        for name, rows in (("cases.jsonl", cases), ("products.jsonl", products), ("cards.jsonl", cards)):
            exporter.write_jsonl(directory / name, rows)
        (directory / "manifest.json").write_text(json.dumps({"source_raw_sha256": {"au-api.json": "frozen-source-hash"}}), encoding="utf-8")
        return cases, products, cards

    def test_all_conditions_keep_opaque_values_full_candidate_arrays_and_fixed_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _, products, cards = self.fixture(directory)
            manifest = exporter.prepare(directory, directory / "cards.jsonl", directory / "out", 70, 20)
            documents = exporter.read_jsonl(directory / "out/documents.jsonl")
            tasks = exporter.read_jsonl(directory / "out/tasks.jsonl")
            self.assertEqual(manifest["task_count"], len(cards))
            self.assertEqual(documents[0]["au"], products[0]["au"])
            self.assertEqual(tasks[0]["selected_value"], cards[0]["selected_value"])
            self.assertEqual(tasks[0]["option_values"], cards[0]["option_values"])
            self.assertEqual(tasks[0]["raw_condition"], cards[0]["raw_condition"])
            self.assertEqual(tasks[0]["source_refs"], cards[0]["source_refs"])
            self.assertEqual(tasks[0]["residual_card"], cards[0])
            self.assertEqual(tasks[0]["direction"], "rakuten_to_au")
            self.assertEqual(tasks[0]["source_sku_key"], "rk-fixed")
            self.assertEqual(tasks[0]["condition_text"], "寸法と数量：幅100×丈200cm(4枚組)")
            self.assertEqual(manifest["source_raw_sha256"], {"au-api.json": "frozen-source-hash"})
            self.assertEqual(tasks[0]["selected_au_row"], products[0]["au"]["rows"][0])
            self.assertEqual(tasks[1]["axis_name"], "色")
            self.assertEqual(tasks[1]["condition_text"], "選択色は青。")
            for task in tasks:
                doc = next(document for document in documents if document["document_id"] == task["fixed_au_product_ref"]["document_id"])
                expected = [window["window_id"] for field in ("title_document", "description_document") for window in doc[field]["windows"]]
                self.assertEqual(task["window_refs"], expected)
                self.assertEqual(task["au_product_id"], doc["au_product_id"])
                self.assertTrue(task["all_windows_required"])
                self.assertTrue(task["applicability_required"])
            self.assertFalse(any("fixed-au:d2:" in ref for ref in tasks[0]["window_refs"]))

    def test_complete_windows_preserve_warnings_both_signed_claims_and_long_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _, products, _ = self.fixture(directory)
            exporter.prepare(directory, directory / "cards.jsonl", directory / "out", 70, 20)
            document = exporter.read_jsonl(directory / "out/documents.jsonl")[0]
            block = document["description_document"]
            expected = "\n".join(record["text"] for record in products[0]["au"]["descriptions"])
            self.assertEqual(block["text"], expected)
            covered = set()
            for index, window in enumerate(block["windows"]):
                start, end = window["derived_text_start"], window["derived_text_end"]
                self.assertEqual(window["text"], expected[start:end])
                self.assertEqual(window["offset_space"], block["document_id"])
                self.assertNotIn("raw_start", window)
                covered.update(range(start, end))
                if index:
                    self.assertEqual(window["overlap_with_previous_chars"], 20)
            self.assertEqual(covered, set(range(len(expected))))
            texts = [window["text"] for window in block["windows"]]
            self.assertTrue(any("返品はお受けできません。" in text for text in texts))
            self.assertTrue(any("付属品が含まれます。" in text for text in texts))
            self.assertTrue(any("付属品が含まれません。" in text for text in texts))
            self.assertTrue(any("対象は幅150cmのみです。" in text for text in texts))
            title = document["title_document"]
            self.assertEqual(title["text"], products[0]["au"]["title"])
            self.assertGreater(len(title["text"]), 70)
            title_coverage = set()
            for window in title["windows"]:
                self.assertLessEqual(len(window["text"]), 70)
                title_coverage.update(range(window["derived_text_start"], window["derived_text_end"]))
            self.assertEqual(title_coverage, set(range(len(title["text"]))))
            for segment in block["segments"]:
                self.assertEqual(expected[segment["derived_text_start"]:segment["derived_text_end"]],
                                 products[0]["au"]["descriptions"][segment["description_index"]]["text"])
                self.assertNotIn("start", segment["source_refs"][0])

    def test_bad_fixed_product_cross_product_rows_and_duplicates_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases, products, cards = self.fixture(Path(tmp))
            bad_cases = copy.deepcopy(cases)
            bad_cases[0]["au_product_id"] = "200"
            with self.assertRaises(ValueError):
                exporter.validate(bad_cases, products, cards)
            bad_cards = copy.deepcopy(cards)
            bad_cards[0]["au_row_key"] = "au:200:0"
            with self.assertRaises(ValueError):
                exporter.validate(cases, products, bad_cards)
            with self.assertRaises(ValueError):
                exporter.validate(cases, products, cards + [cards[0]])
            with self.assertRaises(ValueError):
                exporter.validate(cases + [cases[0]], products, cards)
            bad_cards = copy.deepcopy(cards)
            bad_cards[0]["source_sku_key"] = "wrong-source-SKU"
            with self.assertRaises(ValueError):
                exporter.validate(cases, products, bad_cards)
            reverse_cards = copy.deepcopy(cards)
            reverse_cards[0]["direction"] = "au_to_rakuten"
            with self.assertRaisesRegex(ValueError, "requires rakuten_to_au"):
                exporter.validate(cases, products, reverse_cards)
            across_rows = copy.deepcopy(cards)
            across_rows[1]["condition_id"] = across_rows[0]["condition_id"]
            exporter.validate(cases, products, across_rows)

    def test_count_only_and_explicit_card_limit_do_not_filter_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.fixture(directory)
            output = directory / "out"
            counts = exporter.prepare(directory, directory / "cards.jsonl", output, 70, 20, count_only=True)
            self.assertFalse(output.exists())
            limited = exporter.prepare(directory, directory / "cards.jsonl", output, 70, 20, max_cards=1)
            self.assertEqual(counts["input_card_count"], 3)
            self.assertEqual(limited["input_card_count"], 3)
            self.assertEqual(limited["task_count"], 1)
            self.assertGreater(limited["condition_window_pair_count"], 5)
            with self.assertRaises(FileExistsError):
                exporter.prepare(directory, directory / "cards.jsonl", output)
            with self.assertRaises(ValueError):
                exporter.make_windows("text", "test", 70, 70)


if __name__ == "__main__":
    unittest.main()
