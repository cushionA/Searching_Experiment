import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "experiments/sku-matching/package_luna_real_evaluation.py"
SPEC = importlib.util.spec_from_file_location("package_luna_real_evaluation", MODULE_PATH)
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class LunaRealPackageTests(unittest.TestCase):
    def test_case_and_unique_sku_counts_are_not_conflated(self):
        cases = package.read_jsonl(package.INPUTS / "cases.jsonl")
        counts = package.case_metrics(cases)
        self.assertEqual(counts["case_count"], 1383)
        self.assertEqual(counts["unique_case_id_count"], 1383)
        self.assertEqual(counts["unique_rakuten_sku_record_count"], 644)
        self.assertEqual(counts["rakuten_sku_case_references"], 1383)
        self.assertEqual(counts["rakuten_sku_references_repeated_across_cases"], 739)
        self.assertEqual(counts["family_group_count"], 12)

    def test_active_label_allowlist_excludes_candidate_and_revision_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            labels = pathlib.Path(temp)
            names = [*package.ACTIVE_LABEL_NAMES, "labels-candidate-v1.jsonl",
                     "old-revision/shard-1-labels.jsonl"]
            for name in names:
                path = labels / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
            selected = package.active_label_paths(labels)
            self.assertEqual({p.name for p in selected}, set(package.ACTIVE_LABEL_NAMES))
            self.assertTrue(all("candidate" not in p.name for p in selected))
            self.assertIn("shard-2-audit-corrections.jsonl", {p.name for p in selected})

    def test_package_name_guard_allows_only_active_correction_ledger(self):
        active = (package.LABELS_DIR.relative_to(ROOT).as_posix()
                  + "/shard-2-audit-corrections.jsonl")
        package.validate_annotation_artifact_names({active, "labels/labels.jsonl"})
        for forbidden in (
            "labels/labels-candidate-v2.jsonl",
            "labels/shard-2-diff-audit.jsonl",
            "labels/archive/shard-2-audit-corrections.jsonl",
        ):
            with self.subTest(forbidden=forbidden):
                with self.assertRaisesRegex(ValueError, "artifact|ledger"):
                    package.validate_annotation_artifact_names({forbidden})

    def test_model_outputs_pin_current_code_and_manifest_hashes(self):
        payloads, summaries = package.collect_model_outputs()
        self.assertEqual(set(summaries), set(package.MODELS))
        self.assertEqual(len(payloads), len(package.MODELS) * len(package.MODEL_OUTPUT_NAMES))

    def test_decide_and_derived_reports_are_frozen_and_source_pinned(self):
        decide_payloads, decide = package.collect_decide_outputs(package.DECIDE_EXCLUSIVE_DIR)
        self.assertEqual(len(decide_payloads), 1)
        self.assertEqual(decide["sampling"]["selected_case_count"], 166)
        derived_payloads, derived = package.collect_derived_model_summaries()
        self.assertEqual({path.name for path in derived_payloads.values()},
                         {"comparison.json", "curtain-only.json"})
        self.assertEqual(derived["curtain_only"]["case_count"], 720)

    def test_synthetic_payload_is_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            payload = pathlib.Path(temp) / "case.jsonl"
            original = json.dumps({"case_id": "case-real", "record_kind": "SYNTHETIC"}) + "\n"
            payload.write_text(original, encoding="utf-8")
            before = payload.read_bytes()
            with self.assertRaisesRegex(ValueError, "synthetic provenance"):
                package.validate_structured_payload(payload)
            self.assertEqual(payload.read_bytes(), before)

    def test_payload_snapshot_detects_source_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            source = pathlib.Path(temp) / "cases.jsonl"
            source.write_text('{"case_id":"case-real"}\n', encoding="utf-8")
            payloads = {"cases.jsonl": source}
            snapshot = package.snapshot_payloads(payloads)
            package.assert_payloads_unchanged(payloads, snapshot)
            source.write_text('{"case_id":"case-changed"}\n', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "changed during"):
                package.assert_payloads_unchanged(payloads, snapshot)


if __name__ == "__main__":
    unittest.main()
