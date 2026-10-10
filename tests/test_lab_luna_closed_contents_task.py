from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "sku-matching"
sys.path.insert(0, str(EXPERIMENT))
import sku_luna_closed_contents_task as closed
import sku_luna_inputs as inputs

INPUT_DIR = EXPERIMENT / "results" / "20261010T185500Z-luna-multirow62" / "inference"
P1 = "case-2a0d9005474c4fa52557"
P2 = "case-e1f588b9dd6b8903515e"


class ClosedContentsTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads((INPUT_DIR / "inputs.json").read_text(encoding="utf-8"))
        cls.contexts = json.loads((INPUT_DIR / "source-contexts.json").read_text(encoding="utf-8"))
        cls.case_by_id = {case["case_id"]: case for case in cls.cases}
        verified, hashes = inputs.load_cases(inputs.DEFAULT_ZIP, [P1, P2])
        cls.verified_by_id = {case["case_id"]: case for case in verified}
        cls.source_hashes = hashes

    def request_for(self, case_id):
        case = self.case_by_id[case_id]
        selected = next(c["value"] for c in case["rakuten_conditions"] if c["axis"] == "レースカーテン")
        return closed.facet_request(case, self.contexts[case_id], selected)

    @staticmethod
    def payload(request):
        text = request["body"]["contents"][0]["parts"][0]["text"]
        return text, json.loads(text.split("INPUT=", 1)[1])

    def test_p2_contents_cell_is_bounded_by_html_row_and_next_section(self):
        request = self.request_for(P2)
        text, payload = self.payload(request)
        content = payload["sources"]["S30"]
        raw = content["bounded_html_row"]
        self.assertEqual(content["scope"], "fixed_product")
        self.assertNotIn("field_path", content)
        self.assertNotIn("raw_file_sha256", content)
        local = closed._raw_contents_block(self.case_by_id[P2], self.contexts[P2])
        self.assertEqual(local["field_path"], "$.itemInfo.extraItemComment")
        self.assertEqual(local["raw_file_sha256"],
                         "b1956598c5f3191e3c3d39e200766031baf589321d570e36ceeaf6e8f349a716")
        self.assertIn("<b>内容</b>", raw)
        self.assertTrue(raw.rstrip().endswith("</tr>"))
        self.assertNotIn("<b>カラー</b>", raw)
        self.assertEqual(content["next_section_label"], "カラー")
        self.assertIn("【幅100cm】", raw)
        self.assertIn("遮光カーテン 2枚", raw)
        self.assertIn("タッセル 2枚", raw)
        self.assertIn("【幅150cm】", raw)
        self.assertIn("遮光カーテン 1枚", raw)
        self.assertIn("タッセル 1枚", raw)
        self.assertNotIn("ミラーレースカーテン", raw)
        self.assertNotIn("product_id", payload)
        self.assertNotIn("case_id", payload)
        self.assertIn("閉じた一覧かどうか", text)
        self.assertIn("単に「レース」の語が無いだけで「なし」と決めない", text)
        self.assertIn("表が完全な一覧か曖昧ならunknown", text)

    def test_p1_same_bounded_section_explicitly_lists_lace_by_width(self):
        request = self.request_for(P1)
        _, payload = self.payload(request)
        raw = payload["sources"]["S30"]["bounded_html_row"]
        self.assertIn("【幅100cm】", raw)
        self.assertIn("遮光カーテン 2枚", raw)
        self.assertIn("ミラーレースカーテン 2枚", raw)
        self.assertIn("【幅150cm】", raw)
        self.assertIn("遮光カーテン 1枚", raw)
        self.assertIn("ミラーレースカーテン 1枚", raw)

    def test_every_row_quantity_matches_width_branch_without_title_override(self):
        for case_id, expected in ((P1, {"100": "4枚組", "150": "2枚組"}),
                                  (P2, {"100": "2枚", "150": "1枚"})):
            case = self.case_by_id[case_id]
            request = self.request_for(case_id)
            _, payload = self.payload(request)
            rows = payload["au_rows"]
            self.assertEqual(len(rows), 27)
            observed = {"100": set(), "150": set()}
            counts = {"100": 0, "150": 0}
            for row in rows:
                size = row["conditions"][1]["value"]
                width = "100" if "幅100" in size else "150"
                observed[width].add(size[size.rfind("(") + 1:size.rfind(")")])
                counts[width] += 1
            self.assertEqual(observed, {width: {quantity} for width, quantity in expected.items()})
            self.assertEqual(counts, {"100": 18, "150": 9})
            title = payload["sources"]["S0"]["text"]
            self.assertIn("セット", title)
            # Width-specific AU row values and content table are separate inputs.
            self.assertIn("A1", rows[0]["conditions"][1]["source_id"])

    def test_keyword_source_is_excluded_and_only_fixed_sources_are_citable(self):
        request = self.request_for(P2)
        _, payload = self.payload(request)
        self.assertNotIn("S2", payload["sources"])
        self.assertTrue(all(source["scope"] == "fixed_product" for source in payload["sources"].values()))
        schema = request["body"]["generationConfig"]["responseSchema"]["properties"]["checks"]
        self.assertEqual((schema["minItems"], schema["maxItems"]), (27, 27))
        self.assertEqual(set(schema["items"]["properties"]), {"status", "source_ids"})

    def test_validator_requires_source_citations_and_preserves_unknown(self):
        case = self.case_by_id[P2]
        ctx = self.contexts[P2]
        self.assertEqual(closed.validate_facet_response(case, ctx,
                         {"checks": [{"status": "unknown", "source_ids": []} for _ in range(27)]}), (True, "ok"))
        answer = {"checks": [{"status": "support", "source_ids": ["A1"]}] +
                  [{"status": "unknown", "source_ids": []} for _ in range(26)]}
        self.assertEqual(closed.validate_facet_response(case, ctx, answer), (False, "assertion_without_fixed_source:0"))
        bad_unknown = {"checks": [{"status": "unknown", "source_ids": ["S30"]}] +
                       [{"status": "unknown", "source_ids": []} for _ in range(26)]}
        self.assertEqual(closed.validate_facet_response(case, ctx, bad_unknown), (False, "unknown_with_evidence:0"))

    def test_four_product_value_requests_reuse_only_identical_rows_and_sources(self):
        entries = closed.build_product_requests(self.cases, self.contexts)
        self.assertEqual(len(entries), 4)
        self.assertEqual(sum(entry["coverage_count"] for entry in entries), 62)
        self.assertEqual({entry["row_count"] for entry in entries}, {27})
        case = self.case_by_id[P2]
        changed = json.loads(json.dumps(case))
        changed["case_id"] = "case-copy"
        changed["au_rows"][0]["conditions"][1]["value"] = "幅100×丈81cm(2枚)"
        all_contexts = dict(self.contexts)
        all_contexts["case-copy"] = self.contexts[P2]
        with self.assertRaisesRegex(ValueError, "row data differs"):
            closed.build_product_requests([case, changed], all_contexts)
        changed_source = json.loads(json.dumps(case))
        changed_source["case_id"] = "case-source-copy"
        changed_contexts = dict(self.contexts)
        changed_contexts["case-source-copy"] = json.loads(json.dumps(self.contexts[P2]))
        changed_contexts["case-source-copy"]["S30"]["contexts"] = ["異なる内容一覧"]
        with self.assertRaisesRegex(ValueError, "source evidence differs"):
            closed.build_product_requests([case, changed_source], changed_contexts)


if __name__ == "__main__":
    unittest.main()
