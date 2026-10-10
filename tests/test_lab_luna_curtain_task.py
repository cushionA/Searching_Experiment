from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "sku-matching"
sys.path.insert(0, str(EXPERIMENT))
import sku_luna_curtain_task as curtain

INPUT_DIR = EXPERIMENT / "results" / "20261010T185500Z-luna-multirow62" / "inference"


def synthetic_case(product_id="p1", lace="あり", count=27):
    return {"case_id": f"case-{product_id}-{lace}", "au_product_id": product_id,
            "rakuten_conditions": [{"axis": "サイズ", "value": "100×135cm", "choices": ["unselected"]},
                                   {"axis": "カラー", "value": "白", "choices": ["白", "黒"]},
                                   {"axis": "レースカーテン", "value": lace, "choices": ["あり", "なし"]}],
            "au_rows": [{"row_key": f"hidden-{i}", "conditions": [
                {"axis": "カラー", "value": "白"},
                {"axis": "サイズ", "value": f"幅{100 if i < count // 2 else 150}×丈{80+i}cm({4 if i < count // 2 else 2}枚組)"}]}
                for i in range(count)]}


def synthetic_contexts(keyword=True):
    ctx = {"S0": {"kind": "title", "scope": "fixed_product", "text": "カーテン4枚セット",
                   "contexts": ["カーテン4枚セット"], "provenance": {"source_id": "title"}},
           "S31": {"kind": "description", "scope": "fixed_product", "text": "ミラーレースカーテン2枚",
                    "contexts": ["内容 幅100cm 遮光カーテン2枚 ミラーレースカーテン2枚"],
                    "provenance": {"source_id": "lace-table"}},
           "S84": {"kind": "purchase_option", "scope": "fixed_product", "text": "幅100cmは4枚、幅150cmは2枚",
                    "contexts": ["幅別の主カーテン数"], "provenance": {"source_id": "quantity"}},
           "S99": {"kind": "description", "scope": "fixed_product", "text": "検索ワード ミラーレースカーテン",
                    "contexts": ["▼検索ワード ミラーレースカーテン"]},
           "S98": {"kind": "description", "scope": "related_product", "text": "ミラーレースカーテン2枚",
                    "contexts": ["related page"]}}
    return ctx


def unknown_answer(count=27):
    return {"checks": [{"status": "unknown", "source_ids": []} for _ in range(count)]}


class CurtainTaskRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads((INPUT_DIR / "inputs.json").read_text(encoding="utf-8"))
        cls.contexts = json.loads((INPUT_DIR / "source-contexts.json").read_text(encoding="utf-8"))

    def test_actual_inputs_make_four_shared_product_value_requests_with_coverage(self):
        entries = curtain.build_curtain_lace_requests(self.cases, self.contexts)
        self.assertEqual(len(entries), 4)
        self.assertEqual(sum(entry["coverage_count"] for entry in entries), 62)
        self.assertEqual({(entry["au_product_id"], entry["selected_lace"]["value"]) for entry in entries},
                         {("778809347", "あり"), ("778809347", "なし"),
                          ("778818828", "あり"), ("778818828", "なし")})
        self.assertEqual({entry["row_count"] for entry in entries}, {27})
        self.assertEqual(len({cid for entry in entries for cid in entry["covered_case_ids"]}), 62)
        self.assertTrue(all(entry["coverage_count"] > 1 for entry in entries))

    def test_full_width_qualified_content_sources_and_keyword_non_evidence(self):
        entries = curtain.build_curtain_lace_requests(self.cases, self.contexts)
        p1 = next(entry for entry in entries if entry["au_product_id"] == "778809347" and entry["selected_lace"]["value"] == "あり")
        p2 = next(entry for entry in entries if entry["au_product_id"] == "778818828" and entry["selected_lace"]["value"] == "なし")
        text1 = p1["request"]["body"]["contents"][0]["parts"][0]["text"]
        payload1 = json.loads(text1.split("INPUT=", 1)[1])
        payload2 = json.loads(p2["request"]["body"]["contents"][0]["parts"][0]["text"].split("INPUT=", 1)[1])
        self.assertIn("幅100cm", payload1["sources"]["S33"]["contexts"][0])
        self.assertIn("ミラーレースカーテン 2枚", payload1["sources"]["S33"]["contexts"][0])
        self.assertIn("ミラーレースカーテン 1枚", payload1["sources"]["S38"]["contexts"][0])
        self.assertIn("幅100cmは4枚セット", payload1["sources"]["S84"]["text"])
        self.assertIn("幅100cmは2枚", payload2["sources"]["S82"]["text"])
        self.assertIn("幅150cmは1枚", payload2["sources"]["S82"]["contexts"][0])
        self.assertNotIn("S2", payload1["sources"])
        self.assertNotIn("S2", payload2["sources"])
        self.assertNotIn("検索ワード", " ".join(payload1["sources"]))
        self.assertIn("汎用keyword列", text1)
        self.assertTrue(all(src["scope"] == "fixed_product" for src in payload1["sources"].values()))

    def test_inference_input_is_selected_facet_and_all_27_ordered_rows_only(self):
        case = next(c for c in self.cases if c["au_product_id"] == "778809347" and
                    next(x for x in c["rakuten_conditions"] if x["axis"] == "レースカーテン")["value"] == "あり")
        entry = next(e for e in curtain.build_curtain_lace_requests([case], self.contexts)
                     if e["selected_lace"]["value"] == "あり")
        text = entry["request"]["body"]["contents"][0]["parts"][0]["text"]
        payload = json.loads(text.split("INPUT=", 1)[1])
        self.assertEqual(payload["selected_lace"], {"axis": "レースカーテン", "value": "あり"})
        self.assertEqual(payload["row_order"], list(range(27)))
        self.assertEqual(len(payload["au_rows"]), 27)
        self.assertEqual(payload["au_rows"][8]["conditions"][1]["value"], "幅150×丈200cm(2枚組)")
        self.assertNotIn("choices", text)
        self.assertNotIn("case_id", text)
        self.assertNotIn("rakuten_conditions", payload)
        self.assertNotIn("row_key", text)
        check_props = entry["request"]["body"]["generationConfig"]["responseSchema"]["properties"]["checks"]
        self.assertEqual((check_props["minItems"], check_props["maxItems"]), (27, 27))
        self.assertEqual(set(check_props["items"]["properties"]), {"status", "source_ids"})

    def test_product_level_answer_expands_only_lace_check_into_full_answer(self):
        case = synthetic_case()
        entry = {"au_product_id": "p1", "selected_lace": {"axis": "レースカーテン", "value": "あり"},
                 "covered_case_ids": [case["case_id"]], "row_count": 27,
                 "request": curtain.build_curtain_lace_requests([case], {case["case_id"]: synthetic_contexts()})[0]["request"]}
        base = {"rows": [{"rakuten_checks": [
                    {"status": "support", "source_ids": ["A1"]},
                    {"status": "unknown", "source_ids": []},
                    {"status": "contradiction", "source_ids": ["A0"]}],
                 "au_checks": [{"status": "unknown", "source_ids": []},
                               {"status": "support", "source_ids": ["A1"]}]}
                for _ in range(27)]}
        original = copy.deepcopy(base)
        facet = {"checks": [{"status": "support", "source_ids": ["S31"]} for _ in range(27)]}
        expanded = curtain.expand_curtain_lace_answer(case, base, entry, facet)
        self.assertEqual(base, original)
        for before, after in zip(base["rows"], expanded["rows"], strict=True):
            self.assertEqual(after["rakuten_checks"][0], before["rakuten_checks"][0])
            self.assertEqual(after["rakuten_checks"][1], before["rakuten_checks"][1])
            self.assertEqual(after["rakuten_checks"][2], facet["checks"][0])
            self.assertEqual(after["au_checks"], before["au_checks"])

    def test_non_evidence_keyword_cannot_be_the_only_assertion_source(self):
        case = synthetic_case(count=2)
        invalid = {"checks": [{"status": "support", "source_ids": ["A1"]},
                               {"status": "unknown", "source_ids": []}]}
        self.assertEqual(curtain.validate_facet_response(case, synthetic_contexts(), invalid),
                         (False, "assertion_without_fixed_source_evidence:0"))

    def test_direct_facet_api_validates_shape_refs_and_unknown_evidence(self):
        case = synthetic_case(count=2)
        contexts = synthetic_contexts()
        request = curtain.facet_request(case, contexts, "あり")
        self.assertEqual(set(request), {"body"})
        self.assertIn('"selected_lace":{"axis":"レースカーテン","value":"あり"}',
                      request["body"]["contents"][0]["parts"][0]["text"])
        valid = {"checks": [{"status": "support", "source_ids": ["S31"]},
                             {"status": "unknown", "source_ids": []}]}
        self.assertEqual(curtain.validate_facet_response(case, contexts, valid), (True, "ok"))
        unknown_with_ref = {"checks": [{"status": "unknown", "source_ids": ["S31"]},
                                        {"status": "unknown", "source_ids": []}]}
        self.assertEqual(curtain.validate_facet_response(case, contexts, unknown_with_ref),
                         (False, "unknown_with_evidence:0"))
        self.assertEqual(curtain.validate_facet_response(case, contexts, {"checks": valid["checks"][:1]}),
                         (False, "row_coverage_error"))
        with self.assertRaisesRegex(ValueError, "differs from case"):
            curtain.facet_request(case, contexts, "なし")

    def test_shared_product_value_group_rejects_row_or_source_drift(self):
        first = synthetic_case(count=2)
        second = copy.deepcopy(first)
        second["case_id"] = "case-p1-あり-second"
        ctx_a, ctx_b = synthetic_contexts(), synthetic_contexts()
        with self.assertRaisesRegex(ValueError, "row coverage differs"):
            changed_rows = copy.deepcopy(second)
            changed_rows["au_rows"][1]["conditions"][1]["value"] = "幅150×丈88cm(2枚組)"
            curtain.build_curtain_lace_requests([first, changed_rows],
                                                {first["case_id"]: ctx_a, changed_rows["case_id"]: ctx_b})
        with self.assertRaisesRegex(ValueError, "source context differs"):
            ctx_b["S31"]["contexts"] = ["別の表内容"]
            curtain.build_curtain_lace_requests([first, second],
                                                {first["case_id"]: ctx_a, second["case_id"]: ctx_b})

    def test_wrong_product_or_uncovered_case_cannot_reuse_product_answer(self):
        case = synthetic_case()
        entry = curtain.build_curtain_lace_requests([case], {case["case_id"]: synthetic_contexts()})[0]
        other = copy.deepcopy(case)
        other["case_id"] = "not-covered"
        with self.assertRaisesRegex(ValueError, "not covered"):
            curtain.expand_curtain_lace_answer(other, {"rows": []}, entry, unknown_answer())

    def test_single_case_review_instruction_is_explicit(self):
        self.assertIn("単一case", curtain.SINGLE_CASE_REVIEW_ADDITION)
        self.assertIn("case_id", curtain.SINGLE_CASE_REVIEW_ADDITION)
        self.assertIn("prediction.candidate_row_key", curtain.SINGLE_CASE_REVIEW_ADDITION)
        self.assertIn("別case", curtain.SINGLE_CASE_REVIEW_ADDITION)


if __name__ == "__main__":
    unittest.main()
