import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import prepare_gpu_sku_trial_v2 as builder

INPUTS = ROOT / builder.DEFAULT_INPUTS
MANIFEST = ROOT / builder.DEFAULT_INPUT_MANIFEST
PRODUCTS = ROOT / builder.DEFAULT_PRODUCTS
DOSSIERS = ROOT / builder.DEFAULT_DOSSIERS
SOURCE_READY = all(p.exists() for p in (INPUTS, MANIFEST, PRODUCTS, DOSSIERS))


class PrepareGpuSkuTrialV2Tests(unittest.TestCase):
    def test_axis_serialization_preserves_compound_values_and_slashes(self):
        self.assertEqual(builder.row_sku([
            {"axis_name_raw": "枚数", "value_raw": "21枚セット(パネル20枚＋ドア1枚)"},
            {"axis_name_raw": "タイプ", "value_raw": "スリム / グレー"},
        ]), "枚数=21枚セット(パネル20枚＋ドア1枚) / タイプ=スリム / グレー")

    def test_jsonpath_resolution_is_leaf_exact(self):
        self.assertEqual(builder.json_path_value({"itemInfo":{"itemTitle":"原本"}}, "$.itemInfo.itemTitle"), "原本")
        with self.assertRaises(ValueError):
            builder.json_path_value({}, "$.itemInfo.itemTitle")

    @unittest.skipUnless(SOURCE_READY, "real frozen source package is not present in this checkout")
    def test_full_cohort_rebuild_preserves_ids_skus_rows_and_fixed_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle"
            manifest = builder.build(root=ROOT, output_dir=out, inputs_path=INPUTS,
                input_manifest_path=MANIFEST, products_path=PRODUCTS, dossiers_dir=DOSSIERS)
            old = builder.jsonl(INPUTS)
            new = builder.jsonl(out / "inputs.jsonl")
            side = builder.jsonl(out / "source-cases.jsonl")
            self.assertEqual(manifest["input_count"], 196)
            self.assertEqual([x["case_id"] for x in new], [x["case_id"] for x in old])
            self.assertEqual([x["dossier_id"] for x in new], [x["dossier_id"] for x in old])
            self.assertEqual([x["rakuten"]["sku"] for x in new], [x["rakuten"]["sku"] for x in old])
            self.assertEqual([[r["row_key"] for r in x["au"]["sku_rows"]] for x in new],
                             [[r["row_key"] for r in x["au"]["sku_rows"]] for x in old])
            self.assertEqual([[r["sku"] for r in x["au"]["sku_rows"]] for x in new],
                             [[r["sku"] for r in x["au"]["sku_rows"]] for x in old])
            self.assertTrue(all(s["fixed_sources"]["au_url"].startswith("https://wowma.jp/item/") for s in side))
            self.assertTrue(all(s["fixed_sources"]["rakuten_url"].startswith("https://item.rakuten.co.jp/") for s in side))
            for c, s in zip(new, side):
                self.assertEqual(s["fixed_sources"]["full_au_row_count"], len(c["au"]["sku_rows"]))
                self.assertEqual(s["fixed_sources"]["au_row_key_order"], [r["row_key"] for r in c["au"]["sku_rows"]])
                self.assertTrue(all("source_ref" in e and e["quote"] for e in c["evidence_registry"]))
            self.assertFalse(manifest["labels_read"])
            self.assertFalse(manifest["labels_prices_stock_in_model_input"])
            self.assertEqual(hashlib.sha256((out/"inputs.jsonl").read_bytes()).hexdigest(), manifest["inputs_sha256"])
            self.assertTrue(manifest["source_sha256"]["v3_dossiers"])
            self.assertTrue(manifest["source_sha256"]["raw_sources"])

    @unittest.skipUnless(SOURCE_READY, "real frozen source package is not present in this checkout")
    def test_limit_and_explicit_case_selection_are_deterministic_subsets(self):
        original = builder.jsonl(INPUTS)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "limited"
            result = builder.build(root=ROOT, output_dir=out, inputs_path=INPUTS,
                input_manifest_path=MANIFEST, products_path=PRODUCTS, dossiers_dir=DOSSIERS, limit=3)
            self.assertEqual(result["input_count"], 3)
            self.assertEqual([x["case_id"] for x in builder.jsonl(out/"inputs.jsonl")], [x["case_id"] for x in original[:3]])
            out2 = Path(tmp) / "selected"
            ids = [original[7]["case_id"], original[2]["case_id"]]
            result2 = builder.build(root=ROOT, output_dir=out2, inputs_path=INPUTS,
                input_manifest_path=MANIFEST, products_path=PRODUCTS, dossiers_dir=DOSSIERS, case_ids=ids)
            self.assertEqual(result2["input_count"], 2)
            self.assertEqual([x["case_id"] for x in builder.jsonl(out2/"inputs.jsonl")], [ids[1], ids[0]])


if __name__ == "__main__":
    unittest.main()
