import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import sku_body_invariant_native_rakuten_source_v1 as R9


def _sku_object(description, *, item_id=12345, ids=None, selectors=None):
    selectors = selectors or [
        {"key": "color", "label": "Color", "values": [
            {"value": "Red", "label": "Red"}, {"value": "Blue", "label": "Blue"}]},
        {"key": "size", "label": "Size", "values": [
            {"value": "S", "label": "S"}, {"value": "M", "label": "M"}]},
    ]
    rows = [
        {"variantId": "red-s", "selectorValues": ["Red", "S"]},
        {"variantId": "red-m", "selectorValues": ["Red", "M"]},
        {"variantId": "blue-s", "selectorValues": ["Blue", "S"]},
        {"variantId": "blue-m", "selectorValues": ["Blue", "M"]},
    ]
    if ids is not None:
        rows = [row for row in rows if row["variantId"] in ids]
    return {"itemId": item_id, "shopId": "shop-1", "manageNumber": "item-1",
        "variantSelectors": selectors, "sku": rows,
        "pcFields": {"itemNumber": "item-1", "productDescription": description}}


def _fixture(root, description, *, mutate_api=None, mutate_new=None, include_field=True):
    desc = description if include_field else None
    api = _sku_object(desc)
    new = copy.deepcopy(api)
    if mutate_api:
        mutate_api(api)
    if mutate_new:
        mutate_new(new)
    if not include_field:
        api["pcFields"].pop("productDescription")
        new["pcFields"].pop("productDescription")
    app = {"api": {"data": {"itemInfoSku": api}},
           "newApi": {"itemInfoSku": new}}
    script_text = json.dumps(app, ensure_ascii=False, separators=(",", ":"))
    raw_html = "<!doctype html><html><head><title>Fixture item</title></head><body>" \
        "<script id=\"item-page-app-data\" type=\"application/json\">" + script_text + \
        "</script></body></html>"
    path = root / "capture.html"
    path.write_text(raw_html, encoding="utf-8")
    digest = R9.BASE.sha_file(path)
    title = "Fixture item"
    title_start = raw_html.index(title)
    title_ref = {"raw_file": "capture.html", "sha256": digest,
        "locator": {"kind": "html_text", "encoding": "utf-8", "path": "html/head/title"},
        "start": title_start, "end": title_start + len(title), "quote": title}
    doc = {"side": "rakuten", "document_id": "doc:fixture", "document_sha256": "d" * 64,
        "title": {"text": title, "source_refs": [title_ref]},
        "description": {"segments": [{"source_refs": [{"raw_file": "capture.html",
            "sha256": digest, "locator": "whole_document_visible_text"}]}]}}
    return doc, raw_html


class NativeRakutenBodySourceTests(unittest.TestCase):
    def test_mirrored_native_field_has_raw_lexeme_and_field_local_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            desc = '<p>All colors use "aluminum" construction.\nBody value is exact.</p>' \
                   '<a href="/related">Related product</a>'
            doc, raw = _fixture(root, desc)
            store = R9.BASE.NLI.SOURCES.RawStore(root)
            blocks = R9.native_rakuten_blocks(store, doc)
            self.assertTrue(blocks)
            self.assertTrue(all(b["field"] == "native_rakuten_product_description" for b in blocks))
            self.assertFalse(any("Related product" in b["support_text"] for b in blocks))
            for block in blocks:
                ref = block["source_ref"]
                self.assertEqual(ref["quote"], raw[ref["start"]:ref["end"]])
                self.assertIn('\\"aluminum\\"', ref["quote"])
                self.assertTrue(R9.BASE.NLI.SOURCES.verify_span(store, ref))
                binding = block["native_field_binding"]
                self.assertEqual(binding["script_locator"], "script#item-page-app-data[type=application/json]")
                self.assertEqual(len(binding["mirrored_field_refs"]), 2)
                self.assertTrue(all(R9.BASE.NLI.SOURCES.verify_span(store, x)
                    for x in binding["mirrored_field_refs"]))

    def test_mirror_identity_or_description_disagreement_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc, _ = _fixture(root, "<p>Same identity text.</p>",
                mutate_new=lambda obj: obj.update({"itemId": 99999}))
            self.assertEqual(R9.native_rakuten_blocks(R9.BASE.NLI.SOURCES.RawStore(root), doc), [])
            doc, _ = _fixture(root, "<p>Common description.</p>",
                mutate_new=lambda obj: obj["pcFields"].update({"productDescription": "<p>Different.</p>"}))
            self.assertEqual(R9.native_rakuten_blocks(R9.BASE.NLI.SOURCES.RawStore(root), doc), [])

    def test_mirrored_sku_identity_set_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc, _ = _fixture(root, "<p>Common description.</p>",
                mutate_api=lambda obj: obj.update({"sku": [r for r in obj["sku"] if r["variantId"] != "blue-m"]}))
            self.assertEqual(R9.native_rakuten_blocks(R9.BASE.NLI.SOURCES.RawStore(root), doc), [])

    def test_same_variant_ids_with_different_row_coordinates_abstain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def change_one_row(obj):
                rows = [dict(row) for row in obj["sku"]]
                rows[0]["selectorValues"] = ["Blue", "S"]
                obj["sku"] = rows
            doc, _ = _fixture(root, "<p>Common description.</p>", mutate_new=change_one_row)
            self.assertEqual(R9.native_rakuten_blocks(R9.BASE.NLI.SOURCES.RawStore(root), doc), [])

    def test_partial_axis_coverage_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc, _ = _fixture(root, "<p>Common description.</p>",
                mutate_api=lambda obj: obj.update({"sku": [r for r in obj["sku"] if r["selectorValues"][0] != "Blue"]}),
                mutate_new=lambda obj: obj.update({"sku": [r for r in obj["sku"] if r["selectorValues"][0] != "Blue"]}))
            self.assertEqual(R9.native_rakuten_blocks(R9.BASE.NLI.SOURCES.RawStore(root), doc), [])

    def test_missing_field_and_wrong_raw_sha_abstain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc, _ = _fixture(root, "unused", include_field=False)
            self.assertEqual(R9.native_rakuten_blocks(R9.BASE.NLI.SOURCES.RawStore(root), doc), [])
            doc, _ = _fixture(root, "<p>Common description.</p>")
            doc["title"]["source_refs"][0]["sha256"] = "0" * 64
            self.assertEqual(R9.native_rakuten_blocks(R9.BASE.NLI.SOURCES.RawStore(root), doc), [])

    def test_source_response_rederives_native_identity_field_and_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc, _ = _fixture(root, '<p>Every option uses the "same" material.</p>')
            store = R9.BASE.NLI.SOURCES.RawStore(root)
            with R9._installed_adapter():
                body = R9._source_body(store, doc)
            inventory = R9.BASE._target_sku_inventory(store, doc)
            candidate_path = root / "candidate.json"
            candidate_path.write_text('{"condition":"steel"}', encoding="utf-8")
            span = R9.BASE.NLI.SOURCES.json_leaf_span(store, "candidate.json", "$.condition", "steel")
            option_id = R9.BASE.NLI._span_id(span)
            pair = {"pair_id": "pair:fixture", "cache_key": "cache:fixture",
                "option_values": ["steel"], "candidate_vector": [{"option_index": 0,
                    "whole_option": "steel", "option_evidence_id": option_id}]}
            payload_blocks = [{"body_index": i, "block_id": b["block_id"], "field": b["field"],
                "passage": b["passage"], "support_text": b["support_text"],
                "source_sha256": b["source_ref"]["sha256"],
                "source_alias": f"body-source:{doc['document_sha256'][:16]}:{i}",
                "links_present": b.get("links_present", False)} for i, b in enumerate(body["blocks"])]
            payload = {"context": {"target_document_sha256": doc["document_sha256"],
                "target_selected_facts": [],
                "hypothesis": {"family_members": [{"whole_option": "steel",
                    "evidence_id": option_id, "span": span}]},
                "target_native_sku_inventory": R9.BASE._public_inventory(inventory),
                "eligible_target_body": {"document_sha256": doc["document_sha256"],
                    "status": body["status"], "blocks": payload_blocks}}}
            decision = {"option_index": 0, "whole_option": "steel", "option_evidence_id": option_id,
                "whole_value_assessed": True, "state": "unknown", "scope_assessment": "uncertain",
                "semantic_relation": "uncertain", "scope_and_coordinate_explanation": "意味の対応を判断できない。",
                "option_relation_explanation": "対象条件が特定できない。", "exclusivity_assessment": "uncertain",
                "exclusivity_explanation": "不明。", "grounding_route": "none", "target_fact_indices": [],
                "body_invariant": None, "body_quote_refs": [], "body_condition_bindings": [],
                "direct_contradiction_explanation": ""}
            response = {"protocol": R9.BASE.PROTOCOL, "pair_id": pair["pair_id"],
                "cache_key": pair["cache_key"], "decisions": [decision]}
            checked = R9.validate_source_response(response, pair, payload, body, root)
            self.assertTrue(checked["source_citations_verified"])
            damaged = copy.deepcopy(body)
            damaged["blocks"][0]["native_field_binding"]["native_identity"]["itemId"] = 999
            with self.assertRaisesRegex(ValueError, "exact rederivation"):
                R9.validate_source_response(response, pair, payload, damaged, root)


if __name__ == "__main__":
    unittest.main()
