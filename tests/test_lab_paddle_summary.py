"""Audit checks for raw OCR answers, artifact integrity and weighted source metrics."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments/captcha-small-model"
spec = importlib.util.spec_from_file_location("paddle_summary", EXPERIMENT / "summarize_paddle_text.py")
module = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(EXPERIMENT))
try:
    spec.loader.exec_module(module)
finally:
    sys.path.remove(str(EXPERIMENT))


def sample(index, label, source="source-a"):
    return {"id": str(index), "path": f"fixture-{index}.png", "source": source,
            "sha256": f"{index:064x}", "label": label}


def prediction(item, answer, *, latency=1.0, error=None):
    return {**item, "answer": answer, "exact": answer == item["label"],
            "case_insensitive_exact": answer.casefold() == item["label"].casefold(),
            "distance": module.levenshtein(answer, item["label"]), "characters": len(item["label"]),
            "latency_ms": latency, "confidence": 0.01 if error is None else None, "error": error}


class PaddleSummaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "predictions.jsonl"

    def write_rows(self, rows):
        self.path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    def test_raw_case_and_whitespace_are_preserved_in_the_score(self):
        samples = [sample(1, "Ab9"), sample(2, " A "), sample(3, "0B")]
        rows = [prediction(item, answer) for item, answer in zip(samples, ("ab9", "A", "0b"), strict=True)]
        self.write_rows(rows)
        verified = module.verify_rows(self.path, samples)
        self.assertEqual([row["answer"] for row in verified], ["ab9", "A", "0b"])
        metrics = module.aggregate(verified)
        self.assertEqual(metrics["literal_exact_count"], 0)
        self.assertEqual(metrics["case_insensitive_exact_count"], 2)
        self.assertEqual(metrics["edit_distance_total"], 4)
        self.assertEqual(metrics["reference_characters"], 8)
        self.assertEqual(metrics["character_error_rate"], 0.5)

    def test_verification_rejects_changed_source_claimed_exact_and_nonfinite_timing(self):
        item = sample(1, "Ab9")
        original = prediction(item, "ab9")
        for changes in ({"source": "other-source"}, {"exact": True}, {"latency_ms": float("nan")}):
            with self.subTest(changes=changes):
                tampered = copy.deepcopy(original)
                tampered.update(changes)
                self.write_rows([tampered])
                with self.assertRaises(ValueError):
                    module.verify_rows(self.path, [item])
        self.write_rows([])
        with self.assertRaisesRegex(ValueError, "incomplete predictions"):
            module.verify_rows(self.path, [item])

    def test_source_metrics_and_overall_weighting_include_failed_empty_predictions(self):
        samples = [sample(1, "ABCD"), sample(2, "X", "source-b"),
                   sample(3, "Y", "source-b"), sample(4, "Z", "source-b")]
        failure = {"stage": "image_open", "type": "UnidentifiedImageError", "message": "fixture decode failed"}
        rows = [prediction(samples[0], "ABCE", latency=20.0),
                prediction(samples[1], "X", latency=2.0),
                prediction(samples[2], "Y", latency=4.0),
                prediction(samples[3], "", latency=6.0, error=failure)]
        self.write_rows(rows)
        verified = module.verify_rows(self.path, samples)
        overall = module.aggregate(verified)
        sources = {source: module.aggregate([row for row in verified if row["source"] == source])
                   for source in ("source-a", "source-b")}
        self.assertEqual(sources["source-a"]["literal_exact_match"], 0)
        self.assertEqual(sources["source-a"]["character_error_rate"], 1 / 4)
        self.assertEqual(sources["source-b"]["literal_exact_match"], 2 / 3)
        self.assertEqual(sources["source-b"]["character_error_rate"], 1 / 3)
        self.assertEqual(overall["literal_exact_match"], 1 / 2)
        self.assertEqual(overall["character_error_rate"], 2 / 7)
        self.assertEqual(overall["latency"]["mean_ms"], 8)
        self.assertEqual(overall["failed_samples"], 1)
        self.assertEqual(overall["empty_answers"], 1)


if __name__ == "__main__":
    unittest.main()
