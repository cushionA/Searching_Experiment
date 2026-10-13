"""Export privacy, fixed versions, notebook validity, and preview guard."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

CODE = Path(__file__).resolve().parents[1] / "experiments/sku-matching"
sys.path.insert(0, str(CODE))
import prepare_nextdata_bounded_notebook_v1 as B


class BoundedNotebookTests(unittest.TestCase):
    def config(self):
        return {"dataset_ref": B.DATASET_REF, "dataset_version": 2,
                "kernel_ref": "superbigzabuton/sku-nextdata-table-fp32-20261013-resume-s000", "kernel_version": 1,
                "shard_id": 0, "planning_preview": False, "archive_sha256": "a" * 64, "archive_bytes": 1234,
                "export_manifest_sha256": "b" * 64, "original_bundle_manifest_sha256": B.R.ORIGINAL_BUNDLE_SHA,
                "state_bundle_manifest_sha256": "c" * 64}

    def bootstrap_namespace(self, config):
        notebook = B.render_notebook(config)
        source = "".join(notebook["cells"][0]["source"])
        module = ast.parse(source)
        self.assertIsInstance(module.body[-1], ast.Expr)
        self.assertEqual(module.body[-1].value.func.id, "main")
        module.body.pop()
        namespace = {}
        exec(compile(module, "bootstrap-fixture", "exec"), namespace)
        return namespace

    def test_valid_notebook_preserves_kernel_and_dataset_version_pins(self):
        config = self.config()
        notebook = B.render_notebook(config)
        self.assertEqual(notebook["metadata"]["kernelspec"]["name"], "python3")
        self.assertEqual(notebook["metadata"]["language_info"]["name"], "python")
        self.assertTrue(notebook["cells"][0]["id"])
        self.assertEqual(notebook["cells"][0]["outputs"], [])
        self.assertLess(B.push_payload_bytes(notebook), 1024 * 1024)
        namespace = self.bootstrap_namespace(config)
        self.assertEqual(namespace["CONFIG"], config)
        source = "".join(notebook["cells"][0]["source"])
        self.assertIn("--max-seconds", source)
        self.assertIn('"6000"', source)
        self.assertNotIn("KAGGLE_API_TOKEN", source)

    def test_planning_preview_refuses_before_pip_gpu_or_source_stage(self):
        config = self.config()
        config["planning_preview"] = True
        namespace = self.bootstrap_namespace(config)
        with patch.object(namespace["subprocess"], "run", side_effect=AssertionError("preview executed a subprocess")):
            with self.assertRaisesRegex(RuntimeError, "planning preview"):
                namespace["main"]()

    def test_old_kernel_or_dataset_version_cannot_be_reused(self):
        B.validate_intent(self.config()["kernel_ref"], 1, 2)
        for ref, version, data_version in ((B.R.ORIGINAL_REF, 3, 2), (self.config()["kernel_ref"], 0, 2),
                                          (self.config()["kernel_ref"], 1, 1), ("other-owner/sku-resume", 1, 2)):
            with self.assertRaises(ValueError):
                B.validate_intent(ref, version, data_version)

    def test_archive_rejects_traversal_symlinks_duplicates_and_backslashes(self):
        for name in ("../credentials.env", "/absolute", "worker-state\\index.sqlite"):
            info = zipfile.ZipInfo(name)
            with self.assertRaises(ValueError):
                B.safe_archive_member(info)
            with self.assertRaises(ValueError):
                self.bootstrap_namespace(self.config())["safe_member"](info)
        link = zipfile.ZipInfo("worker-state/link")
        link.external_attr = 0o120777 << 16
        with self.assertRaises(ValueError):
            B.safe_archive_member(link)

    def test_unexpected_private_table_and_score_fields_block_export(self):
        db = sqlite3.connect(":memory:")
        for table, columns in B.DB_COLUMNS.items():
            db.execute("CREATE TABLE " + table + " (" + ",".join(c + " TEXT" for c in columns) + ")")
        try:
            with patch.object(B.R, "verify_index_identity"):
                B.audit_index_export(db, planning_preview=True)
                db.execute("CREATE TABLE private_case_gold (case_id TEXT, label TEXT)")
                with self.assertRaises(ValueError):
                    B.audit_index_export(db, planning_preview=True)
                db.execute("DROP TABLE private_case_gold")
                db.execute("VACUUM")
                score = {k: None for k in B.SCORE_FIELDS["nli"]}
                score["gold_label"] = "do-not-export"
                origin = {"type": "original_v2", "remote_ref": B.R.ORIGINAL_REF, "version": 2,
                          "file_sha256": "d" * 64, "line_number": 1}
                db.execute("INSERT INTO cache VALUES (?,?,?,?)", ("nli", "key", json.dumps(score), json.dumps(origin)))
                with self.assertRaises(ValueError):
                    B.audit_index_export(db, planning_preview=True)
        finally:
            db.close()

    def test_export_marker_pins_inventory_and_bytes_and_preserves_partial_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "original").mkdir()
            source = root / "original/BUNDLE_MANIFEST.json"
            source.write_bytes(b"frozen-source\n")
            config = self.config()
            marker = {"dataset_ref": config["dataset_ref"], "dataset_version": 2,
                      "kernel_ref": config["kernel_ref"], "kernel_version": 1,
                      "shard_id": 0, "max_seconds": 6000, "labels_included": False,
                      "private_source_joins_included": False, "raw_pages_included": False,
                      "credentials_included": False, "planning_preview": False,
                      "files": {"original/BUNDLE_MANIFEST.json": {"size_bytes": source.stat().st_size,
                                "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}}}
            marker_path = root / B.EXPORT_NAME
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            config["export_manifest_sha256"] = B.sha(marker_path)
            namespace = self.bootstrap_namespace(config)
            self.assertEqual(namespace["verify_export"](root), marker)
            (root / "private-product-closure.json").write_text("private", encoding="utf-8")
            with self.assertRaises(ValueError):
                namespace["verify_export"](root)
            (root / "private-product-closure.json").unlink()
            source.write_bytes(b"changed-source\n")
            with self.assertRaises(ValueError):
                namespace["verify_export"](root)
            text = "".join(B.render_notebook(config)["cells"][0]["source"])
            self.assertIn("finally:", text)
            self.assertIn("preserve_output(output, returncode, started)", text)
            self.assertNotIn("shutil.rmtree(output)", text)

    def test_actual_zip_bytes_must_match_independent_frozen_member_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.zip"
            data = b"exact-public-input"
            with zipfile.ZipFile(path, "x") as archive:
                archive.writestr("original/input.jsonl", data)
            files = {"original/input.jsonl": {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}}
            B.verify_export_archive(path, files)
            wrong_hash = copy.deepcopy(files)
            wrong_hash["original/input.jsonl"]["sha256"] = hashlib.sha256(b"other-public-input").hexdigest()
            with self.assertRaises(ValueError):
                B.verify_export_archive(path, wrong_hash)
            with self.assertRaises(ValueError):
                B.verify_export_archive(path, {**files, "private-closure.json": {"size_bytes": 0, "sha256": "a" * 64}})


if __name__ == "__main__":
    unittest.main()
