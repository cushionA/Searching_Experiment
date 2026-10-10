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
spec = importlib.util.spec_from_file_location("trial_luna_sku_policy_test", SCRIPT)
assert spec and spec.loader
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


def make_case(dimensions):
    return {
        "case_id": "policy-synthetic",
        "rakuten_url": "https://example.invalid/item",
        "rakuten_sku_key": "rk-private",
        "rakuten_variant_id": "variant-private",
        "au_product_id": "au-private",
        "rakuten_conditions": [{"condition_id": "R-private", "axis": "色", "value": "白",
                                "choices": ["白", "黒"]}],
        "selected_attributes": dimensions,
        "au_rows": [{"row_key": "row-private", "sku_id": "sku-private", "row_index": 0,
                      "column_index": 0,
                      "conditions": [{"condition_id": "A-private", "axis": "色", "value": "白",
                                      "source": {"quote": "色=白"}}]}],
        "sources": [{"source_id": "S0", "kind": "title", "text": "固定商品", "scope": "fixed_product",
                     "source": {"quote": "固定商品"}}],
    }


def supported_answer():
    return {"rows": [{"rakuten_checks": [{"status": "support", "source_ids": ["A0"]}],
                      "au_checks": [{"status": "support", "source_ids": ["A0"]}]}]}


class LunaSkuTaskPolicyV3Tests(unittest.TestCase):
    def ready_round(self, temp, dimensions):
        archive = Path(temp) / "empty.zip"
        with zipfile.ZipFile(archive, "w"):
            pass
        round_dir = Path(temp) / "round"
        case = make_case(dimensions)
        manifest = trial.prepare(round_dir, [case], {"input": "synthetic"}, archive, mode="scoped-v3")
        self.assertEqual(manifest["mode"], "scoped-v3")
        self.assertEqual(manifest["task_version"], trial.VERSION_V3)
        (round_dir / "answers/01.json").write_text(json.dumps(supported_answer()), encoding="utf-8")
        self.assertEqual(trial.evaluate(round_dir)["cases"][0]["decision"], "candidate")
        return round_dir, case

    def test_v2_empty_signature_fails_but_v3_is_local_na_pass(self):
        for stage, expected_pass, expected_applicable in (("signature-v2", False, None),
                                                          ("signature-v3", True, False)):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temp:
                round_dir, _ = self.ready_round(temp, [])
                entry = trial.prepare_signature(round_dir, stage)[0]
                if stage == "signature-v3":
                    self.assertFalse(entry["inference_required"])
                else:
                    self.assertNotIn("inference_required", entry)
                answer_path = round_dir / entry["answer"]
                if stage == "signature-v3":
                    self.assertEqual(json.loads(answer_path.read_text(encoding="utf-8")), {"checks": []})
                else:
                    answer_path.write_text(json.dumps({"checks": []}), encoding="utf-8")
                result = trial.signature_results(round_dir, stage)["policy-synthetic"]
                self.assertEqual(result["passed"], expected_pass)
                if stage == "signature-v3":
                    self.assertEqual(result["applicable"], expected_applicable)
                else:
                    self.assertNotIn("applicable", result)

    def test_v3_required_dimension_unknown_or_contradiction_cannot_pass(self):
        for status in ("unknown", "contradiction"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                round_dir, _ = self.ready_round(temp, [{"axis": "幅", "value": 100, "unit": "cm"}])
                entry = trial.prepare_signature(round_dir, "signature-v3")[0]
                answer = {"checks": [{"status": status, "source_ids": [] if status == "unknown" else ["A0"]}]}
                (round_dir / entry["answer"]).write_text(json.dumps(answer), encoding="utf-8")
                result = trial.signature_results(round_dir, "signature-v3")["policy-synthetic"]
                self.assertFalse(result["passed"])
                self.assertTrue(result["applicable"])

    def test_scoped_v3_payload_keeps_schema_and_excludes_choices_and_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "empty.zip"
            with zipfile.ZipFile(archive, "w"):
                pass
            round_dir = Path(temp) / "round"
            trial.prepare(round_dir, [make_case([])], {}, archive, mode="scoped-v3")
            req = json.loads((round_dir / "requests/01.json").read_text(encoding="utf-8"))
            prompt = req["body"]["contents"][0]["parts"][0]["text"]
            self.assertIn("同じfacet/同じ意味", prompt)
            self.assertNotIn("choices", prompt)
            self.assertNotIn("R-private", prompt)
            self.assertNotIn("row-private", prompt)
            schema = req["body"]["generationConfig"]["responseSchema"]
            for side in ("rakuten_checks", "au_checks"):
                props = schema["properties"]["rows"]["items"]["properties"][side]["items"]["properties"]
                self.assertEqual(set(props), {"status", "source_ids"})

    def test_baseline_prompt_body_and_scoped_v2_instruction_bytes_are_unchanged(self):
        case = make_case([])
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "empty.zip"
            with zipfile.ZipFile(archive, "w"):
                pass
            round_dir = Path(temp) / "baseline"
            trial.prepare(round_dir, [case], {}, archive, mode="baseline")
            saved = json.loads((round_dir / "requests/01.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["body"], trial.smoke.request_body(case))
            self.assertEqual(saved["body"]["contents"][0]["parts"][0]["text"],
                             trial.smoke.request_body(case)["contents"][0]["parts"][0]["text"])

    def test_v3_signature_prompt_is_v2_prompt_plus_addition(self):
        with tempfile.TemporaryDirectory() as temp:
            round_dir, _ = self.ready_round(temp, [{"axis": "幅", "value": 100, "unit": "cm"}])
            v2 = trial.prepare_signature(round_dir, "signature-v2")[0]
            v3 = trial.prepare_signature(round_dir, "signature-v3")[0]
            p2 = json.loads((round_dir / v2["request"]).read_text(encoding="utf-8"))["body"]["contents"][0]["parts"][0]["text"]
            p3 = json.loads((round_dir / v3["request"]).read_text(encoding="utf-8"))["body"]["contents"][0]["parts"][0]["text"]
            self.assertEqual(p3, p2.replace("INPUT=", trial.policy.SIGNATURE_V3_ADDITION + "\nINPUT=", 1))
            self.assertIn("円形", p3)
            self.assertIn("直径", p3)


if __name__ == "__main__":
    unittest.main()
