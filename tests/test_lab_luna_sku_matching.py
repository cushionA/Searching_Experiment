from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "sku-matching"
sys.path.insert(0, str(EXPERIMENT))
SCRIPT = EXPERIMENT / "trial_luna_sku_matching.py"
spec = importlib.util.spec_from_file_location("trial_luna_sku_matching_test", SCRIPT)
assert spec and spec.loader
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


def fixture_case():
    return {
        "case_id": "synthetic-neutral-case",
        "cohort": "synthetic-neutral",
        "rakuten_url": "https://example.invalid/r/sku",
        "rakuten_sku_key": "rakuten-sku-real-17",
        "rakuten_variant_id": "variant-real-18",
        "au_product_id": "au-product-real-23",
        "rakuten_conditions": [
            {"condition_id": "R0", "axis": "色", "value": "白",
             "choices": ["白", "黒"]},
        ],
        "selected_attributes": [
            {"axis": "幅", "value": 100, "unit": "cm", "source_verified": True},
        ],
        "gold_row_keys": ["gold-must-not-enter-request"],
        "au_rows": [
            {"row_key": "au-row-fixed-31", "sku_id": "au-sku-real-41",
             "row_index": 3, "column_index": 2,
             "conditions": [
                 {"condition_id": "A:au-row-fixed-31:0", "axis": "色", "value": "白",
                  "source": {"quote": "色=白"}},
                 {"condition_id": "A:au-row-fixed-31:1", "axis": "幅", "value": "100cm",
                  "source": {"quote": "幅=100cm"}},
             ]},
        ],
        "sources": [
            {"source_id": "S0", "kind": "title", "text": "中立な固定商品の説明",
             "source": {"quote": "中立な固定商品の説明"}},
        ],
    }


def answer(status="support", rak_refs=("A0",), au_status="support"):
    return {"rows": [{
        "rakuten_checks": [{"status": status, "source_ids": list(rak_refs)}],
        "au_checks": [
            {"status": au_status, "source_ids": ["A0"] if au_status != "unknown" else []},
            {"status": au_status, "source_ids": ["A1"] if au_status != "unknown" else []},
        ],
    }]}


class LunaSkuMatchingContractTests(unittest.TestCase):
    def test_nonfixed_source_assertions_are_downgraded(self):
        case = fixture_case()
        obj = answer(rak_refs=("S0",))
        guarded, issues = trial.guard_response(case, obj, {"S0": {"scope": "other_product"}})
        check = guarded["rows"][0]["rakuten_checks"][0]
        self.assertEqual(check, {"status": "unknown", "source_ids": []})
        self.assertTrue(any("source_not_fixed_product:S0" in e
                            for issue in issues for e in issue["errors"]))

    def test_wrong_axis_A_reference_is_downgraded(self):
        case = fixture_case()
        guarded, issues = trial.guard_response(case, answer(rak_refs=("A1",)), {})
        self.assertEqual(guarded["rows"][0]["rakuten_checks"][0],
                         {"status": "unknown", "source_ids": []})
        self.assertTrue(any("wrong_axis_reference:A1" in e
                            for issue in issues for e in issue["errors"]))

    def test_expansion_restores_exact_local_quotes_and_real_condition_ids(self):
        case = fixture_case()
        expanded = trial.smoke.expand_response(case, answer())
        row = expanded["rows"][0]
        self.assertEqual(row["row_key"], "au-row-fixed-31")
        self.assertEqual(row["rakuten_checks"][0]["evidence"][0]["quote"], "白")
        self.assertEqual(row["rakuten_checks"][0]["evidence"][0]["source_id"],
                         "A:au-row-fixed-31:0")
        self.assertEqual(row["au_checks"][1]["evidence"][0]["quote"], "100cm")

    def test_missing_and_malformed_answers_remain_not_evaluated(self):
        with tempfile.TemporaryDirectory() as temp:
            round_dir = Path(temp) / "round"
            case = fixture_case()
            trial.prepare(round_dir, [case], {"source": "synthetic"}, Path(temp) / "unused.zip", mode="baseline")
            result = trial.evaluate(round_dir)["cases"][0]
            self.assertEqual(result["decision"], "not_evaluated")
            self.assertEqual(result["validation_reason"], "missing_or_invalid_json")
            (round_dir / "answers/01.json").write_text("{broken", encoding="utf-8")
            result = trial.evaluate(round_dir)["cases"][0]
            self.assertEqual(result["decision"], "not_evaluated")
            self.assertFalse(result["validation_ok"])

    def test_decision_excludes_only_when_every_row_has_a_contradiction(self):
        case = fixture_case()
        obj = answer(status="contradiction", rak_refs=("A0",), au_status="contradiction")
        self.assertEqual(trial.decision(case, obj), ("exclude", None))
        unknown = answer(status="unknown", rak_refs=(), au_status="unknown")
        self.assertEqual(trial.decision(case, unknown), ("pending", None))

    def test_one_fully_supported_candidate_is_returned(self):
        case = fixture_case()
        self.assertEqual(trial.decision(case, answer()), ("candidate", "au-row-fixed-31"))

    def test_input_and_request_mutations_fail_integrity(self):
        for relative in ("inputs.json", "requests/01.json"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temp:
                round_dir = Path(temp) / "round"
                trial.prepare(round_dir, [fixture_case()], {"source": "synthetic"},
                              Path(temp) / "unused.zip", mode="baseline")
                target = round_dir / relative
                target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    trial.integrity(round_dir)

    def test_answer_mutation_after_evaluation_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            round_dir = Path(temp) / "round"
            trial.prepare(round_dir, [fixture_case()], {"source": "synthetic"},
                          Path(temp) / "unused.zip", mode="baseline")
            answer_path = round_dir / "answers/01.json"
            answer_path.write_text(json.dumps(answer()), encoding="utf-8")
            trial.evaluate(round_dir)
            answer_path.write_text(json.dumps(answer(status="unknown", rak_refs=(), au_status="unknown")),
                                   encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "answer was changed"):
                trial.evaluate(round_dir)

    def _prepare_scoped_synthetic(self, round_dir, temp, case):
        archive = Path(temp) / "empty-synthetic.zip"
        with zipfile.ZipFile(archive, "w"):
            pass
        trial.prepare(round_dir, [case], {"source": "synthetic"}, archive, mode="scoped")

    def _ready_signature_round(self, temp, signature_status="support"):
        round_dir = Path(temp) / "round"
        case = fixture_case()
        self._prepare_scoped_synthetic(round_dir, temp, case)
        (round_dir / "answers/01.json").write_text(json.dumps(answer()), encoding="utf-8")
        self.assertEqual(trial.evaluate(round_dir)["cases"][0]["decision"], "candidate")
        entries = trial.prepare_signature(round_dir)
        self.assertEqual(len(entries), 1)
        sig_answer = {"checks": [{"status": signature_status,
                                  "source_ids": ["A1"] if signature_status != "unknown" else []}]}
        sig_path = round_dir / entries[0]["answer"]
        sig_path.write_text(json.dumps(sig_answer, ensure_ascii=False), encoding="utf-8")
        return round_dir, case, sig_path

    def test_scoped_request_uses_explicit_A_ids_without_private_identifiers_or_choices(self):
        case = fixture_case()
        case["rakuten_conditions"][0]["condition_id"] = "LONG-RAKUTEN-CONDITION-ID"
        case["au_rows"][0]["conditions"][0]["condition_id"] = "LONG-AU-CONDITION-ID-0"
        case["au_rows"][0]["conditions"][1]["condition_id"] = "LONG-AU-CONDITION-ID-1"
        case["au_rows"].append({"row_key": "private-row-key-2", "sku_id": "private-sku-id-2",
                                "conditions": [
                                    {"condition_id": "LONG-OTHER-ID-0", "axis": "色", "value": "黒"},
                                    {"condition_id": "LONG-OTHER-ID-1", "axis": "幅", "value": "120cm"},
                                ]})
        case["gold_row_keys"] = ["SECRET-GOLD"]
        req = trial.scoped_request(case, {"S0": {"kind": "title", "text": "neutral", "scope": "fixed_product", "contexts": ["neutral"]}})
        payload_text = req["body"]["contents"][0]["parts"][0]["text"]
        payload = json.loads(payload_text.split("INPUT=", 1)[1])
        rows = payload["au_rows"]
        self.assertEqual([c["source_id"] for c in rows[0]["conditions"]], ["A0", "A1"])
        self.assertEqual([c["source_id"] for c in rows[1]["conditions"]], ["A0", "A1"])
        self.assertEqual(rows[0]["allowed_row_source_ids"], ["A0", "A1"])
        for private in ("LONG-RAKUTEN", "LONG-AU", "private-row-key", "private-sku-id", "SECRET-GOLD", "choices"):
            self.assertNotIn(private, payload_text)
        schema = req["body"]["generationConfig"]["responseSchema"]
        enum = schema["properties"]["rows"]["items"]["properties"]["rakuten_checks"]["items"]["properties"]["source_ids"]["items"]["enum"]
        self.assertEqual(enum, ["S0", "A0", "A1"])

    def test_signature_contradiction_or_unknown_vetoes_confirmed_color_candidate(self):
        for signature_status in ("contradiction", "unknown"):
            with self.subTest(signature_status=signature_status), tempfile.TemporaryDirectory() as temp:
                round_dir, case, _ = self._ready_signature_round(temp, signature_status)
                reviews = Path(temp) / "reviews.json"
                reviews.write_text(json.dumps([{"case_id": case["case_id"], "verdict": "confirm",
                                                "row_key": "au-row-fixed-31"}]), encoding="utf-8")
                summary = trial.export_links(round_dir, reviews)
                self.assertEqual(summary["exported_links"], 0)
                self.assertEqual(summary["cases"][0]["decision"], "pending")
                self.assertFalse(summary["cases"][0]["selected_dimensions_passed"])

    def test_export_requires_exact_independent_review_and_supported_dimension_signature(self):
        with tempfile.TemporaryDirectory() as temp:
            round_dir, case, sig_path = self._ready_signature_round(temp, "support")
            reviews = Path(temp) / "reviews.json"
            reviews.write_text(json.dumps([{"case_id": case["case_id"], "verdict": "confirm",
                                            "row_key": "different-row"}]), encoding="utf-8")
            summary = trial.export_links(round_dir, reviews)
            self.assertEqual(summary["exported_links"], 0)
            self.assertEqual(summary["cases"][0]["decision"], "pending")

            reviews.write_text(json.dumps([{"case_id": case["case_id"], "verdict": "confirm",
                                            "row_key": "au-row-fixed-31"}]), encoding="utf-8")
            summary = trial.export_links(round_dir, reviews)
            self.assertEqual(summary["exported_links"], 1)
            self.assertEqual(summary["cases"][0]["decision"], "adopt")
            link = json.loads((round_dir / "links.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(link["rakuten_sku_key"], "rakuten-sku-real-17")
            self.assertEqual(link["au_sku_id"], "au-sku-real-41")
            self.assertEqual(link["au_row_key"], "au-row-fixed-31")
            self.assertEqual((link["au_row_index"], link["au_column_index"]), (3, 2))
            signature = trial.signature_results(round_dir)[case["case_id"]]
            self.assertEqual(link["signature_answer_sha256"], trial.sha(sig_path.read_bytes()))
            self.assertEqual(link["selected_dimensions_evidence"], signature["expanded_checks"])
            check = link["selected_dimensions_evidence"][0]
            self.assertEqual(check["target"], {"axis": "幅", "value": 100, "unit": "cm", "source_verified": True})
            self.assertEqual(check["evidence"][0]["quote"], "100cm")

    def test_empty_dimension_signature_cannot_pass_vacuously(self):
        with tempfile.TemporaryDirectory() as temp:
            round_dir = Path(temp) / "round"
            case = fixture_case()
            case["selected_attributes"] = []
            self._prepare_scoped_synthetic(round_dir, temp, case)
            (round_dir / "answers/01.json").write_text(json.dumps(answer()), encoding="utf-8")
            trial.evaluate(round_dir)
            entries = trial.prepare_signature(round_dir)
            self.assertEqual(entries[0]["targets"], [])
            (round_dir / entries[0]["answer"]).write_text(json.dumps({"checks": []}), encoding="utf-8")
            result = trial.signature_results(round_dir)[case["case_id"]]
            self.assertTrue(result["validation_ok"])
            self.assertFalse(result["passed"])

    def test_signature_targets_must_match_frozen_selected_dimensions(self):
        with tempfile.TemporaryDirectory() as temp:
            round_dir, case, _ = self._ready_signature_round(temp, "support")
            manifest_path = round_dir / "signature-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["entries"][0]["targets"][0]["value"] = 999
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "targets differ from frozen"):
                trial.signature_results(round_dir)

    def test_signature_v2_request_is_separate_and_preserves_v1_request_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            round_dir = Path(temp) / "round"
            case = fixture_case()
            self._prepare_scoped_synthetic(round_dir, temp, case)
            (round_dir / "answers/01.json").write_text(json.dumps(answer()), encoding="utf-8")
            trial.evaluate(round_dir)
            v1 = trial.prepare_signature(round_dir)
            v1_path = round_dir / v1[0]["request"]
            before = v1_path.read_bytes()
            v2 = trial.prepare_signature(round_dir, "signature-v2")
            self.assertEqual(v1_path.read_bytes(), before)
            manifest = json.loads((round_dir / "signature-v2-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema"], "selected-dimensions-verification-v2")
            self.assertEqual(v2[0]["request"], "signature-v2-requests/01.json")
            request = json.loads((round_dir / v2[0]["request"]).read_text(encoding="utf-8"))
            prompt = request["body"]["contents"][0]["parts"][0]["text"]
            self.assertIn("signature-v2", v2[0]["request"])
            self.assertIn("寸法集合", prompt)
            self.assertIn("明示の軸ラベル", prompt)
            self.assertNotIn("gold-row", prompt)

    def test_missing_or_malformed_signature_answer_blocks_export(self):
        for malformed in (False, True):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as temp:
                round_dir = Path(temp) / "round"
                case = fixture_case()
                self._prepare_scoped_synthetic(round_dir, temp, case)
                (round_dir / "answers/01.json").write_text(json.dumps(answer()), encoding="utf-8")
                trial.evaluate(round_dir)
                entries = trial.prepare_signature(round_dir)
                sig_path = round_dir / entries[0]["answer"]
                if malformed:
                    sig_path.write_text("{broken", encoding="utf-8")
                reviews = Path(temp) / "reviews.json"
                reviews.write_text(json.dumps([{"case_id": case["case_id"], "verdict": "confirm",
                                                "row_key": "au-row-fixed-31"}]), encoding="utf-8")
                summary = trial.export_links(round_dir, reviews)
                self.assertEqual(summary["exported_links"], 0)
                self.assertEqual(summary["cases"][0]["decision"], "pending")



if __name__ == "__main__":
    unittest.main()
