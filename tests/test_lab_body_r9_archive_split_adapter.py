"""Synthetic reference-routing checks for the DEV10 one-archive adapter."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".lab-output/sku-next-body-invariant-native-execution-20261013-v1/grade_dev10_single_archival_version_adapter_v1.py"
SPEC = importlib.util.spec_from_file_location("body_r9_archive_adapter", SCRIPT)
ADAPTER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(ADAPTER)


def _write_synthetic_archive(path: Path, split: str, case_ids: list[str]) -> None:
    prefix = getattr(ADAPTER.ORIGINAL_SCORE, f"{split.upper()}_LABEL_PREFIX")
    answer_rows = []
    resolved_rows = []
    for index, case_id in enumerate(case_ids, 1):
        key = f"row-{case_id}"
        answer_rows.append({"s": "synthetic label content", "expected": "accept",
                            "au_rows": [key], "data_quality_flag": "synthetic", "rationale": []})
        resolved_rows.append({"case_id": case_id, "expected": "accept",
                              "data_quality_flag": "synthetic", "answer_file": "answers.jsonl",
                              "answer_line": index, "au_row_keys": [key]})
    answer_bytes = b"".join((json.dumps(row, separators=(",", ":")) + "\n").encode() for row in answer_rows)
    resolved_bytes = b"".join((json.dumps(row, separators=(",", ":")) + "\n").encode() for row in resolved_rows)
    digest = lambda data: __import__("hashlib").sha256(data).hexdigest()
    manifest = {"answers_sha256": {"answers.jsonl": digest(answer_bytes)},
                "resolved_sha256": digest(resolved_bytes)}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(prefix + "private/resolved.jsonl", resolved_bytes)
        zf.writestr(prefix + "private/label-manifest.json", json.dumps(manifest).encode())
        zf.writestr(prefix + "answers.jsonl", answer_bytes)
    ADAPTER.PARSER_GLOBALS[f"{split.upper()}_LABEL_ARCHIVE_SHA256"] = digest(path.read_bytes())


class SingleArchivalSplitAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.v2 = self.root / "synthetic-v2.zip"
        self.v3 = self.root / "synthetic-v3.zip"
        self.rows = self.root / "row-plans.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def membership(self, ids, split):
        self.rows.write_text("".join(json.dumps({"case_id": case_id, "au_row_key": f"row-{case_id}"}) + "\n" for case_id in ids))
        return [{"case_id": case_id, "source_version": split} for case_id in ids]

    def test_v2_only_skips_unselected_v3_archive_without_opening_it(self):
        _write_synthetic_archive(self.v2, "v2", ["c2"])
        refs = ADAPTER.ADAPTED_READ_LABEL_ARCHIVES(self.v2, self.root / "does-not-exist-v3.zip",
                                                    self.membership(["c2"], "v2"), self.rows)
        self.assertEqual(set(refs), {"c2"})
        self.assertEqual(refs["c2"]["_reference_split"], "v2")

    def test_v3_only_skips_unselected_v2_archive_without_opening_it(self):
        _write_synthetic_archive(self.v3, "v3", ["c3"])
        refs = ADAPTER.ADAPTED_READ_LABEL_ARCHIVES(self.root / "does-not-exist-v2.zip", self.v3,
                                                    self.membership(["c3"], "v3"), self.rows)
        self.assertEqual(set(refs), {"c3"})
        self.assertEqual(refs["c3"]["_reference_split"], "v3")

    def test_mixed_selected_membership_reads_both_archives(self):
        _write_synthetic_archive(self.v2, "v2", ["c2"])
        _write_synthetic_archive(self.v3, "v3", ["c3"])
        members = self.membership(["c2"], "v2") + self.membership(["c3"], "v3")
        self.rows.write_text("".join(json.dumps({"case_id": c, "au_row_key": f"row-{c}"}) + "\n" for c in ("c2", "c3")))
        refs = ADAPTER.ADAPTED_READ_LABEL_ARCHIVES(self.v2, self.v3, members, self.rows)
        self.assertEqual({k: v["_reference_split"] for k, v in refs.items()}, {"c2": "v2", "c3": "v3"})

    def test_unsupported_source_version_remains_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported source_version"):
            ADAPTER.ADAPTED_READ_LABEL_ARCHIVES(self.v2, self.v3,
                [{"case_id": "bad", "source_version": "v4"}], self.rows)

    def test_membership_and_split_map_must_cover_same_nonempty_cases(self):
        with self.assertRaisesRegex(ValueError, "split map must exactly cover"):
            ADAPTER.PARSER_GLOBALS["load_token_reference_shards"](
                self.v2, self.v3, {"c2"}, {}, {})

    def test_parser_clone_changes_only_empty_split_guard(self):
        source = ADAPTER.inspect.getsource(ADAPTER.ORIGINAL_SCORE.load_token_reference_shards)
        adapted = ADAPTER.ADAPTED_LOADER_SOURCE
        self.assertEqual(source.count("if not selected:"), 1)
        self.assertIn("if not selected:\n            continue", adapted)
        self.assertEqual(source.replace(
            'if not selected:\n            raise ValueError(f"frozen subset has no cases for required split {split}")',
            "if not selected:\n            continue", 1), adapted)

    def test_grader_facade_overrides_only_archive_reader(self):
        self.assertIs(ADAPTER.GRADE.SCORE, ADAPTER.SCORE_FACADE)
        self.assertIs(ADAPTER.SCORE_FACADE.score_variant, ADAPTER.ORIGINAL_SCORE.score_variant)
        self.assertIs(ADAPTER.SCORE_FACADE.canonical_decision, ADAPTER.ORIGINAL_SCORE.canonical_decision)
        self.assertIs(ADAPTER.SCORE_FACADE.canonical_reference, ADAPTER.ORIGINAL_SCORE.canonical_reference)


if __name__ == "__main__":
    unittest.main()
