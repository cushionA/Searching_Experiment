"""Synthetic structural tests for product-level condition share eligibility."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKU_DIR = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(SKU_DIR))
SPEC = importlib.util.spec_from_file_location("product_condition_share_prepare", SKU_DIR / "prepare_product_condition_share_v1.py")
PREP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREP)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_rows(path: Path, rows: list[dict]) -> bytes:
    body = b"".join((json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode() for row in rows)
    path.write_bytes(body)
    return body


class ProductConditionSharePrepareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.prepared = self.root / "prepared"
        self.prepared.mkdir()
        self.output = self.root / "output"
        self.document = {"document_id": "doc-1", "document_sha256": "doc-sha", "product_id": "product-1"}
        self.requests = []
        self.tasks = []

    def tearDown(self):
        self.tmp.cleanup()

    def add_request(self, rid, *, selected="wanted-a", direction="rakuten_to_au", axes="カラー：灰"):
        self.requests.append({"request_id": rid, "direction": direction, "target_document_id": "doc-1",
                              "target_document_sha256": "doc-sha", "axis_name": "サイズ",
                              "option_values": ["S", "M"], "selected_value": selected,
                              "target_selected_axes_text": axes})

    def add_task(self, tid, rid, origin="one_sided", *, value="M", row="row-1"):
        self.tasks.append({"task_id": tid, "request_id": rid, "direction": "rakuten_to_au",
                           "origin": origin, "case_id": "case-1", "au_row_key": row,
                           "axis_key": "サイズ", "raw_condition": {"value": value},
                           "source_ref": {"path": "source.jsonl", "line": 1}})

    def freeze(self, schema_version="fixture-v1"):
        files = {"requests.jsonl": write_rows(self.prepared / "requests.jsonl", self.requests),
                 "documents.jsonl": write_rows(self.prepared / "documents.jsonl", [self.document]),
                 "tasks.jsonl": write_rows(self.prepared / "tasks.jsonl", self.tasks)}
        manifest = {"schema_version": schema_version, "output_sha256": {k: digest(v) for k, v in files.items()}}
        if schema_version == PREP.MIXED_SCHEMA:
            manifest["row_policy_audit"] = dict(PREP.MIXED_ROW_POLICY_AUDIT)
        (self.prepared / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_only_all_one_sided_tasks_eligible_and_wanted_value_not_group_key(self):
        self.add_request("r1", selected="S")
        self.add_request("r2", selected="M")
        self.add_request("r3")
        self.add_task("t1", "r1")
        self.add_task("t2", "r2")
        self.add_task("t3a", "r3", "one_sided")
        self.add_task("t3b", "r3", "model_candidate")
        self.freeze()
        result = PREP.prepare(self.prepared, self.output)
        counts = result["structural_savings"]
        self.assertEqual(result["eligible_forward_request_ids"], ["r1", "r2"])
        self.assertEqual([proof["task_proofs"][0]["task_id"] for proof in result["source_proof"]], ["t1", "t2"])
        self.assertEqual(counts["eligible_forward_task_fanout"], 2)
        self.assertEqual(counts["old_exact_input_groups"], 1)
        self.assertEqual(counts["product_level_groups"], 1)
        self.assertEqual(counts["option_label_pairs_old"], 2)
        self.assertEqual(counts["jev_labels_with_one_unknown_product_level"], 3)
        self.assertEqual(result["sold_out_rows_excluded_upstream"], True)
        self.assertIn("sold-out SKU rows excluded upstream", result["statement"])

    def test_reverse_requests_pass_through_without_becoming_eligible(self):
        self.add_request("forward")
        self.add_task("tf", "forward")
        self.add_request("reverse", direction="au_to_rakuten")
        self.freeze()
        result = PREP.prepare(self.prepared, self.output)
        self.assertEqual(result["eligible_forward_request_ids"], ["forward"])
        self.assertEqual(result["pass_through"]["reverse_request_ids"], ["reverse"])

    def test_mixed_gpu_schema_reports_sold_out_rows_retained_as_audit_only(self):
        self.add_request("mixed")
        self.add_task("tm", "mixed")
        self.freeze(schema_version="mixed-real-gpu-sku-inputs-v1")
        result = PREP.prepare(self.prepared, self.output)
        self.assertFalse(result["sold_out_rows_excluded_upstream"])
        self.assertEqual(result["row_policy_audit"], PREP.MIXED_ROW_POLICY_AUDIT)
        self.assertIn("including sold-out rows, are retained", result["statement"])
        self.assertIn("only Rakuten one_sided with null AU axis is eligible to share", result["statement"])
        self.assertEqual([proof["task_proofs"][0]["task_id"] for proof in result["source_proof"]], ["tm"])

    def test_upstream_residual_requires_exact_one_sided_rakuten_null_axis_proof(self):
        self.add_request("novel-good")
        self.add_request("novel-bad")
        self.add_task("tg", "novel-good", "upstream_residual", value="M", row="row-1")
        self.add_task("tb", "novel-bad", "upstream_residual", value="S", row="row-2")
        self.freeze()
        novel = self.root / "novel"
        novel.mkdir()
        conditions = [
            {"case_id": "case-1", "row_key": "row-1", "axis_key": "サイズ", "value": "M",
             "kind": "rakuten", "status": "one_sided", "au_axis": None},
            {"case_id": "case-1", "row_key": "row-2", "axis_key": "サイズ", "value": "S",
             "kind": "rakuten", "status": "one_sided", "au_axis": "サイズ"},
        ]
        body = write_rows(novel / "upstream_conditions.jsonl", conditions)
        (novel / "manifest.json").write_text(json.dumps({"schema_version": "novel-v1",
            "output_sha256": {"upstream_conditions.jsonl": digest(body)}}), encoding="utf-8")
        result = PREP.prepare(self.prepared, self.output, novel)
        self.assertEqual(result["eligible_forward_request_ids"], ["novel-good"])
        proof = result["source_proof"][0]["task_proofs"][0]
        self.assertIn("condition_sha256", proof)
        self.assertEqual(proof["condition_ref"]["line"], 1)

    def test_novel_manifest_sha_mismatch_fails_closed(self):
        self.add_request("novel")
        self.add_task("tn", "novel", "upstream_residual")
        self.freeze()
        novel = self.root / "novel"
        novel.mkdir()
        body = write_rows(novel / "upstream_conditions.jsonl", [])
        (novel / "manifest.json").write_text(json.dumps({"schema_version": "novel-v1",
            "output_sha256": {"upstream_conditions.jsonl": "wrong-sha"}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA differs"):
            PREP.prepare(self.prepared, self.output, novel)

    def test_prepared_output_sha_mismatch_fails_closed(self):
        self.add_request("r")
        self.add_task("t", "r")
        self.freeze()
        (self.prepared / "tasks.jsonl").write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA differs"):
            PREP.prepare(self.prepared, self.output)

    def test_output_dir_is_never_overwritten(self):
        self.add_request("r")
        self.add_task("t", "r")
        self.freeze()
        PREP.prepare(self.prepared, self.output)
        with self.assertRaises(FileExistsError):
            PREP.prepare(self.prepared, self.output)


if __name__ == "__main__":
    unittest.main()
