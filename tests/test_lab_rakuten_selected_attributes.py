import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "experiments/sku-matching/extract_rakuten_selected_attributes.py"
SPEC = importlib.util.spec_from_file_location("extract_rakuten_selected_attributes", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RakutenSelectedAttributesTests(unittest.TestCase):
    def test_embedded_selected_sku_attributes_are_cited_and_normalized(self):
        data = {"api": {"data": {"itemInfoSku": {
            "variantSelectors": [{"key": "カラー"}],
            "sku": [{"selectorValues": ["紺"], "merchantDefinedSkuId": "X1", "attributes": [
                {"title": "本体横幅", "value": "50", "unit": "cm"},
                {"title": "本体奥行", "value": "570", "unit": "mm"},
                {"title": "価格", "value": "999"},
                {"title": "独自仕様", "value": "A"}]},
                {"selectorValues": ["赤"], "merchantDefinedSkuId": "X2", "attributes": []}]}}}}
        html = '<script type="application/json" id="item-page-app-data">' + json.dumps(data, ensure_ascii=False) + "</script>"
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "page.html"
            raw.write_text(html, encoding="utf-8")
            import hashlib
            sha = hashlib.sha256(raw.read_bytes()).hexdigest()
            case = {"case_id": "c1", "rakuten": {"source": {"source_row_index": 0},
                    "option_values": [{"axis_key": "カラー", "value": "紺"}]}}
            dossier = {"rakuten_product": {"description": {"source": {
                "raw_file": str(raw), "sha256": sha, "encoding": "utf-8"}}}}
            result = MODULE.extract_selected_attributes(case, dossier)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["page_dimensions"], {"width_cm": 50, "depth_cm": 57})
        self.assertTrue(all("価格" not in str(a) for a in result["selected_attributes"]))
        self.assertEqual(result["selected_attributes"][-1]["status"], "unknown_unparsed_attribute")
        self.assertIn("sku[0].attributes[0]", result["selected_attributes"][0]["source_ref"]["json_path"])

    def test_selected_row_must_match_case_axes(self):
        data = {"api": {"data": {"itemInfoSku": {
            "variantSelectors": [{"key": "カラー"}],
            "sku": [{"selectorValues": ["緑"], "attributes": []}]}}}}
        html = '<script type="application/json" id="item-page-app-data">' + json.dumps(data) + "</script>"
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "page.html"
            raw.write_text(html, encoding="utf-8")
            import hashlib
            case = {"rakuten": {"source": {"source_row_index": 0},
                    "option_values": [{"axis_key": "カラー", "value": "青"}]}}
            dossier = {"rakuten_product": {"description": {"source": {
                "raw_file": str(raw), "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                "encoding": "utf-8"}}}}
            result = MODULE.extract_selected_attributes(case, dossier)
        self.assertEqual(result["status"], "selected_axis_mismatch")
        self.assertEqual(result["selected_attributes"], [])


if __name__ == "__main__":
    unittest.main()
