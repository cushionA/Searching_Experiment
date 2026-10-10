import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "sku-matching"))
import prepare_integrated_sku_inputs_v1 as integrated


class IntegratedSkuInputsV1Tests(unittest.TestCase):
    def test_raw_au_short_heading_and_purchase_exception_are_retained(self):
        with tempfile.TemporaryDirectory(dir=integrated.ROOT) as temp:
            root = Path(temp)
            raw_path = root / "item.json"
            leaf = "サイズ\n幅100cmは2枚になります。臭いによる返品はお受けできませんのでご購入をお願いします。"
            raw_path.write_text(json.dumps({"itemInfo": {"extraItemComment": leaf}}, ensure_ascii=False), encoding="utf-8")
            rel = str(raw_path.relative_to(integrated.ROOT))
            product = {"au_product": {"raw_file": rel, "sha256": integrated.digest(raw_path)},
                       "au_description_lines": []}
            lines = integrated._raw_description_lines(integrated.src.RawStore(integrated.ROOT), product, "au")
            text = "\n".join(x["text"] for x in lines)
            self.assertIn("サイズ", text)
            self.assertIn("ご購入", text)
            self.assertIn("返品はお受けできません", text)
            self.assertTrue(all(integrated.src.verify_span(integrated.src.RawStore(integrated.ROOT), x["span"])
                                for x in lines if x["span"]))

    def test_raw_expected_sha_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=integrated.ROOT) as temp:
            root = Path(temp)
            raw_path = root / "item.json"
            raw_path.write_text(json.dumps({"itemInfo": {"extraItemComment": "サイズ"}}, ensure_ascii=False), encoding="utf-8")
            rel = str(raw_path.relative_to(integrated.ROOT))
            product = {"au_product": {"raw_file": rel, "sha256": "0" * 64}, "au_description_lines": []}
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                integrated._raw_description_lines(integrated.src.RawStore(integrated.ROOT), product, "au")

    def test_rederive_keeps_full_au_rows_and_selected_case_identity_fields(self):
        product = {"au_rows": [{"row_key": "au:123:456:0:0", "axes": [{"axis_name": "サイズ", "value": "幅100cm"}]}],
                   "rakuten_product": {"selector_families": [{"key": "サイズ", "label": "サイズ",
                                                               "values": ["幅100cm", "幅120cm"], "variant_tokens": ["old"]}]},
                   "color_vocab": [], "variant_tokens": []}
        case = {"case_id": "existing-case", "dossier_id": "existing-dossier", "au_product_id": "123",
                "rakuten_selected": {"sku_record_key": "existing-sku", "axes": [{"value": "幅100cm"}]}}
        before = json.loads(json.dumps(case))
        integrated._rederive(product)
        self.assertEqual(product["au_rows"][0]["row_key"], "au:123:456:0:0")
        self.assertEqual(case, before)
        self.assertEqual(product["rakuten_product"]["selector_families"][0]["values"], ["幅100cm", "幅120cm"])

    def test_existing_source_bundles_are_read_only(self):
        paths = [integrated.DEFAULT_LEGACY / "cases.jsonl", integrated.DEFAULT_LEGACY / "products.jsonl",
                 integrated.DEFAULT_FAMILY / "cases.jsonl", integrated.DEFAULT_FAMILY / "products.jsonl",
                 integrated.DEFAULT_NOVEL / "cases.jsonl", integrated.DEFAULT_NOVEL / "products.jsonl"]
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        after = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
