"""Source-contract tests only; synthetic fixtures are not performance data."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKU_DIR = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(SKU_DIR))
SPEC = importlib.util.spec_from_file_location("novel_condition_prepare", SKU_DIR / "prepare_novel_condition_choices_v1.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class NovelInputSourceContracts(unittest.TestCase):
    def frozen(self, directory, prefix="", **flags):
        directory.mkdir()
        files = {"cases.jsonl": b'{"case_id":"synthetic-case"}\n', "products.jsonl": b'{}\n'}
        for name, body in files.items():
            (directory / name).write_bytes(body)
        manifest = {"synthetic": False, "labels_read": False, **flags,
                    "output_sha256": {prefix + name: RUNNER.sha(body) for name, body in files.items()}}
        (directory / "manifest.json").write_text(json.dumps(manifest))
        return manifest

    def test_generic_and_integrated_source_manifest_keys_both_resolve(self):
        with tempfile.TemporaryDirectory() as temp:
            for prefix in ("", "inputs/"):
                directory = Path(temp) / ("integrated" if prefix else "generic")
                self.frozen(directory, prefix)
                self.assertIs(RUNNER.freeze_inputs(directory)["labels_read"], False)

    def test_mutated_source_bytes_are_rejected_before_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "source"
            self.frozen(directory)
            (directory / "cases.jsonl").write_bytes(b'{"case_id":"changed"}\n')
            with self.assertRaisesRegex(ValueError, "SHA differs"):
                RUNNER.freeze_inputs(directory)

    def test_missing_file_hash_cannot_pass_freeze(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "source"
            manifest = self.frozen(directory)
            del manifest["output_sha256"]["products.jsonl"]
            (directory / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "SHA differs"):
                RUNNER.freeze_inputs(directory)

    def test_synthetic_or_label_consuming_inputs_cannot_be_staged(self):
        with tempfile.TemporaryDirectory() as temp:
            for number, flags in enumerate(({"synthetic": True}, {"labels_read": True}, {"synthetic": None})):
                directory = Path(temp) / str(number)
                self.frozen(directory, **flags)
                with self.assertRaisesRegex(ValueError, "real, label-free"):
                    RUNNER.freeze_inputs(directory)

    def test_nested_original_raw_references_preserve_whole_filenames_and_hashes(self):
        value = {"axes": [{"value": "幅１００×８０cm / レースあり", "value_span": {
            "raw_file": "原本/選択肢.json", "sha256": "a" * 64}}], "title": {
            "source_ref": {"raw_file": "原本/商品.json", "sha256": "b" * 64}}}
        original = json.dumps(value, ensure_ascii=False)
        self.assertEqual(RUNNER.raw_references(value), {"原本/選択肢.json": "a" * 64, "原本/商品.json": "b" * 64})
        self.assertEqual(json.dumps(value, ensure_ascii=False), original)

    def test_same_raw_source_with_conflicting_hashes_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "contradictory raw SHA"):
            RUNNER.raw_references([{"raw_file": "raw.json", "sha256": "a" * 64},
                                   {"raw_file": "raw.json", "sha256": "b" * 64}])

    def test_unpinned_producer_code_is_rejected_without_importing_it(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "producer.py"
            path.write_text('raise AssertionError("must not import unpinned code")\n')
            with self.assertRaisesRegex(ValueError, "pinned producer SHA"):
                RUNNER.load_producer(path)

    def test_existing_stage_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            with self.assertRaises(FileExistsError):
                RUNNER.stage(path / "missing", path / "missing", path / "missing.py", path)


if __name__ == "__main__":
    unittest.main()
