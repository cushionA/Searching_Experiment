import hashlib
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".lab-output/sku-real-luna-annotation-inputs-20261010-v2"


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class LunaAnnotationInputTests(unittest.TestCase):
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
