import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments" / "sku-matching"))
import audit_luna_sku_labels as audit


def fixture(case_id_suffix="1", group="g1", split="dev", url="https://item.rakuten.co.jp/shop/item/"):
    pair_id = f"au:1|raku:{case_id_suffix}"
    key = "a" * 64
    case_id = audit.case_id_for(pair_id, key)
    dossier_id = f"d-{case_id_suffix}"
    row_key = "au:1:10:0:0"
    dossier = {
        "dossier_id": dossier_id, "group_id": group, "pair_ref": pair_id,
        "au_product": {"product_id": "1", "title_raw": "fixed AU title",
                        "source": {"json_path": "$.itemInfo.itemName"},
                        "purchase_options_raw": {"free_options": [{"title": "Mount type", "selection_titles": ["wall"]}]},
                        "description": {"source": {"json_paths": ["$.itemInfo.extraItemComment"]},
                                        "blocks": [{"source_field": "$.itemInfo.extraItemComment", "text": "Steel frame specification"}]}},
        "au_rows": [{"row_key": row_key,
                     "axes_raw": [{"axis_name_raw": "Color", "value_raw": "navy"}]}],
        "rakuten_product": {"title_raw": "Rakuten title",
                             "source": {"url": url, "raw_file": "raw.html", "sha256": "b" * 64,
                                         "json_path": "products.jsonl[manage_number=x]"},
                             "description": {"source": {"json_path": "Rakuten product page HTML body", "page_url": url},
                                             "visible_page_text_excerpt": "Aluminum-free steel frame"}},
    }
    case = {"case_id": case_id, "dossier_id": dossier_id, "group_id": group, "split": split,
            "rakuten": {"title_raw": "Rakuten title", "option_values": [{"axis_key": "Color", "value": "navy"}],
                        "axes_labels": [{"key": "Color", "label": "Color"}],
                        "source": {"url": url, "raw_file": "raw.html", "sha256": "b" * 64,
                                   "sku_record_key": key, "source_row_index": 0,
                                   "source_row_key": f"{url}#sku-row-0-aaaaaaaaaaaaaaaa",
                                   "source_sku_key": f"{url}#variant:0", "json_path": "products.jsonl[manage_number=x]"}}}
    label = {"case_id": case_id, "decision": "matched", "matching_au_row_keys": [row_key],
             "rationale": "The documented frame material and color align.",
             "evidence": [{"source": "au_row", "source_ref": row_key, "quote": "Color=navy"},
                          {"source": "rakuten_description", "source_ref": "Rakuten product page HTML body",
                           "quote": "Aluminum-free steel frame"}]}
    return case, dossier, label


class LunaLabelAuditTests(unittest.TestCase):
    def manifest(self):
        return {"blindness": {"synthetic_data_included": False,
                              "matcher_and_model_fields_removed": True},
                "split_policy": {"families_kept_together": []}}

    def test_accepts_exact_case_source_and_fixed_au_row_citations(self):
        case, dossier, label = fixture()
        result = audit.validate_label_records([case], {dossier["dossier_id"]: dossier}, [label], {}, self.manifest())
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["decision_counts"], {"matched": 1})
        self.assertEqual(result["citation_count"], 2)

    def test_rejects_row_keys_on_unmatched_and_nonverbatim_citations(self):
        case, dossier, label = fixture()
        label["decision"] = "unmatched"
        label["evidence"][0]["quote"] = "navy color, but this text is not in the source"
        result = audit.validate_label_records([case], {dossier["dossier_id"]: dossier}, [label], {}, self.manifest())
        codes = {e["code"] for e in result["errors"]}
        self.assertIn("nonmatched_has_au_row_keys", codes)
        self.assertIn("evidence_quote_not_in_dossier_source", codes)

    def test_binds_au_row_quote_to_cited_row_not_another_row(self):
        case, dossier, label = fixture()
        dossier["au_rows"].append({"row_key": "au:1:11:0:1",
                                   "axes_raw": [{"axis_name_raw": "Color", "value_raw": "red"}]})
        label["evidence"][0]["source_ref"] = "au:1:11:0:1"
        label["evidence"][0]["quote"] = "Color=navy"
        result = audit.validate_label_records([case], {dossier["dossier_id"]: dossier}, [label], {}, self.manifest())
        self.assertIn("evidence_quote_not_in_dossier_source", {e["code"] for e in result["errors"]})

    def test_rejects_duplicate_labels_and_split_leakage_by_url_and_group(self):
        case1, dossier1, label1 = fixture("1", "family", "dev")
        case2, dossier2, label2 = fixture("2", "family", "test")
        result = audit.validate_label_records([case1, case2],
            {dossier1["dossier_id"]: dossier1, dossier2["dossier_id"]: dossier2},
            [label1, label2, copy.deepcopy(label2)], {}, self.manifest())
        codes = {e["code"] for e in result["errors"]}
        self.assertIn("duplicate_or_missing_label_case_id", codes)
        self.assertIn("group_split_leakage", codes)
        self.assertIn("rakuten_url_split_leakage", codes)

    def test_rejects_commercial_fields_in_blinded_case(self):
        case, dossier, label = fixture()
        case["price_jpy"] = 1000
        result = audit.validate_label_records([case], {dossier["dossier_id"]: dossier}, [label], {}, self.manifest())
        self.assertIn("commercial_field_leaked_into_blinded_input", {e["code"] for e in result["errors"]})


if __name__ == "__main__":
    unittest.main()
