from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments/sku-matching/trial_cpu_title_evidence.py"
SPEC = importlib.util.spec_from_file_location("cpu_title_evidence_tested", SCRIPT)
assert SPEC and SPEC.loader
extractor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extractor)

SOURCE = ROOT / ".lab-output/sku-cpu-residual-task-20261010-v9/products.jsonl"
MODEL_DIR = ROOT / ".deps/sku-gliner-extract-model"
REQUIRED_MODEL_FILES = (
    "model.safetensors",
    "tokenizer.json",
    "config.json",
    "encoder_config/config.json",
    "tokenizer_config.json",
)
HAS_REAL_SOURCE_INPUTS = (
    SOURCE.is_file()
    and extractor.DEFAULT_RAW_TITLE_SOURCE.is_file()
)
HAS_REAL_SOURCE_AND_MODEL = HAS_REAL_SOURCE_INPUTS and all(
    (MODEL_DIR / name).is_file() for name in REQUIRED_MODEL_FILES
)


class CpuTitleEvidenceTests(unittest.TestCase):
    @unittest.skipUnless(HAS_REAL_SOURCE_INPUTS,
                         "requires the source-backed title input files")
    def test_inputs_keep_only_29_title_and_provenance_records(self):
        products, source_hashes = extractor.read_products(SOURCE)
        self.assertEqual(len(products), 29)
        self.assertEqual(len({item["dossier_id"] for item in products}), 29)
        self.assertTrue(all(item["title_raw"] and item["title_provenance"]["raw_source_ref"]["json_path"] == "$.au_product_title_raw" for item in products))
        self.assertTrue(all("description_blocks" not in item and "au_sku_rows" not in item for item in products))
        self.assertEqual(source_hashes["raw_title_source_sha256"],
                         extractor.sha256_file(extractor.DEFAULT_RAW_TITLE_SOURCE))
        derived = [item for item in products if not item["title_provenance"]["model_input_equals_raw_source_leaf"]]
        self.assertEqual(len(derived), 28)
        self.assertTrue(all(item["title_provenance"]["status"] == "derived_semantic_line_not_verbatim" for item in derived))

    def test_literal_baseline_quotes_are_exact_and_keep_positive_negative_conflict(self):
        title = "遮光カーテン レースカーテンセット レースなし 4枚セット"
        baseline = extractor.literal_baseline(title)
        inclusion = baseline["lace_inclusion"][0]
        exclusion = baseline["lace_exclusion"][0]
        self.assertEqual(title[inclusion["start"]:inclusion["end"]], inclusion["quote"])
        self.assertEqual(title[exclusion["start"]:exclusion["end"]], exclusion["quote"])
        self.assertEqual(baseline["package_count"][0]["quote"], "4枚セット")

    def test_cards_preserve_unknowns_conditions_and_conflicts(self):
        no_claim_title = "遮光カーテン 無地"
        empty_spans = {field: [] for field in extractor.ENTITY_DESCRIPTIONS["ja"]}
        no_claim = extractor.evidence_card(no_claim_title, empty_spans,
                                           extractor.literal_baseline(no_claim_title), "a" * 64)
        self.assertEqual(no_claim["lace"]["status"], "unknown")
        self.assertEqual(no_claim["package_composition"]["status"], "unknown")

        conflict_title = "レースカーテンセット レースなし"
        conflict = extractor.evidence_card(conflict_title, empty_spans,
                                           extractor.literal_baseline(conflict_title), "b" * 64)
        self.assertEqual(conflict["lace"]["status"], "review")
        self.assertTrue(conflict["lace"]["conflict_candidates_present"])

        condition_title = "選べる17サイズ レース付き 2枚セット"
        conditional = extractor.evidence_card(condition_title, empty_spans,
                                              extractor.literal_baseline(condition_title), "c" * 64)
        self.assertEqual(conditional["lace"]["status"], "review")
        self.assertEqual(conditional["package_composition"]["status"], "review")

    def test_model_spans_require_exact_source_substring(self):
        title = "レースカーテンセット 4枚"
        raw = {"entities": {
            "lace_inclusion": [{"text": "レースカーテンセット", "start": 0, "end": 10, "confidence": 0.91}],
            "package_count": [{"text": "4枚", "start": 20, "end": 22, "confidence": 0.8}],
        }}
        spans = extractor.normalize_span_candidates(title, raw, "prefix レースカーテンセット suffix")
        self.assertTrue(spans["lace_inclusion"][0]["literal_span_valid"])
        self.assertEqual(spans["lace_inclusion"][0]["quote"], "レースカーテンセット")
        self.assertTrue(spans["lace_inclusion"][0]["source_leaf_quote_valid"])
        self.assertEqual(spans["lace_inclusion"][0]["quote_provenance"], "verbatim_source_leaf")
        self.assertFalse(spans["package_count"][0]["literal_span_valid"])
        self.assertIsNone(spans["package_count"][0]["quote"])
        self.assertEqual(spans["lace_inclusion"][0]["scope"], "title_only_selected_sku_applicability_unknown")
        derived_only = extractor.normalize_span_candidates(title, raw, "unrelated raw title")
        self.assertTrue(derived_only["lace_inclusion"][0]["literal_span_valid"])
        self.assertFalse(derived_only["lace_inclusion"][0]["source_leaf_quote_valid"])
        self.assertEqual(derived_only["lace_inclusion"][0]["quote_provenance"], "derived_input_title_only")

    @unittest.skipUnless(HAS_REAL_SOURCE_AND_MODEL,
                         "requires the prepared title inputs and pinned local GLiNER snapshot")
    def test_preinference_artifacts_capture_exact_source_and_model_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "prepared"
            products, manifest = extractor.prepare_artifacts(SOURCE, out, SCRIPT, MODEL_DIR)
            self.assertEqual(len(products), 29)
            self.assertEqual(manifest["status"], "prepared_before_inference")
            self.assertEqual(manifest["input_sha256"], extractor.sha256_file(SOURCE))
            self.assertEqual(manifest["source_hashes"]["raw_title_source_sha256"],
                             extractor.sha256_file(extractor.DEFAULT_RAW_TITLE_SOURCE))
            self.assertEqual(manifest["model"]["repo_id"], extractor.MODEL_REPO)
            self.assertEqual(manifest["model"]["revision"], extractor.MODEL_REVISION)
            self.assertEqual(manifest["model"]["verified_files"]["model.safetensors"]["sha256"],
                             "c1ff4ec0bc00031c15530b8f3c33d3677f27949e6a0cb52e1247a6224b6c5395")
            input_snapshot = [json.loads(line) for line in (out / "inputs-title-only.jsonl").read_text().splitlines()]
            self.assertEqual(len(input_snapshot), 29)
            self.assertNotIn("description_blocks", input_snapshot[0])
            self.assertNotIn("au_sku_rows", input_snapshot[0])
            self.assertIn("raw_source_title", input_snapshot[0])
            self.assertIn("raw_source_ref", input_snapshot[0]["title_provenance"])
            self.assertTrue((out / "schema.json").is_file())
            saved_manifest = json.loads((out / "run-manifest.json").read_text())
            self.assertEqual(saved_manifest["status"], "prepared_before_inference")
            self.assertEqual(saved_manifest["code_sha256"], extractor.sha256_file(SCRIPT))


if __name__ == "__main__":
    unittest.main()
