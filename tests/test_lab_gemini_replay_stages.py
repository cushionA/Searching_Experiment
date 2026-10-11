from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments/sku-matching"
STAGES_PATH = EXP / "sku_gemini_replay_stages.py"
SPEC = importlib.util.spec_from_file_location("sku_gemini_replay_stages", STAGES_PATH)
stages = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = stages
SPEC.loader.exec_module(stages)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def request_input(req: dict) -> dict:
    text = req["body"]["contents"][0]["parts"][0]["text"]
    marker = "INPUT="
    if marker not in text:
        raise AssertionError("request lacks INPUT marker")
    return json.loads(text.split(marker, 1)[1])


class GeminiReplayStagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gemini-replay-test-")
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name) / "replay-run"
        self.assertFalse(self.run_dir.is_relative_to(ROOT))

    def prepare(self):
        return stages.prepare(self.run_dir)

    def test_prepare_builds_exact_70_chunk_coverage_and_50_selected_only_contracts(self):
        result = self.prepare()
        srcdir = stages.V13 / "derived"
        source_cases = read_json(srcdir / "inputs.json")
        source_manifest = read_json(srcdir / "manifest.json")
        dispatch = read_json(self.run_dir / "matching-dispatch-manifest.json")
        manifest = read_json(self.run_dir / "derived/manifest.json")
        cases = {case["case_id"]: case for case in source_cases}

        self.assertEqual(result["case_count"], 50)
        self.assertEqual(result["chunk_count"], 70)
        self.assertEqual(len({case["case_id"] for case in source_cases}), 50)
        self.assertEqual(sum(len(case["au_rows"]) for case in source_cases), 934)
        self.assertEqual(len(dispatch["entries"]), 70)
        self.assertEqual(len(manifest["entries"]), 50)
        self.assertEqual(len({entry["original_case_id"] for entry in dispatch["entries"]}), 50)
        self.assertEqual(dispatch["source_mix"], {
            "v9_original_cases": 45,
            "v13_fresh_cases": 4,
            "v9_fallback_cases": 1,
            "fallback_case_ids": ["case-105af0d9f0b13877f3c1"],
        })

        full_source_entries = {entry["case_id"]: entry for entry in source_manifest["entries"]}
        for entry in manifest["entries"]:
            copied = self.run_dir / "derived" / entry["request"]
            original = srcdir / full_source_entries[entry["case_id"]]["request"]
            self.assertEqual(copied.read_bytes(), original.read_bytes())
            payload = request_input(read_json(copied))
            case = cases[entry["case_id"]]
            self.assertEqual(payload["rakuten_conditions"], [
                {"axis": cond["axis"], "value": cond["value"]}
                for cond in case["rakuten_conditions"]
            ])
            self.assertTrue(all("choices" not in cond for cond in payload["rakuten_conditions"]))
            self.assertEqual(payload["selected_attributes"], [
                {key: attr.get(key) for key in ("axis", "value", "unit")}
                for attr in case.get("selected_attributes", [])
            ])
            self.assertTrue(all("choices" not in attr for attr in payload["selected_attributes"]))
            self.assertTrue((self.run_dir / "derived" / entry["answer"]).parent.is_dir())
            self.assertFalse((self.run_dir / "derived" / entry["answer"]).exists())

        by_case = {cid: [] for cid in cases}
        for entry in dispatch["entries"]:
            by_case[entry["original_case_id"]].append(entry)
            source_root = stages.V13 if entry["source_round"] == "v13-fresh" else stages.V9
            source_request = source_root / entry["source_request"]
            copied_request = self.run_dir / entry["request"]
            self.assertEqual(copied_request.read_bytes(), source_request.read_bytes())
            raw = read_json(copied_request)
            self.assertEqual(entry["request_body_sha256"], stages._body_sha(copied_request.read_bytes()))
            payload = request_input(raw)
            case = cases[entry["original_case_id"]]
            start = entry["row_start"]
            stop = start + entry["row_count"]
            expected = case["au_rows"][start:stop]
            self.assertLessEqual(entry["row_count"], 27)
            self.assertEqual(len(payload["au_rows"]), entry["row_count"])
            self.assertEqual(len(expected), entry["row_count"])
            for local_index, (actual, original) in enumerate(zip(payload["au_rows"], expected, strict=True)):
                self.assertEqual(actual["row_index"], local_index)
                self.assertEqual(
                    [(x["axis"], x["value"]) for x in actual["conditions"]],
                    [(x["axis"], x["value"]) for x in original["conditions"]],
                )
        for cid, entries in by_case.items():
            pos = 0
            for entry in sorted(entries, key=lambda x: x["row_start"]):
                self.assertEqual(entry["row_start"], pos)
                pos += entry["row_count"]
            self.assertEqual(pos, len(cases[cid]["au_rows"]), cid)

    def test_missing_matching_chunks_stay_not_evaluated_without_old_answer_reuse(self):
        self.prepare()

        result = stages.collect_matching(self.run_dir)

        self.assertEqual(result["validation_pass"], 0)
        self.assertEqual(result["counts"], {"not_evaluated": 50})
        manifest = read_json(self.run_dir / "derived/manifest.json")
        self.assertEqual(len(manifest["entries"]), 50)
        for entry in manifest["entries"]:
            self.assertEqual(entry["output_origin"]["method"], "incomplete Gemini response; invalid placeholder")
            self.assertFalse(entry["output_origin"]["all_chunks_valid"])
            self.assertNotIn("reused_from", entry)
            answer = read_json(self.run_dir / "derived" / entry["answer"])
            self.assertIn("chunk_errors", answer)
            self.assertNotIn("rows", answer)

    def _write_all_unknown_chunk_answers(self):
        dispatch = read_json(self.run_dir / "matching-dispatch-manifest.json")
        for entry in dispatch["entries"]:
            request = read_json(self.run_dir / entry["request"])
            payload = request_input(request)
            rows = []
            for row in payload["au_rows"]:
                rows.append({
                    "rakuten_checks": [
                        {"status": "unknown", "source_ids": []}
                        for _ in payload["rakuten_conditions"]
                    ],
                    "au_checks": [
                        {"status": "unknown", "source_ids": []}
                        for _ in row["conditions"]
                    ],
                })
            answer = self.run_dir / entry["answer"]
            answer.parent.mkdir(parents=True, exist_ok=True)
            answer.write_text(json.dumps({"rows": rows}), encoding="utf-8")

    def test_all_unknown_matching_is_pending_and_review_prompts_only_refresh_three_dynamic_fields(self):
        self.prepare()
        self._write_all_unknown_chunk_answers()

        collected = stages.collect_matching(self.run_dir)
        self.assertEqual(collected["validation_pass"], 70)
        self.assertEqual(collected["counts"], {"pending": 50})
        signature = stages.signature_prepare(self.run_dir)
        self.assertEqual(signature["candidate_count"], 0)
        self.assertEqual(signature["planned_calls"], 0)
        review_result = stages.reviews_prepare(self.run_dir)
        self.assertEqual(review_result, {"planned_calls": 50, "case_coverage": 50})

        source_manifest = read_json(stages.V13 / "review-dispatch-manifest.json")
        dispatch = read_json(self.run_dir / "review-dispatch-manifest.json")
        self.assertEqual(dispatch["planned_calls"], 50)
        self.assertEqual(len(dispatch["entries"]), 50)
        for old, new in zip(source_manifest["entries"], dispatch["entries"], strict=True):
            original = read_json(stages.V13 / old["request"])
            generated = read_json(self.run_dir / new["request"])
            self.assertEqual(new["case_id"], old["case_id"])
            self.assertTrue(new["inference_required"])
            old_item = original["cases"][0]
            new_item = generated["cases"][0]
            fixed_old = copy.deepcopy(old_item)
            fixed_new = copy.deepcopy(new_item)
            dynamic = ("prediction", "matching_evidence_trace", "dimension_verification")
            for field in dynamic:
                fixed_old.pop(field, None)
                fixed_new.pop(field, None)
            self.assertEqual(fixed_new, fixed_old)
            self.assertEqual(new_item["prediction"]["decision"], "pending")
            self.assertEqual(new_item["prediction"]["candidate_row_key"], None)
            trace = new_item["matching_evidence_trace"]
            case = next(c for c in read_json(self.run_dir / "derived/inputs.json")
                        if c["case_id"] == new["case_id"])
            self.assertEqual(len(trace), len(case["au_rows"]))
            self.assertTrue(all(
                check["status"] == "unknown" and check["source_ids"] == []
                for row in trace
                for side in ("rakuten_checks", "au_checks")
                for check in row[side]
            ))
            self.assertEqual(new_item["dimension_verification"]["passed"], None)
            self.assertFalse((self.run_dir / new["answer"]).exists())

    def test_missing_or_invalid_reviews_remain_not_evaluated_and_export_no_links(self):
        self.prepare()
        stages.collect_matching(self.run_dir)
        stages.signature_prepare(self.run_dir)
        stages.reviews_prepare(self.run_dir)

        dispatch = read_json(self.run_dir / "review-dispatch-manifest.json")
        first = self.run_dir / dispatch["entries"][0]["answer"]
        first.parent.mkdir(parents=True, exist_ok=True)
        first.write_text("not-json", encoding="utf-8")
        # Remaining 49 answers stay absent to exercise both invalid and missing paths.

        summary = stages.reviews_collect(self.run_dir)

        self.assertEqual(summary["counts"], {"not_evaluated": 50})
        self.assertEqual(summary["exported_links"], 0)
        self.assertTrue(all(row["decision"] == "not_evaluated" for row in summary["cases"]))
        final_reviews = read_json(self.run_dir / "derived/final-reviews.json")
        self.assertEqual(len(final_reviews), 50)
        self.assertTrue(all(review["verdict"] == "unresolved" for review in final_reviews))
        links = (self.run_dir / "derived/links.jsonl").read_text(encoding="utf-8")
        self.assertEqual(links, "")
        audit = read_json(self.run_dir / "review-collection-audit.json")
        self.assertEqual(len(audit["reviews"]), 50)
        self.assertTrue(all(row["status"] == "not_evaluated" for row in audit["reviews"]))


if __name__ == "__main__":
    unittest.main()
