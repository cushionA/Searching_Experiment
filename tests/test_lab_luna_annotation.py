import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".lab-output/sku-real-luna-annotation-inputs-20261010-v2"


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


SPEC = importlib.util.spec_from_file_location(
    "build_luna_annotation_inputs", ROOT / "experiments/sku-matching/build_luna_annotation_inputs.py")
builder = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(builder)


class LunaAnnotationInputTests(unittest.TestCase):
    def test_semantic_split_preserves_specification_purchase_wording_and_product_names(self):
        exception = "天板高さ50cmのシルバーには持ち手がありません。予めご了承の上、ご購入をお願いいたします。"
        pet_cart = "ペットカートは軽量で折りたたみ可能です。"
        round_type = "商品名：円型テーブル"
        for text in (exception, pet_cart, round_type):
            semantic, reason = builder.semantic_line(text)
            self.assertEqual(semantic, text)
            self.assertIsNone(reason)
        masked, reason = builder.semantic_line("サイズ100cm、通常価格1,280円の商品です")
        self.assertIn("サイズ100cm", masked)
        self.assertNotIn("1,280円", masked)
        self.assertIsNone(reason)
        stock_masked, reason = builder.semantic_line("サイズ100cm、在庫ありです")
        self.assertIn("サイズ100cm", stock_masked)
        self.assertNotIn("在庫あり", stock_masked)
        self.assertIsNone(reason)
        self.assertEqual(builder.semantic_line("送料について：全国一律500円")[1], "shipping_policy")
        self.assertEqual(builder.DEFAULT_OUTPUT.name, "sku-real-luna-annotation-inputs-20261010-v3")

    def test_option_specifications_are_kept_and_raw_option_text_is_audit_only(self):
        note = "天板高さ50cmのシルバーには持ち手がありません。予めご了承の上、ご購入をお願いいたします。"
        result = builder.public_au_options({"free_options": [{"title": "仕様上の注意", "freeOptionsList": [{"title": note}]}]})
        self.assertEqual(result["free_options"][0]["selection_titles"], [note])
        self.assertEqual(result["raw_fields"][0]["selection_titles"], [note])
        self.assertEqual(result["raw_fields"][0]["use"], "audit_only_not_semantic_input")

    def test_description_extractors_preserve_raw_source_and_keep_mixed_specs_semantic(self):
        au_record = {"itemInfo": {"extraItemComment": (
            '<p>天板高さ50cmのシルバーには持ち手がありません。予めご了承の上、ご購入をお願いいたします。</p>'
            '<p>ペットカートは軽量です。</p><p>円型テーブル</p><p>価格1,280円</p><p>送料について</p>')}}
        rak_html = ('<span class="item_desc"><p>天板高さ50cmのシルバーには持ち手がありません。'
                    '予めご了承の上、ご購入をお願いいたします。</p><p>ペットカート対応</p>'
                    '<p>価格1,280円</p><p>送料について</p></span>')
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            au_path = pathlib.Path(temp) / "au.json"
            au_bytes = json.dumps(au_record, ensure_ascii=False).encode()
            au_path.write_bytes(au_bytes)
            au_desc = builder.extract_au_description(au_path, hashlib.sha256(au_bytes).hexdigest())
            self.assertIn("ご購入をお願いいたします", "\n".join(b["text"] for b in au_desc["blocks"]))
            self.assertIn("ペットカート", "\n".join(b["text"] for b in au_desc["blocks"]))
            self.assertIn("円型テーブル", "\n".join(b["text"] for b in au_desc["blocks"]))
            self.assertIn("raw_text", au_desc["raw_fields"][0])
            self.assertTrue(any(x["reason"] == "shipping_policy" for x in au_desc["commercial_only_lines"]))

            rk_path = pathlib.Path(temp) / "rakuten.html"
            rk_bytes = rak_html.encode()
            rk_path.write_bytes(rk_bytes)
            rk_desc = builder.extract_rakuten_description(rk_path, hashlib.sha256(rk_bytes).hexdigest())
            excerpt = rk_desc["individual_description_excerpt"]
            self.assertIn("ご購入をお願いいたします", excerpt)
            self.assertIn("ペットカート", excerpt)
            self.assertIn("1,280円", rk_desc["raw_fields"][0]["raw_text"])
            self.assertNotIn("1,280円", excerpt)

    def test_blind_cases_have_resolved_dossiers_and_stable_ids(self):
        cases = read_jsonl(OUTPUT / "cases.jsonl")
        index = json.loads((OUTPUT / "dossier_index.json").read_text(encoding="utf-8"))
        self.assertEqual(len(cases), 1383)
        self.assertEqual(len({case["case_id"] for case in cases}), len(cases))
        for case in cases:
            self.assertIn(case["dossier_id"], {p.stem for p in (OUTPUT / "dossiers").glob("*.json")})
            dossier = json.loads((OUTPUT / "dossiers" / f"{case['dossier_id']}.json").read_text(encoding="utf-8"))
            self.assertEqual(index[dossier["pair_ref"]], f"dossiers/{case['dossier_id']}.json")
            expected = "case-" + hashlib.sha256(
                f"{dossier['pair_ref']}\0{case['rakuten']['source']['sku_record_key']}".encode()
            ).hexdigest()[:20]
            self.assertEqual(case["case_id"], expected)
            self.assertEqual(len({row["row_key"] for row in dossier["au_rows"]}), len(dossier["au_rows"]))
            self.assertTrue(all(row.get("axes_raw") is not None for row in dossier["au_rows"]))
            self.assertTrue(all(row["row_key"].startswith(f"au:{dossier['au_product']['product_id']}:")
                                for row in dossier["au_rows"]))
            self.assertIn("purchase_options_raw", dossier["au_product"])
            rdesc = dossier["rakuten_product"]["description"]
            self.assertIn("individual_description_excerpt", rdesc)
            self.assertEqual(rdesc["source"]["raw_file"], dossier["rakuten_product"]["source"]["raw_file"])

    def test_blind_inputs_exclude_commercial_and_label_fields(self):
        manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
        cases = read_jsonl(OUTPUT / "cases.jsonl")
        eligibility = read_jsonl(OUTPUT / "eligibility.jsonl")
        au_eligibility = read_jsonl(OUTPUT / "au_eligibility.jsonl")
        self.assertEqual(len(eligibility), len(cases))
        self.assertEqual(len(au_eligibility), 29)
        self.assertTrue(manifest["source_inputs_pre_post_sha256"]["unchanged"])
        self.assertTrue(manifest["raw_source_pre_post_sha256"]["unchanged"])
        forbidden = {"configuration", "expected_lace", "preclusion_reason", "pair_status",
                     "is_independent_gold", "matching_au_rows", "matcher_output", "model_output"}
        for path in [OUTPUT / "cases.jsonl", *sorted((OUTPUT / "dossiers").glob("*.json"))]:
            raw = path.read_text(encoding="utf-8")
            for key in forbidden:
                self.assertNotIn(f'"{key}"', raw, f"{key} leaked into {path.name}")
        for dossier_path in sorted((OUTPUT / "dossiers").glob("*.json")):
            digest = hashlib.sha256(dossier_path.read_bytes()).hexdigest()
            self.assertEqual(manifest["dossier_sha256"][str(dossier_path.relative_to(OUTPUT))], digest)
        # Commercial fields are intentionally available only through the separately stored eligibility files.
        self.assertTrue(all("price_jpy" in row["rakuten"] for row in eligibility))
        self.assertTrue(all("rows" in row for row in au_eligibility))

    def test_group_split_and_shard_membership_are_atomic(self):
        cases = read_jsonl(OUTPUT / "cases.jsonl")
        group_split = {}
        group_shard = {}
        shard_case_ids = []
        for case in cases:
            group = case["group_id"]
            group_split.setdefault(group, set()).add(case["split"])
            group_shard.setdefault(group, set()).add(case["shard_id"])
        self.assertTrue(all(len(value) == 1 for value in group_split.values()))
        self.assertTrue(all(len(value) == 1 for value in group_shard.values()))
        self.assertGreaterEqual(sum("dev" in v for v in group_split.values()), 3)
        self.assertGreaterEqual(sum("test" in v for v in group_split.values()), 3)
        for shard in ("shard-1", "shard-2", "shard-3"):
            shard_cases = read_jsonl(OUTPUT / shard / "cases.jsonl")
            shard_case_ids.extend(case["case_id"] for case in shard_cases)
            self.assertTrue(all(case["shard_id"] == shard for case in shard_cases))
            shard_manifest = json.loads((OUTPUT / shard / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(shard_manifest["case_count"], len(shard_cases))
            self.assertEqual(shard_manifest["case_ids"], sorted(case["case_id"] for case in shard_cases))
        self.assertEqual(sorted(shard_case_ids), sorted(case["case_id"] for case in cases))


if __name__ == "__main__":
    unittest.main()
