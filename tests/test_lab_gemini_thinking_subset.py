from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(EXP))
DRIVER_PATH = EXP / "trial_gemini_thinking_off_subset.py"
SPEC = importlib.util.spec_from_file_location("gemini_thinking_subset", DRIVER_PATH)
driver = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = driver
SPEC.loader.exec_module(driver)


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class GeminiThinkingSubsetTests(unittest.TestCase):
    def test_low_mode_binds_model_endpoint_and_only_adds_thinking_config(self):
        driver.configure("low")
        self.assertEqual(driver.transport.MODEL, "gemini-3.5-flash-lite")
        self.assertIn("/models/gemini-3.5-flash-lite:generateContent", driver.transport.ENDPOINT)
        self.assertEqual(driver.THINKING_CONFIG,
                         {"thinkingLevel": "LOW", "includeThoughts": False})
        body = {"contents": [{"parts": [{"text": "fixed task"}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
        adapted = driver.thinking_off_body(body)
        self.assertEqual(adapted["generationConfig"]["thinkingConfig"], driver.THINKING_CONFIG)
        restored = copy.deepcopy(adapted)
        restored["generationConfig"].pop("thinkingConfig")
        self.assertEqual(restored, body)
        with self.assertRaises(ValueError):
            driver.thinking_off_body({**body, "generationConfig": {
                **body["generationConfig"], "thinkingConfig": {"thinkingLevel": "MINIMAL"}}})

    def test_prepare_keeps_selected_four_cases_and_exact_chunk_coverage(self):
        driver.configure("low")
        with tempfile.TemporaryDirectory(prefix="test-gemini-thinking-subset-",
                                         dir=EXP / "results") as tmp:
            root = Path(tmp) / "run"
            driver.prepare(root, "low")
            plan = read(root / "replay-plan.json")
            dispatch = read(root / "matching-dispatch-manifest.json")
            cases = read(root / "derived/inputs.json")
            self.assertEqual(plan["thinking_config_requested"],
                             {"thinkingLevel": "LOW", "includeThoughts": False})
            self.assertEqual(plan["model_requested"], "gemini-3.5-flash-lite")
            self.assertEqual(plan["case_ids"], list(driver.CASE_IDS))
            self.assertEqual({c["case_id"] for c in cases}, set(driver.CASE_IDS))
            self.assertEqual(len(dispatch["entries"]), 9)
            self.assertEqual(sum(e["row_count"] for e in dispatch["entries"]), 190)
            self.assertEqual(dispatch["prior_answers_reused"], 0)
            by_case = {cid: [] for cid in driver.CASE_IDS}
            for entry in dispatch["entries"]:
                by_case[entry["original_case_id"]].append(entry)
                saved = read(root / entry["request"])
                control = read(EXP / entry["control_request"])
                body = saved["body"]
                original_body = control["body"]
                comparable = copy.deepcopy(body)
                comparable["generationConfig"].pop("thinkingConfig")
                self.assertEqual(comparable, original_body)
                self.assertEqual(body["generationConfig"]["thinkingConfig"], driver.THINKING_CONFIG)
            for cid, entries in by_case.items():
                pos = 0
                for entry in sorted(entries, key=lambda e: e["row_start"]):
                    self.assertEqual(entry["row_start"], pos)
                    pos += entry["row_count"]
                expected = next(c for c in cases if c["case_id"] == cid)
                self.assertEqual(pos, len(expected["au_rows"]))

    def test_review_prompts_refresh_predictions_and_provider_bodies(self):
        driver.configure("low")
        with tempfile.TemporaryDirectory(prefix="test-gemini-thinking-review-",
                                         dir=EXP / "results") as tmp:
            root = Path(tmp) / "run"
            driver.prepare(root, "low")
            driver.stages.collect_matching(root)
            driver.signature_prepare(root)
            self.assertLessEqual(read(root / "signature-dispatch-manifest.json")["planned_calls"], 4)
            driver.reviews_prepare(root)
            source = {e["case_id"]: read(driver.SOURCE / e["request"])
                      for e in read(driver.SOURCE / "review-dispatch-manifest.json")["entries"]}
            dispatch = read(root / "review-dispatch-manifest.json")
            self.assertEqual(dispatch["planned_calls"], 4)
            for entry in dispatch["entries"]:
                fresh = read(root / entry["request"])
                old_item = source[entry["case_id"]]["cases"][0]
                new_item = fresh["cases"][0]
                fixed_new, fixed_old = copy.deepcopy(new_item), copy.deepcopy(old_item)
                for key in ("prediction", "matching_evidence_trace", "dimension_verification"):
                    self.assertIn(key, new_item)
                    fixed_new.pop(key, None)
                    fixed_old.pop(key, None)
                self.assertEqual(fixed_new, fixed_old)
                body = read(root / entry["body_request"])
                self.assertEqual(body["generationConfig"]["thinkingConfig"], driver.THINKING_CONFIG)
                self.assertEqual(entry["body_request_sha256"], driver.base.sha(root / entry["body_request"]))

    def test_failed_fake_attempt_cannot_be_resent(self):
        class Opener:
            calls = 0

            def __call__(self, _request, timeout=None):
                self.calls += 1
                raise urllib.error.URLError("fixture failure")

        opener = Opener()
        with tempfile.TemporaryDirectory(prefix="gemini-thinking-once-") as tmp:
            root = Path(tmp)
            answer = root / "answer.json"
            body = {"contents": [{"parts": [{"text": "fixture"}]}]}
            driver.transport.call_once(root, "matching", 1, body, answer, "fixture-token", opener=opener)
            with self.assertRaises(FileExistsError):
                driver.transport.call_once(root, "matching", 1, body, answer, "fixture-token", opener=opener)
            self.assertEqual(opener.calls, 1)
            self.assertTrue((root / "provider/matching/001.attempt.json").exists())
            self.assertFalse(answer.exists())


if __name__ == "__main__":
    unittest.main()
