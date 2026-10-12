import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("canonicalize_source_citation_table_v1.py")
SPEC = importlib.util.spec_from_file_location("citation_table_canonicalizer", MODULE_PATH)
CANON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CANON)


class CitationTableCanonicalizerTests(unittest.TestCase):
    def test_exact_duplicates_only_change_table_and_keep_first_order(self):
        citation = {"citation_id": "c1", "source_sha256": "a" * 64,
                    "quote": "Size: Large", "start": 3, "end": 14, "extra": {"x": 1}}
        response = {"pair_id": "p", "cache_key": "k", "judgments": [
            {"state": "support", "reason": "literal", "citation_ids": ["c1"]},
            {"state": "unknown", "reason": "unresolved", "citation_ids": []}],
            "citation_table": [citation, {"citation_id": "c2", "quote": "other"},
                               copy.deepcopy(citation)],
            "option_vectors": [["x"], ["y"]], "protocol": {"version": 7}}
        original = copy.deepcopy(response)
        result, report = CANON.canonicalize_response(response)
        self.assertEqual(report, {"status": "deduplicated", "removed_entries": 1})
        self.assertEqual(result["citation_table"], [citation, {"citation_id": "c2", "quote": "other"}])
        self.assertEqual({k: v for k, v in result.items() if k != "citation_table"},
                         {k: v for k, v in original.items() if k != "citation_table"})
        self.assertEqual(response, original)

    def test_conflicting_duplicate_id_leaves_entire_response_unchanged(self):
        response = {"judgments": [{"state": "conflict", "citation_ids": ["c"]}],
                    "citation_table": [{"citation_id": "c", "quote": "one"},
                                       {"citation_id": "c", "quote": "two"}]}
        before = copy.deepcopy(response)
        result, report = CANON.canonicalize_response(response)
        self.assertEqual(result, before)
        self.assertEqual(report["status"], "ambiguous_fail_closed")
        self.assertEqual(report["citation_id"], "c")

    def test_equality_covers_sha_quote_and_offsets(self):
        original = {"citation_id": "c", "source_sha256": "a" * 64,
                    "quote": "same", "start": 10, "end": 14}
        variants = [dict(original, source_sha256="b" * 64), dict(original, quote="different"),
                    dict(original, start=11), dict(original, end=15)]
        for variant in variants:
            with self.subTest(variant=variant):
                response = {"citation_table": [original, variant]}
                result, report = CANON.canonicalize_response(response)
                self.assertEqual(result, response)
                self.assertEqual(report["status"], "ambiguous_fail_closed")

    def test_jsonl_cli_writes_receipts_and_refuses_overwrite(self):
        response = {"judgments": [], "citation_table": [
            {"citation_id": "c", "quote": "q"}, {"citation_id": "c", "quote": "q"}]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "input.jsonl", root / "output"
            source.write_text(json.dumps(response) + "\n", encoding="utf-8")
            receipt = CANON.canonicalize_file(source, output)
            saved = json.loads((output / "responses.jsonl").read_text(encoding="utf-8"))
            report = json.loads((output / "change-report.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(saved["citation_table"], [response["citation_table"][0]])
            self.assertEqual(report["removed_entries"], 1)
            self.assertEqual(receipt["input_response_count"], 1)
            with self.assertRaises(FileExistsError):
                CANON.canonicalize_file(source, output)


if __name__ == "__main__":
    unittest.main()
