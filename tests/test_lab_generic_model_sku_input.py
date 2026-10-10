import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "sku-matching"))
import prepare_generic_model_sku_v1 as adapter


class GenericModelSkuInputTests(unittest.TestCase):
    def test_raw_text_keeps_warnings_and_source_refs_and_all_fixed_axes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            au_path, rak_path = root / "au.json", root / "rak.html"
            purchase_path = root / "options.jsonl"
            au_raw = {"itemInfo": {
                "itemComment": "▼ご購入前に確認ください<br>返品はお受けできません。",
                "extraItemComment": "サイズ\n幅100cmは4枚組です。",
                "extraItemCommentWithVideo": "動画の説明です。",
                "detailComment": "返品はお受けできません。<br>別売りフックは含まれません。",
                "detailLinkName": "サイズ表はこちら"}}
            au_path.write_text(json.dumps(au_raw, ensure_ascii=False), encoding="utf-8")
            rak_path.write_text("<html><script>exclude me</script><body>購入してください。<br>カートに入れても在庫は確保されません.<iframe src=\"https://example.invalid/embedded\"></iframe></body></html>", encoding="utf-8")
            purchase_text = "色を選んでください"
            purchase_path.write_text(json.dumps({"au_product_options_raw": {"free_options": [{"title": purchase_text}]}}, ensure_ascii=False) + "\n", encoding="utf-8")
            au_sha = hashlib.sha256(au_path.read_bytes()).hexdigest()
            rak_sha = hashlib.sha256(rak_path.read_bytes()).hexdigest()
            purchase_sha = hashlib.sha256(purchase_path.read_bytes()).hexdigest()
            p = {
                "dossier_id": "d1",
                "au_product": {"product_id": "100", "title": "固定AUタイトル", "title_span": {"quote": "固定AUタイトル"},
                               "raw_file": str(au_path), "sha256": au_sha,
                               "axis_names": {"row": "カラー", "column": "サイズ"}},
                "au_rows": [
                    {"row_key": "r0", "axes": [{"axis_name": "サイズ", "value": "幅100×丈200cm(4枚組)", "value_span": {"quote": "幅100×丈200cm(4枚組)"}}]},
                    {"row_key": "r1", "axes": [{"axis_name": "サイズ", "value": "幅100×丈220cm(4枚組)", "value_span": {"quote": "幅100×丈220cm(4枚組)"}}]},
                ],
                "au_purchase_option_lines": [{"text": purchase_text, "span": {"quote": purchase_text, "raw_file": str(purchase_path),
                    "sha256": purchase_sha, "start": 0, "end": len(purchase_text),
                    "locator": {"kind": "jsonl_leaf", "line": 1, "json_path": "$.au_product_options_raw.free_options[0].title"}}}],
                "rakuten_product": {"raw_file": str(rak_path), "sha256": rak_sha, "encoding": "utf-8",
                                     "url": "https://item.rakuten.co.jp/example/item/", "title": "Rakタイトル",
                                     "title_span": {"quote": "Rakタイトル"}},
            }
            c = {"case_id": "c1", "dossier_id": "d1", "group_id": "g1", "au_product_id": "100",
                 "rakuten_selected": {"url": "https://item.rakuten.co.jp/example/item/", "variant_id": "sku-X",
                    "source_sku_key": "source-X", "axes": [{"axis_index": 0, "axis_key": "サイズ",
                    "axis_label": "サイズ", "value": "幅100×丈220cm(4枚組)", "family_values": ["幅100×丈200cm", "幅100×丈220cm"],
                    "value_span": {"quote": "幅100×丈220cm(4枚組)"}}],
                    "variant_attributes": [{"title": "選択肢", "value": "幅100×丈220cm(4枚組)", "value_span": {"quote": "幅100×丈220cm(4枚組)"}}]}}
            product = adapter.make_product(root, p)
            case = adapter.make_case(c, "legacy")
            c2 = json.loads(json.dumps(c))
            c2["case_id"] = "c2"
            c2["rakuten_selected"]["variant_id"] = "sku-Y"
            c2["rakuten_selected"]["axes"][0]["value"] = "幅100×丈200cm(4枚組)"
            case2 = adapter.make_case(c2, "legacy")

            self.assertEqual(len(product["au"]["rows"]), 2)
            self.assertEqual(product["au"]["rows"][1]["axes"][0]["value"], "幅100×丈220cm(4枚組)")
            self.assertEqual(case["rakuten"]["axes"][0]["family_values"], ["幅100×丈200cm", "幅100×丈220cm"])
            self.assertEqual(case["rakuten"]["variant_id"], "sku-X")
            self.assertEqual(case["rakuten"]["url"], c["rakuten_selected"]["url"])
            self.assertNotEqual(case["rakuten"]["axes"][0]["value"], case2["rakuten"]["axes"][0]["value"])
            self.assertNotEqual(case["rakuten"]["variant_id"], case2["rakuten"]["variant_id"])
            self.assertNotIn("selected_axes", product["rakuten"])
            self.assertNotIn("variant_id", product["rakuten"])
            self.assertNotIn("variant_attributes", product["rakuten"])
            au_text = "\n".join(x["text"] for x in product["au"]["descriptions"])
            rak_text = "\n".join(x["text"] for x in product["rakuten"]["descriptions"])
            self.assertIn("返品はお受けできません", au_text)
            self.assertIn("別売りフックは含まれません", au_text)
            self.assertIn("動画の説明です", au_text)
            self.assertIn("サイズ表はこちら", au_text)
            self.assertIn("カートに入れても在庫は確保されません", rak_text)
            self.assertNotIn("exclude me", rak_text)
            duplicate = next(x for x in product["au"]["descriptions"] if x["text"] == "返品はお受けできません。")
            self.assertEqual(set(duplicate["source_fields"]), {"$.itemInfo.itemComment", "$.itemInfo.detailComment"})
            self.assertEqual(len(duplicate["source_refs"]), 2)
            link_label = next(x for x in product["au"]["descriptions"] if x["text"] == "サイズ表はこちら")
            self.assertEqual(link_label["source_fields"], ["$.itemInfo.detailLinkName"])
            self.assertEqual(product["rakuten"]["descriptions"][0]["source_refs"][0]["raw_file"], str(rak_path))
            self.assertIn("https://example.invalid/embedded", product["rakuten"]["iframe_urls"])
            self.assertEqual(product["au"]["purchase_options"][0]["source_ref"]["quote"], purchase_text)
            self.assertEqual(product["rakuten"]["title_source"]["quote"], "Rakタイトル")

    def test_group_diverse_limit_and_refuse_overwrite(self):
        cases = [{"case_id": str(i), "group_id": str(i % 2)} for i in range(4)]
        picked = adapter.diverse(cases, 3)
        self.assertEqual([x["case_id"] for x in picked], ["0", "1", "2"])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "existing"
            out.mkdir()
            with self.assertRaises(FileExistsError):
                adapter.prepare(Path(tmp), out)

    def test_invalid_purchase_quote_is_not_claimed_as_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "options.jsonl"
            path.write_text(json.dumps({"name": "original"}) + "\n", encoding="utf-8")
            item = {"text": "changed", "span": {"quote": "original", "raw_file": str(path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "start": 0, "end": 8,
                    "locator": {"kind": "jsonl_leaf", "line": 1, "json_path": "$.name"}}}
            self.assertFalse(adapter.valid_purchase_option(root, item))


if __name__ == "__main__":
    unittest.main()
