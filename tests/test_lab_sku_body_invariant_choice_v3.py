import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import sku_body_invariant_choice_v3 as V3


class BodyInvariantProtocolV3Tests(unittest.TestCase):
    def setUp(self):
        self.support = "本商品は全サイズ共通でアルミ製です。"
        self.block = {"body_index": 0, "block_id": "body-block:one",
            "field": "native_description", "passage": self.support,
            "support_text": self.support, "source_sha256": "a" * 64,
            "source_alias": "body-source:dddddddddddddddd:0",
            "links_present": False}
        self.states = [
            {"state_index": 0, "values": [{"axis_index": 0, "axis_name": "サイズ", "value": "S"}], "member_evidence_ids": ["raw-span:s"]},
            {"state_index": 1, "values": [{"axis_index": 0, "axis_name": "サイズ", "value": "M"}], "member_evidence_ids": ["raw-span:m"]},
        ]
        self.pair = {"pair_id": "pair:1", "cache_key": "cache:1", "option_values": ["アルミ", "スチール"],
            "candidate_vector": [{"option_index": 0, "whole_option": "アルミ", "option_evidence_id": "option:0"},
                                 {"option_index": 1, "whole_option": "スチール", "option_evidence_id": "option:1"}]}
        self.payload = {"context": {"pair_id": "pair:1", "target_document_sha256": "d" * 64,
            "target_selected_facts": [],
            "eligible_target_body": {"blocks": [self.block]},
            "target_native_sku_inventory": {"status": "verified_native_axes", "all_option_states": self.states}}}

    def _body_decision(self, index, option, state="support"):
        return {"option_index": index, "whole_option": option,
            "option_evidence_id": f"option:{index}", "whole_value_assessed": True,
            "state": state, "scope_assessment": "same_condition" if state != "unknown" else "uncertain",
            "semantic_relation": state if state in {"support", "explicit_conflict"} else "uncertain",
            "scope_and_coordinate_explanation": "この候補全体を独立に照合した。",
            "option_relation_explanation": "属性語と候補全体の意味を確認した。",
            "exclusivity_assessment": "model_assessed", "exclusivity_explanation": "明示された商品属性を評価した。",
            "grounding_route": "body_invariant", "target_fact_indices": [],
            "body_invariant": {"attribute": "材質", "attribute_quote": "アルミ製", "value": "アルミ",
                "value_quote": "アルミ製", "role": "product_attribute", "role_explanation": "商品自体の材質記述。",
                "subject_resolution": "target_product", "subject_scope": "文の主語は対象商品の本文。",
                "target_identity_explanation": "ペアに示された対象商品の文脈と一致。",
                "quantifier_scope": "全サイズに共通と明記。",
                "invariance_basis": "explicit_universal_statement", "target_document_sha256": "d" * 64,
                "family_audit": [{"state_index": s["state_index"], "whole_state": s,
                    "member_evidence_ids": s["member_evidence_ids"], "applicability": "covered",
                    "explanation": "本文の全サイズ共通記述に含まれる。"} for s in self.states],
                "semantic_explanation": "『全サイズ共通』が対象SKU全体を明示する。",
                "scope_evidence_quote": "全サイズ共通",
                "positive_scope_explanation": "対象商品の全サイズに適用する説明。",
                "product_scope_assessment": "target_product_level", "whole_attribute_assessed": True},
            "body_condition_bindings": [],
            "body_quote_refs": [{"body_index": 0, "block_id": "body-block:one",
                "document_sha256": "d" * 64, "source_sha256": "a" * 64,
                "source_alias": self.block["source_alias"], "quote": "全サイズ共通でアルミ製",
                "start": self.support.index("全サイズ共通"),
                "end": self.support.index("全サイズ共通") + len("全サイズ共通でアルミ製")}],
            "direct_contradiction_explanation": ""}

    def _response(self):
        return {"protocol": V3.PROTOCOL, "pair_id": "pair:1", "cache_key": "cache:1",
            "decisions": [self._body_decision(0, "アルミ"), self._body_decision(1, "スチール", "unknown")]}

    def test_complete_body_invariant_audit_and_exact_quote_validate(self):
        response = self._response()
        result = V3.validate_literal_response(response, self.pair, self.payload)
        self.assertTrue(result["transport_validated"])
        rendered = V3.render_response(response, self.pair, self.payload)
        self.assertEqual(rendered["judgments"], response["decisions"])
        self.assertFalse(rendered["semantic_truth_verified"])

    def test_missing_target_sku_state_fails_closed(self):
        response = self._response()
        response["decisions"][0]["body_invariant"]["family_audit"].pop()
        with self.assertRaisesRegex(ValueError, "family audit"):
            V3.validate_literal_response(response, self.pair, self.payload)

    def test_axis_absence_cannot_stand_in_for_explicit_invariance(self):
        response = self._response()
        response["decisions"][0]["body_invariant"]["invariance_basis"] = "axis_absence"
        with self.assertRaisesRegex(ValueError, "schema violation"):
            V3.validate_literal_response(response, self.pair, self.payload)

    def test_related_product_subject_is_rejected(self):
        response = self._response()
        response["decisions"][0]["body_invariant"]["subject_resolution"] = "related_product"
        with self.assertRaisesRegex(ValueError, "semantic fields"):
            V3.validate_literal_response(response, self.pair, self.payload)

    def test_positive_product_scope_wide_specification_can_cover_complete_family(self):
        response = self._response()
        invariant = response["decisions"][0]["body_invariant"]
        invariant["invariance_basis"] = "positive_scope_wide_product_specification"
        invariant["scope_evidence_quote"] = "アルミ製"
        invariant["positive_scope_explanation"] = "本文が対象商品の製品仕様として属性を説明する。"
        invariant["quantifier_scope"] = "製品仕様として対象商品全体を説明する。"
        V3.validate_literal_response(response, self.pair, self.payload)

    def test_nonattribute_role_cannot_decide_invariant_match(self):
        response = self._response()
        response["decisions"][0]["body_invariant"]["role"] = "comparison"
        with self.assertRaisesRegex(ValueError, "product-attribute role"):
            V3.validate_literal_response(response, self.pair, self.payload)

    def test_bad_body_offset_fails_and_renderer_never_repairs_it(self):
        response = self._response()
        response["decisions"][0]["body_quote_refs"][0]["end"] += 1
        with self.assertRaisesRegex(ValueError, "quote reference"):
            V3.render_response(response, self.pair, self.payload)

    def test_hidden_selected_value_metadata_cannot_change_visible_payload(self):
        class PayloadNLI:
            @staticmethod
            def _build_model_payload(pair, context, source_docs):
                return {"protocol": "base", "pair_id": pair["pair_id"], "cache_key": pair["cache_key"],
                    "context": {"direction": context["direction"], "hypothesis": context["hypothesis"],
                        "target_selected_facts": context["target_selected_facts"],
                        "target_document_sha256": context["target_document_sha256"],
                        "eligible_source_bodies": []}, "candidate_vector": pair["candidate_vector"],
                    "candidate_vector_order": "exact", "scope_status": "model_assessed_unproved",
                    "sourceproof_certified": False, "production_adoption_allowed": False}
        prior = V3.NLI
        try:
            V3.NLI = PayloadNLI
            context = {"direction": "au_to_rakuten", "hypothesis": {"family_values": ["x", "y"]},
                "target_selected_facts": [], "target_document_sha256": "d" * 64}
            pair = {**self.pair, "context_id": "ctx"}
            docs = {"d" * 64: {"document_sha256": "d" * 64, "status": "eligible_blocks",
                "title": {}, "blocks": [], "citations": []}}
            inventory = {"status": "verified_native_axes", "all_option_states": []}
            first = V3.build_model_payload(pair, context, docs, inventory)
            changed = {**pair, "source_selected_value": "private row value", "selected_wanted": "private"}
            second = V3.build_model_payload(changed, context, docs, inventory)
            self.assertEqual(first, second)
        finally:
            V3.NLI = prior

    def test_selected_sku_body_quote_must_bind_exact_selected_axis_and_value(self):
        fact = {"axis_name": "色", "value": "赤",
            "axis_name_span": {"quote": "色", "raw_file": "raw.json", "sha256": "f" * 64,
                "start": 0, "end": 1},
            "value_span": {"quote": "赤", "raw_file": "raw.json", "sha256": "f" * 64,
                "start": 1, "end": 2}}
        payload = copy.deepcopy(self.payload)
        payload["context"]["target_selected_facts"] = [fact]
        payload["context"]["eligible_target_body"]["blocks"][0]["support_text"] += " 色は赤"
        response = self._response()
        d = response["decisions"][0]
        d.update({"grounding_route": "selected_sku_body", "target_fact_indices": [0],
            "body_invariant": None,
            "body_condition_bindings": [{"target_fact_index": 0, "body_axis_quote": "色",
                "body_value_quote": "赤", "role": "product_attribute",
                "binding_explanation": "本文の色条件を選択済みの対象行に結び付けた。"}]})
        d["body_quote_refs"] = [{"body_index": 0, "block_id": "body-block:one",
            "document_sha256": "d" * 64, "source_sha256": "a" * 64,
            "source_alias": self.block["source_alias"], "quote": "色は赤",
            "start": len(self.support) + 1, "end": len(self.support) + 1 + len("色は赤")}]
        V3.validate_literal_response(response, self.pair, payload)
        payload["context"]["target_selected_facts"][0]["axis_name"] = "サイズ"
        payload["context"]["target_selected_facts"][0]["axis_name_span"]["quote"] = "サイズ"
        with self.assertRaisesRegex(ValueError, "exact selected fact"):
            V3.validate_literal_response(response, self.pair, payload)

    def test_decision_and_invariant_objects_reject_unmodeled_properties(self):
        response = self._response()
        response["decisions"][0]["winner"] = True
        with self.assertRaisesRegex(ValueError, "Additional properties"):
            V3.validate_literal_response(response, self.pair, self.payload)

    def test_atomic_persist_validates_all_before_final_file_exists(self):
        response = self._response()
        literal = json.dumps(response, ensure_ascii=False)
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "responses.jsonl"
            with self.assertRaises(ValueError):
                V3.persist_literal_responses_atomically(destination, [literal], ["wrong-pair"], lambda x: None)
            self.assertFalse(destination.exists())
            result = V3.persist_literal_responses_atomically(destination, [literal], ["pair:1"],
                lambda x: V3.validate_literal_response(x, self.pair, self.payload))
            self.assertTrue(result["literal_model_output_preserved"])
            self.assertEqual(destination.read_text(encoding="utf-8"), literal + "\n")

    def test_native_body_extraction_excludes_anchor_text_and_rederives_raw_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "native.json"
            raw_obj = {"itemInfo": {"itemTitle": "対象商品",
                "itemComment": "<div><p>全サイズ共通でアルミ製です。</p><a href='/other'>関連商品の説明</a></div>",
                "skuInfo": {"optionName": {"row": "サイズ", "column": ""},
                    "rowNames": ["S", "M"], "columnNames": [""]}}}
            source.write_text(json.dumps(raw_obj, ensure_ascii=False), encoding="utf-8")
            digest = V3.sha_file(source)
            rel = "native.json"
            candidate_source = root / "candidate.json"
            candidate_source.write_text(json.dumps({"condition": "アルミ"}, ensure_ascii=False), encoding="utf-8")
            candidate_span = V3.NLI.SOURCES.json_leaf_span(V3.NLI.SOURCES.RawStore(root),
                "candidate.json", "$.condition", "アルミ")
            item_title = raw_obj["itemInfo"]["itemTitle"]
            comment = raw_obj["itemInfo"]["itemComment"]
            title_span = V3.NLI.SOURCES.json_leaf_span(V3.NLI.SOURCES.RawStore(root), rel,
                "$.itemInfo.itemTitle", item_title)
            doc = {"document_id": "doc:synthetic", "document_sha256": "d" * 64, "side": "au",
                "title": {"text": item_title, "source_refs": [title_span]},
                "description": {"segments": [{"source_refs": [{"raw_file": rel,
                    "sha256": digest, "locator": "$.itemInfo.itemComment"}]}]}}
            body = V3._source_body(V3.NLI.SOURCES.RawStore(root), doc)
            inventory = V3._target_sku_inventory(V3.NLI.SOURCES.RawStore(root), doc)
            texts = [block["support_text"] for block in body["blocks"]]
            self.assertTrue(any("全サイズ共通でアルミ製" in value for value in texts))
            self.assertFalse(any("関連商品の説明" in value for value in texts))
            for block in body["blocks"]:
                self.assertTrue(V3.NLI.SOURCES.verify_span(V3.NLI.SOURCES.RawStore(root), block["source_ref"]))
            damaged = copy.deepcopy(body)
            damaged["blocks"][0]["support_text"] += " 改変"
            option_id = V3.NLI._span_id(candidate_span)
            pair = {"pair_id": "pair:1", "cache_key": "cache:1", "option_values": ["アルミ"],
                "candidate_vector": [{"option_index": 0, "whole_option": "アルミ", "option_evidence_id": option_id}]}
            support_block = next(i for i, b in enumerate(body["blocks"]) if "全サイズ共通" in b["support_text"])
            selected = body["blocks"][support_block]
            payload = {"context": {"target_document_sha256": "d" * 64, "target_selected_facts": [],
                "hypothesis": {"family_members": [{"whole_option": "アルミ", "evidence_id": option_id,
                    "span": candidate_span}]},
                "eligible_target_body": {"document_sha256": "d" * 64, "status": body["status"], "blocks": [
                    {"body_index": i, "block_id": b["block_id"], "field": b["field"],
                     "support_text": b["support_text"], "source_sha256": b["source_ref"]["sha256"],
                     "passage": b["passage"], "source_alias": f"body-source:{'d' * 16}:{i}",
                     "links_present": b.get("links_present", False)} for i, b in enumerate(body["blocks"])]},
                "target_native_sku_inventory": V3._public_inventory(inventory)}}
            decision = {"option_index": 0, "whole_option": "アルミ", "option_evidence_id": option_id,
                "whole_value_assessed": True, "state": "unknown", "scope_assessment": "uncertain",
                "semantic_relation": "uncertain", "scope_and_coordinate_explanation": "不明瞭。",
                "option_relation_explanation": "条件が結び付かない。", "exclusivity_assessment": "uncertain",
                "exclusivity_explanation": "不明。", "grounding_route": "none", "target_fact_indices": [],
                "body_invariant": None, "body_quote_refs": [], "body_condition_bindings": [],
                "direct_contradiction_explanation": ""}
            response = {"protocol": V3.PROTOCOL, "pair_id": "pair:1", "cache_key": "cache:1", "decisions": [decision]}
            # `none` route needs no citations, but every saved body block still has
            # to rederive before it can be included in the source-bound context.
            with self.assertRaisesRegex(ValueError, "source block text/ref"):
                V3.validate_source_response(response, pair, payload, damaged, root)
            payload["context"]["eligible_target_body"]["blocks"][0]["support_text"] += " 改変"
            with self.assertRaisesRegex(ValueError, "payload eligible body block inventory"):
                V3.validate_source_response(response, pair, payload, body, root)
            payload["context"]["eligible_target_body"]["blocks"][0]["support_text"] = selected["support_text"]
            payload["context"]["target_native_sku_inventory"]["all_option_states"].append({"invented": True})
            with self.assertRaisesRegex(ValueError, "native target SKU inventory"):
                V3.validate_source_response(response, pair, payload, body, root)
            payload["context"]["target_native_sku_inventory"]["all_option_states"] = V3._public_inventory(inventory)["all_option_states"]
            payload["context"]["eligible_target_body"]["title"] = {"text": "改変タイトル", "source_aliases": ["target-title:0"]}
            with self.assertRaisesRegex(ValueError, "unverified title metadata"):
                V3.validate_source_response(response, pair, payload, body, root)

    def test_duplicate_native_axis_captions_keep_separate_coordinate_states(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "au.json"
            raw_obj = {"itemInfo": {"itemTitle": "対象商品", "skuInfo": {
                "optionName": {"row": "色", "column": "色"},
                "rowNames": ["赤", "青"], "columnNames": ["赤", "青"],
                "stockList": [[{}, {}], [{}, {}]]}}}
            source.write_text(json.dumps(raw_obj, ensure_ascii=False), encoding="utf-8")
            digest = V3.sha_file(source)
            title_span = V3.NLI.SOURCES.json_leaf_span(V3.NLI.SOURCES.RawStore(root),
                "au.json", "$.itemInfo.itemTitle", "対象商品")
            doc = {"side": "au", "document_sha256": "e" * 64,
                "title": {"source_refs": [title_span]}, "description": {"segments": []}}
            inventory = V3._target_sku_inventory(V3.NLI.SOURCES.RawStore(root), doc)
            self.assertEqual(inventory["status"], "verified_native_axes")
            self.assertEqual(len(inventory["all_option_states"]), 4)
            self.assertEqual([v["axis_kind"] for v in inventory["all_option_states"][0]["values"]],
                ["row", "column"])
            self.assertEqual([v["axis_index"] for v in inventory["all_option_states"][0]["values"]], [0, 1])


if __name__ == "__main__":
    unittest.main()
