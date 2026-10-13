from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import sku_live_nextdata_body_adapter_v1 as LIVE


class LiveDescriptionSourceTests(unittest.TestCase):
    def setUp(self):
        if not (LIVE.CHEERIO_ROOT / "node_modules/cheerio/package.json").is_file():
            self.skipTest("pinned Cheerio dependency is not installed")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source_root = Path(self.tmp.name) / "old"
        self.live_root = Path(self.tmp.name) / "live"
        self.source_root.mkdir()
        self.live_root.mkdir()

    @staticmethod
    def sha(raw):
        return hashlib.sha256(raw).hexdigest()

    def fixture(self, fields=None, page_override=None):
        item = {"lotNumber": "123", "itemTitle": "原値のタイトル", "skuInfo": {
            "optionName": {"row": "サイズ"}, "rowNames": ["S", "L"],
            "columnNames": [], "stockList": [[{}], [{}]]}}
        item.update(fields or {"extraItemComment": "<table><tr><th>サイズ</th><td>S</td></tr></table>",
                               "itemComment": "<p>商品詳細</p>"})
        raw = LIVE.BODY.canon({"itemInfo": item})
        (self.source_root / "old.json").write_bytes(raw)
        digest, doc_sha = self.sha(raw), self.sha(b"stable-product-identity")
        old_store = LIVE.BODY.NLI.SOURCES.RawStore(self.source_root)
        title_ref = LIVE.BODY.NLI.SOURCES.json_leaf_span(old_store, "old.json", "$.itemInfo.itemTitle")
        descriptions = [{"source_refs": [{"raw_file": "old.json", "sha256": digest,
                         "locator": "$.itemInfo." + field}]} for field in
                        ("extraItemComment", "extraItemCommentWithVideo", "itemComment", "detailComment")]
        doc = {"document_id": "original-au", "document_sha256": doc_sha, "side": "au",
               "title": {"source_refs": [title_ref]}, "description": {"segments": descriptions}}
        body = LIVE.BODY._source_body(old_store, doc)
        inventory = LIVE.BODY._target_sku_inventory(old_store, doc)
        name_ref = LIVE.BODY.NLI.SOURCES.json_leaf_span(old_store, "old.json", "$.itemInfo.skuInfo.optionName.row")
        value_refs = [LIVE.BODY.NLI.SOURCES.json_leaf_span(old_store, "old.json", f"$.itemInfo.skuInfo.rowNames[{i}]")
                      for i in range(2)]
        vector = [{"option_index": i, "whole_option": value,
                   "option_evidence_id": LIVE.BODY.NLI._span_id(value_refs[i])}
                  for i, value in enumerate(["S", "L"])]
        pair = {"pair_id": "fixture:pair", "cache_key": "fixture:cache", "protocol": LIVE.BODY.PROTOCOL,
                "preparation_status": "ready", "option_values": ["S", "L"], "candidate_vector": vector,
                "candidate_vector_order": "exact original raw family order", "scope_status": "fixture",
                "evaluation_instruction": "fixture source protocol"}
        context = {"direction": "rakuten_to_au", "target_document_sha256": doc_sha,
                   "source_body_refs": [{"document_sha256": doc_sha}],
                   "hypothesis": {"family_members": [{"whole_option": value,
                       "evidence_id": vector[i]["option_evidence_id"], "span": value_refs[i]}
                       for i, value in enumerate(["S", "L"])]},
                   "target_selected_facts": [{"axis_name": "サイズ", "value": "S",
                       "axis_name_span": name_ref, "value_span": value_refs[0]}]}
        payload = LIVE.BODY.build_model_payload(pair, context, {doc_sha: body}, inventory)
        next_data = {"query": {"item_id": "123"}, "props": {"pageProps": {"initialState": {
            "itemDetails": {"itemPageInfo": {"itemInfo": item}}}}}}
        # Actual Next JSON escapes HTML characters; source offsets still address
        # decoded string leaves rather than offsets in the serialized JSON.
        script = json.dumps(next_data, ensure_ascii=True).replace("<", "\\u003c").encode()
        page = b'<html><script id="__NEXT_DATA__" type="application/json">' + script + b'</script></html>'
        if page_override is not None:
            page = page_override
        (self.live_root / "page.html").write_bytes(page)
        (self.live_root / "nextdata.json").write_bytes(script)
        record = {"product_id": "123", "logical_au_raw_file": "old.json",
                  "logical_au_raw_capture_sha256": digest, "status": 200,
                  "requested_url": "https://wowma.jp/item/123", "final_url": "https://wowma.jp/item/123",
                  "page_raw_file": "page.html", "page_sha256": self.sha(page), "nextdata_status": "present",
                  "nextdata_raw_file": "nextdata.json", "nextdata_raw_script_sha256": self.sha(script)}
        manifest = {"product_records": [record]}
        return payload, pair, body, {doc_sha: body}, manifest

    def unknown(self, pair):
        return {"protocol": LIVE.BODY.PROTOCOL, "pair_id": pair["pair_id"], "cache_key": pair["cache_key"],
                "decisions": [{"option_index": i, "whole_option": value,
                    "option_evidence_id": pair["candidate_vector"][i]["option_evidence_id"],
                    "whole_value_assessed": True, "state": "unknown", "scope_assessment": "uncertain",
                    "semantic_relation": "uncertain", "scope_and_coordinate_explanation": "fixture uncertainty",
                    "option_relation_explanation": "fixture whole option", "exclusivity_assessment": "uncertain",
                    "exclusivity_explanation": "fixture uncertainty", "grounding_route": "none",
                    "target_fact_indices": [], "body_invariant": None, "body_quote_refs": [],
                    "body_condition_bindings": [], "direct_contradiction_explanation": ""}
                    for i, value in enumerate(pair["option_values"])]}

    def build(self, fixture):
        payload, _, _, bodies, manifest = fixture
        return LIVE.build_live_payload_views(payload, bodies, manifest, self.source_root, self.live_root)

    def validate(self, fixture, described, context, response=None):
        _, pair, body, _, _ = fixture
        return LIVE.validate_live_source_response(response or self.unknown(pair), pair, described,
                                                  body, self.source_root, context)

    def test_exact_user_text_title_dedupe_and_escaped_json_provenance(self):
        html = '<table><tr><th>サイズ</th><td>S<br>L</td></tr></table><script>garbage</script>'
        fixture = self.fixture({"extraItemComment": html, "extraItemCommentWithVideo": html,
                                "itemComment": '<p>本文 <a href="/other">リンク原文</a></p>',
                                "detailComment": "<div>詳細</div>"})
        before = deepcopy(fixture[0])
        original, described, context, audit = self.build(fixture)
        self.assertEqual(original, before)
        self.assertEqual(fixture[0], before)
        target = described["context"]["eligible_target_body"]
        self.assertEqual(len(target["blocks"]), 4)
        self.assertEqual(target["blocks"][0]["field"], "native_product_name")
        self.assertIn("サイズ\tS", target["blocks"][1]["support_text"])
        self.assertNotIn("garbage", target["blocks"][1]["support_text"])
        self.assertIn("リンク原文", target["blocks"][2]["support_text"])
        self.assertTrue(target["blocks"][2]["links_present"])
        proof = next(iter(context["documents"].values()))
        self.assertEqual(len(proof["sections"][0]["source_fields"]), 2)
        self.assertTrue(proof["source_bound_title_added"])
        self.assertFalse(audit["fallback_original_full"])
        response = self.unknown(fixture[1])
        decision = response["decisions"][0]
        decision["grounding_route"] = "selected_sku_body"
        decision["target_fact_indices"] = [0]
        decision["body_quote_refs"] = [{
            "body_index": i, "block_id": target["blocks"][i]["block_id"],
            "document_sha256": target["document_sha256"],
            "source_sha256": target["blocks"][i]["source_sha256"],
            "source_alias": target["blocks"][i]["source_alias"],
            "quote": target["blocks"][i]["support_text"], "start": 0,
            "end": len(target["blocks"][i]["support_text"])} for i in [0, 1]]
        decision["body_condition_bindings"] = [{"target_fact_index": 0, "body_axis_quote": "サイズ",
            "body_value_quote": "S", "role": "uncertain", "binding_explanation": "fixture source only"}]
        semantic_before = deepcopy(response)
        checked = self.validate(fixture, described, context, response)
        self.assertTrue(checked["source_citations_verified"])
        self.assertFalse(checked["semantic_truth_verified"])
        self.assertEqual(len(checked["citations"]), 2)
        self.assertEqual(checked["citations"][1]["transformation"]["kind"], "exact_user_cheerio_descriptionText")
        self.assertEqual(response, semantic_before)

    def test_media_only_and_missing_next_use_exact_original_full_fallback(self):
        for fields, override in (({"extraItemComment": '<img src="spec.png" alt="SKU証明にはしない">'}, None),
                                 ({"itemComment": "<p>old description</p>"}, b"<html>no Next script</html>")):
            with self.subTest(fields=fields, page_override=override):
                fixture = self.fixture(fields, override)
                original, described, context, audit = self.build(fixture)
                self.assertEqual(described, fixture[0])
                self.assertEqual(original, fixture[0])
                self.assertTrue(audit["fallback_original_full"])
                proof = next(iter(context["documents"].values()))
                self.assertFalse(proof["source_bound_title_added"])
                if override is None:
                    self.assertEqual(proof["sections"][0]["images"][0]["alt"], "SKU証明にはしない")
                    self.assertTrue(proof["sections"][0]["media_only"])
                self.assertEqual(proof["body_view_sha256"], self.sha(LIVE.BODY.canon(
                    fixture[0]["context"]["eligible_target_body"])))
                checked = self.validate(fixture, described, context)
                self.assertTrue(checked["source_citations_verified"])

    def test_public_text_fact_and_raw_capture_tampering_fail_closed(self):
        fixture = self.fixture()
        _, described, context, _ = self.build(fixture)
        broken = deepcopy(described)
        broken["context"]["eligible_target_body"]["blocks"][1]["support_text"] += " altered"
        with self.assertRaisesRegex(ValueError, "frozen input"):
            self.validate(fixture, broken, context)
        broken = deepcopy(described)
        broken["context"]["target_selected_facts"][0]["value"] = "L"
        with self.assertRaisesRegex(ValueError, "original facts"):
            self.validate(fixture, broken, context)
        (self.live_root / "nextdata.json").write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.validate(fixture, described, context)

    def test_wrong_variant_and_transformation_pins_fail_closed(self):
        fixture = self.fixture()
        _, described, context, _ = self.build(fixture)
        with self.assertRaisesRegex(ValueError, "description variant"):
            LIVE.validate_live_source_response(self.unknown(fixture[1]), fixture[1], described,
                fixture[2], self.source_root, context, variant="table")
        altered = deepcopy(context)
        altered["extractor_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "implementation changed"):
            self.validate(fixture, described, altered)

    def test_original_inventory_and_fact_spans_are_independently_rederived(self):
        fixture = self.fixture()
        _, described, context, _ = self.build(fixture)
        for mutation, error in (("inventory", "native target SKU inventory"), ("fact_span", "selected target fact")):
            with self.subTest(mutation=mutation):
                altered = deepcopy(described)
                proof = deepcopy(context)
                if mutation == "inventory":
                    altered["context"]["target_native_sku_inventory"]["axes"][0]["axis_name"] = "架空の軸"
                else:
                    fact = altered["context"]["target_selected_facts"][0]
                    fact["value_span"] = {**fact["value_span"], "quote": "原文に無い値"}
                # Bypass only the input hash to exercise the independent raw
                # source check; changing a frozen private receipt is not valid.
                proof["original_non_body_sha256"] = LIVE._non_body_sha(altered)
                with self.assertRaisesRegex(ValueError, error):
                    self.validate(fixture, altered, proof)


if __name__ == "__main__":
    unittest.main()
