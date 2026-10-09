"""Focused checks for payload integrity and observed SKU row grain."""
import hashlib
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sku_audit", ROOT / "experiments/sku-matching/audit_snapshot.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class SkuAuditTests(unittest.TestCase):
    def test_payload_hash_and_byte_count_must_match_manifest(self):
        body = b'{"sku":1}\n'
        manifest_entry = {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        observed = audit.verify_payload_entry("skus.jsonl", body, manifest_entry)
        self.assertTrue(observed["matches_embedded_manifest"])

        tampered_entry = dict(manifest_entry, sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "manifest mismatch: skus.jsonl"):
            audit.verify_payload_entry("skus.jsonl", body, tampered_entry)

    def test_duplicate_au_matrix_cell_is_rejected_even_if_sku_ids_differ(self):
        products = [{"item_id": "p1", "sku": {"option_name": {"row": "color", "column": "size"}, "rowCount": 1, "columnCount": 1}}]
        skus = [
            {"item_id": "p1", "sku_id": 11, "row_index": 0, "column_index": 0},
            {"item_id": "p1", "sku_id": 12, "row_index": 0, "column_index": 0},
        ]
        with self.assertRaisesRegex(ValueError, "duplicate row-grain key in au_fixture: 1"):
            audit.table_audit(products, skus, "au_fixture")

    def test_au_snake_case_axis_counts_match_observed_option_cells(self):
        products = [{"item_id": "p1", "sku": {
            "option_name": {"row": "サイズ", "column": "色"},
            "row_count": 1,
            "column_count": 10,
        }}]
        skus = [
            {"item_id": "p1", "sku_id": i, "row_index": 0, "column_index": i - 1}
            for i in range(1, 11)
        ]
        result = audit.table_audit(products, skus, "au_fixture")
        self.assertEqual(result["sku_axes_by_role"], {"row": ["サイズ"], "column": ["色"]})
        self.assertEqual(result["row_column_shape_by_product"]["p1"], {
            "rows": 1, "columns": 10, "cells": 10, "observed_sku_rows": 10,
        })

    def test_shared_rakuten_page_hash_does_not_make_distinct_skus_duplicates(self):
        products = [{"source_url": "https://example.test/item/"}]
        skus = [
            {"source_url": "https://example.test/item/", "sku_id": "a", "sha256": "page-hash"},
            {"source_url": "https://example.test/item/", "sku_id": "b", "sha256": "page-hash"},
        ]
        result = audit.table_audit(products, skus, "rakuten_fixture")
        self.assertEqual(result["sku_rows"], 2)
        self.assertEqual(result["source_sha256_unique_count"], 1)
        self.assertEqual(result["source_sha256_reused_sku_rows"], 1)


if __name__ == "__main__":
    unittest.main()
