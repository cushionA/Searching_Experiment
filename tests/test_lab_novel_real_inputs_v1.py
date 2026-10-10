import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments/sku-matching/prepare_novel_real_inputs_v1.py"
SPEC = importlib.util.spec_from_file_location("prepare_novel_real_inputs_v1", SCRIPT)
prep = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prep)


class NovelRealInputTests(unittest.TestCase):
    def test_builds_all_56_rows_without_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            manifest = prep.prepare(out)
            inputs = [json.loads(x) for x in (out / "inputs.jsonl").read_text().splitlines()]
            sources = [json.loads(x) for x in (out / "source-cases.jsonl").read_text().splitlines()]
        self.assertEqual(manifest["input_count"], 56)
        self.assertEqual(manifest["source_case_count"], 56)
        self.assertEqual(len(inputs), 56)
        self.assertEqual(len(sources), 56)
        self.assertEqual(manifest["rakuten_sku_count_total"], 56)
        self.assertEqual({x["family_id"] for x in inputs}, prep.SUPPORTED)
        self.assertEqual(len({x["case_id"] for x in inputs}), 56)
        for row in inputs:
            self.assertEqual(row["label_status"], "unlabeled_private_candidate_not_gold")
            self.assertNotIn("label", row)
            self.assertNotIn("gold", row)
            self.assertNotIn("answer", row)
            self.assertTrue(row["au"]["sku_rows"])
            self.assertTrue(row["rakuten"]["selected_sku"]["variant_id"])

    def test_exact_source_spans_and_series_links_are_scoped_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            prep.prepare(out)
            inputs = [json.loads(x) for x in (out / "inputs.jsonl").read_text().splitlines()]
            sources = [json.loads(x) for x in (out / "source-cases.jsonl").read_text().splitlines()]
        for inp in inputs:
            for ev in inp["evidence_registry"]:
                self.assertNotRegex(ev["scope"], re.compile(r"series|sibling|link", re.I))
                self.assertTrue(ev["quote"])
                ref = ev["source_ref"]
                path = ROOT / ref["raw_file"]
                self.assertEqual(prep.sha256(path.read_bytes()), ref["raw_sha256"])
                if "html_char_start" in ref:
                    data = path.read_bytes()
                    decoded, _ = prep.decode_html(data)
                    self.assertEqual(decoded[ref["html_char_start"]:ref["html_char_end"]], ev["quote"])
                elif "value_char_start" in ref:
                    self.assertGreaterEqual(ref["value_char_start"], 0)
                    self.assertGreater(ref["value_char_end"], ref["value_char_start"])
                else:
                    self.assertIn("raw_char_start", ref)
                    self.assertGreater(ref["raw_char_end"], ref["raw_char_start"])
            self.assertNotIn("related_link_scope", inp)
        for source in sources:
            self.assertEqual(source["label_status"], "unlabeled_private_candidate_not_gold")
            self.assertIn("rakuten_related_link_scope_audit_only", source)
            self.assertIn("au_related_link_scope_audit_only", source)
            self.assertIn("series_scope_audit_only", source["rakuten_selected_sku_attributes"])

    def test_source_arrays_and_counts_are_complete(self):
        expected = {"HG020": 35, "fca2160": 6, "mbc005": 6, "qaa0100": 9}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            prep.prepare(out)
            inputs = [json.loads(x) for x in (out / "inputs.jsonl").read_text().splitlines()]
        for manage, count in expected.items():
            group = [x for x in inputs if manage.lower() in x["family_id"].lower()]
            self.assertEqual(len(group), count)
            first_au = group[0]["au"]["original_full_sku_array"]
            self.assertTrue(first_au["rowNames"])
            self.assertTrue(first_au["stockList"])
            self.assertEqual(group[0]["au"]["sku_rows"], group[-1]["au"]["sku_rows"])
            self.assertTrue(all(x["rakuten"]["selected_sku"]["source_sku_key"] for x in group))
            self.assertTrue(all(x["rakuten"]["selected_sku"]["price_jpy"] is not None for x in group))


if __name__ == "__main__":
    unittest.main()
