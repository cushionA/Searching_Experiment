import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments" / "sku-matching"))
import audit_real_capture


class RealCaptureAuditTests(unittest.TestCase):
    def write_jsonl(self, path: Path, rows: list[dict]) -> None:
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def test_audits_observed_matrix_rows_and_keeps_null_stock_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            au = root / "au-real"
            raku = root / "raku-real"
            au.mkdir()
            raku.mkdir()
            raw = (b'{"itemInfo":{"skuInfo":{"rowNames":["red"],"columnNames":["M"],'
                   b'"stockList":[[{"isSoldOut":false,"isShortStock":false,"remainingStock":null,'
                   b'"shippingScheduleText":null}]]}}}')
            (au / "1-item.json").write_bytes(raw)
            (au / "1-options.json").write_bytes(b"{}")
            self.write_jsonl(au / "products.jsonl", [{
                "item_id": "1", "item_api": {"sha256": hashlib.sha256(raw).hexdigest()},
                "options_api": {"sha256": hashlib.sha256(b"{}").hexdigest()},
                "current_price": 1200, "current_price_json_type": "int",
                "sku": {"row_names": ["red"], "column_names": ["M"],
                        "stock_list_shape": [1, [1]], "combination_count": 1},
            }])
            self.write_jsonl(au / "skus.jsonl", [{
                "item_id": "1", "row_index": 0, "column_index": 0,
                "row_option_value": "red", "column_option_value": "M",
                "stock": {"isSoldOut": False, "isShortStock": False,
                          "remainingStock": None, "shippingScheduleText": None},
            }])
            self.write_jsonl(raku / "products.jsonl", [])
            self.write_jsonl(raku / "skus.jsonl", [])
            result = audit_real_capture.audit_au([au])
            self.assertEqual(result["counts"]["raw_source_expected_matrix_rows"], 1)
            self.assertEqual(result["stock_source_value_counts"]["null_remainingStock"], 1)
            self.assertEqual(result["raw_integrity"]["sha256_match"], 2)
            self.assertEqual(result["errors"], [])

    def test_detects_rakuten_sku_id_collision_without_dropping_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture = root / "raku-real"
            capture.mkdir()
            raw = (b'<script type="application/json" id="item-page-app-data">'
                   b'{"api":{"data":{"itemInfoSku":{"variantSelectors":[{"key":"size","name":null,'
                   b'"label":"size","values":[{"value":"S","label":"S"},{"value":"L","label":"L"}]}],'
                   b'"sku":[{"merchantDefinedSkuId":"merchant-id","variantId":"variant-1",'
                   b'"selectorValues":["S"]},{"merchantDefinedSkuId":"merchant-id",'
                   b'"variantId":"variant-2","selectorValues":["L"]}]}}}}</script>')
            raw_path = root / "page.html"
            raw_path.write_bytes(raw)
            raw_ref = str(raw_path)
            url = "https://item.rakuten.co.jp/store/item/"
            self.write_jsonl(capture / "products.jsonl", [{
                "source_url": url, "raw_file": raw_ref, "sha256": hashlib.sha256(raw).hexdigest(),
                "sku_count": 2, "axes": [{"key": "size", "name": None, "label": "size",
                                            "values": [{"value": "S", "label": "S"},
                                                       {"value": "L", "label": "L"}]}],
            }])
            shared = {"source_url": url, "sku_id": "merchant-id", "merchant_defined_sku_id": "merchant-id",
                      "inventory_display_setting": "HIDDEN_STOCK", "visible_availability": "unknown_hidden_stock_display",
                      "option_values": [{"axis_key": "size", "value": "S"}]}
            self.write_jsonl(capture / "skus.jsonl", [
                {**shared, "variant_id": "variant-1"},
                {**shared, "variant_id": "variant-2", "option_values": [{"axis_key": "size", "value": "L"}]},
            ])
            result = audit_real_capture.audit_rakuten([capture])
            self.assertEqual(result["counts"]["sku_rows"], 2)
            collisions = [e for e in result["errors"] if e["code"] == "duplicate_rakuten_source_sku_key"]
            self.assertEqual(len(collisions), 1)
            self.assertEqual(collisions[0]["first_variant_id"], "variant-1")
            self.assertEqual(collisions[0]["duplicate_variant_id"], "variant-2")

    def test_rejects_forbidden_synthetic_capture_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / "sku-synthetic-case"
            capture.mkdir()
            (capture / "products.jsonl").write_text("", encoding="utf-8")
            (capture / "skus.jsonl").write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                audit_real_capture.validate_dirs([capture], "au")


if __name__ == "__main__":
    unittest.main()
