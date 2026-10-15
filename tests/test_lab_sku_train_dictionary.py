"""Synthetic train-only tests for the library SKU whole-value dictionary."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
from library_train_dictionary_v1 import build_dictionary, dictionary_request_state  # noqa: E402


class TrainDictionaryTests(unittest.TestCase):
    def make_population(self, dossiers=1, source_value="navy", target_value="紺", groups=None,
                        au_extra=None, source_axis="楽天サイズ", target_axis="サイズ"):
        products, cases, requests, documents, refs, group_map = [], [], [], [], {}, {}
        for i in range(dossiers):
            dossier = f"d{i}"
            group_map[dossier] = groups[i] if groups else f"g{i}"
            source_values = ["common-a", "common-b", source_value]
            target_values = ["common-a", "common-b", target_value]
            if au_extra:
                target_values.extend(au_extra)
            au_rows = [{"row_key": f"{dossier}:r{j}", "axes": [{"axis_name": target_axis, "value": value}]}
                       for j, value in enumerate(target_values)]
            products.append({"dossier_id": dossier, "au_rows": au_rows,
                             "rakuten_product": {"selector_families": [
                                 {"label": source_axis, "values": source_values}]}})
            case_id = f"c{i}"
            cases.append({"case_id": case_id, "dossier_id": dossier,
                          "rakuten_selected": {"axes": [{"axis_label": source_axis, "value": source_value}]}})
            docs = [{"document_id": f"{dossier}:au", "dossier_id": dossier, "side": "au"},
                    {"document_id": f"{dossier}:rk", "dossier_id": dossier, "side": "rakuten"}]
            documents.extend(docs)
            # Requests provide complete native vectors for each axis, in each direction.
            requests.extend([
                {"request_id": f"{dossier}:r-au", "direction": "rakuten_to_au",
                 "target_document_id": f"{dossier}:au", "axis_name": source_axis,
                 "option_values": source_values},
                {"request_id": f"{dossier}:r-rk", "direction": "au_to_rakuten",
                 "target_document_id": f"{dossier}:rk", "axis_name": target_axis,
                 "option_values": target_values},
            ])
            refs[case_id] = {"label": "matched", "au_row_keys": [f"{dossier}:r{target_values.index(target_value)}"],
                             "source_answer_sha256": f"answer-{i}"}
        raw_values = {value for product in products for row in product["au_rows"]
                      for axis in row["axes"] for value in [axis["value"]]}
        raw_values.update(value for product in products
                          for family in product["rakuten_product"]["selector_families"]
                          for value in family["values"])
        raw_values.update(axis["value"] for case in cases
                          for axis in case["rakuten_selected"]["axes"])
        raw_values.update(value for request in requests for value in request["option_values"])
        return products, cases, requests, documents, {value: value for value in raw_values}, refs, group_map

    def train(self, **kwargs):
        min_support = kwargs.pop("min_support", 1)
        return build_dictionary(*self.make_population(**kwargs), min_support=min_support)

    def runtime_view(self, catalogs, *requests):
        values = {value for vectors in catalogs.values() for vector in vectors for value in vector}
        for request in requests:
            values.update(request.get("option_values", []))
            values.update(axis.get("value") for axis in request.get("target_selected_axes", []))
        return {value: value for value in values}

    def find(self, dictionary, kind, members):
        return next(e for e in dictionary["accepted_alias_groups"]
                    if e["kind"] == kind and set(e["members"]) == set(members))

    def test_learns_axis_and_train_matched_whole_value_alias(self):
        population = self.make_population()
        snapshot = copy.deepcopy(population)
        dictionary = build_dictionary(*population, min_support=1)
        self.assertEqual(set(self.find(dictionary, "axis", ["楽天サイズ", "サイズ"])["members"]),
                         {"楽天サイズ", "サイズ"})
        axis = self.find(dictionary, "axis", ["楽天サイズ", "サイズ"])["canonical"]
        value = self.find(dictionary, "value", ["navy", "紺"])
        self.assertEqual(value["axis_role"], axis)
        self.assertEqual(value["evidence_kind"], "train_reference_positive_whole_value")
        self.assertEqual(population, snapshot)

    def test_price_free_whole_value_mapping_is_learned_and_applied(self):
        products, cases, requests, docs, raw, refs, groups = self.make_population(
            dossiers=2, source_value="navy (limited)", target_value="濃紺・限定")
        raw["navy (limited)"] = "navy"
        raw["濃紺・限定"] = "濃紺"
        dictionary = build_dictionary(products, cases, requests, docs, raw, refs, groups, 1)
        axis = self.find(dictionary, "axis", ["楽天サイズ", "サイズ"])["canonical"]
        group = self.find(dictionary, "value", ["navy", "濃紺"])
        self.assertEqual(group["axis_role"], axis)
        self.assertNotIn("navy (limited)", group["members"])
        self.assertNotIn("濃紺・限定", group["members"])
        dossier = "d0"
        docs = {"target": {"dossier_id": dossier, "side": "au"}}
        catalogs = {(dossier, "rakuten", "楽天サイズ"):
                        {tuple(["common-a", "common-b", "navy (limited)"])},
                    (dossier, "au", "サイズ"):
                        {tuple(["common-a", "common-b", "濃紺・限定"])} }
        request = {"direction": "rakuten_to_au", "target_document_id": "target",
                   "axis_name": "楽天サイズ", "option_values": ["common-a", "common-b", "navy (limited)"],
                   "selected_value": "navy (limited)",
                   "target_selected_axes": [{"axis_name": "サイズ", "value": "濃紺・限定"}]}
        state, _, _ = dictionary_request_state(request, docs, catalogs, dictionary, raw)
        self.assertEqual(state, "support")

    def test_price_text_fields_are_not_alias_evidence(self):
        products, cases, requests, docs, raw, refs, groups = self.make_population(dossiers=2)
        for product in products:
            product["price_text"] = "送料無料"
            product["rakuten_product"]["price_text"] = "送料込"
            product["au_product"] = {"price_text": "税込"}
        for case in cases:
            case["rakuten_selected"]["price_text"] = "送料無料"
        dictionary = build_dictionary(products, cases, requests, docs, raw, refs, groups, 1)
        alias_members = {member for entry in dictionary["accepted_alias_groups"]
                         if entry["kind"] == "value" for member in entry["members"]}
        self.assertTrue({"navy", "紺"}.issubset(alias_members))
        self.assertTrue({"送料無料", "送料込", "税込"}.isdisjoint(alias_members))

    def test_locally_ambiguous_cross_side_axis_matches_are_blocked(self):
        products, cases, requests, docs, raw, refs, groups = self.make_population(dossiers=2)
        # Every AU row exposes a second axis with the same shared values. The
        # Rakuten source axis now has two local raw-overlap matches on AU.
        for product in products:
            for row in product["au_rows"]:
                row["axes"].append({"axis_name": "寸法", "value": row["axes"][0]["value"]})
        dictionary = build_dictionary(products, cases, requests, docs, raw, refs, groups, 1)
        ambiguous = next(e for e in dictionary["blocked_candidates"]
                         if e["kind"] == "axis" and "楽天サイズ" in e["members"]
                         and "サイズ" in e["members"])
        self.assertEqual(ambiguous["blocked_reason"], "same_side_axis_collision")
        self.assertFalse(any(e["kind"] == "axis" and "楽天サイズ" in e["members"]
                             for e in dictionary["accepted_alias_groups"]))

    def test_numeric_containing_aliases_are_rejected_including_decimals_and_model_ids(self):
        for src, dst in [("26.5", "25.5"), ("modelA12", "modelB12")]:
            with self.subTest(src=src):
                dictionary = self.train(source_value=src, target_value=dst)
                self.assertFalse(any(e["kind"] == "value" and set(e["members"]) == {src, dst}
                                     for e in dictionary["accepted_alias_groups"]))

    def test_support_counts_independent_groups_not_rows_or_skus(self):
        products, cases, requests, docs, raw, refs, groups = self.make_population(
            dossiers=1, au_extra=[f"other-{i}" for i in range(20)])
        # Multiple AU catalog rows in the same product/group are still one group.
        dictionary = build_dictionary(products, cases, requests, docs, raw, refs, groups, min_support=2)
        blocked = next(e for e in dictionary["blocked_candidates"]
                       if e["kind"] == "axis" and set(e["members"]) == {"楽天サイズ", "サイズ"})
        self.assertEqual(blocked["supporting_product_ids"], ["d0"])
        self.assertEqual(blocked["supporting_group_ids"], ["g0"])
        self.assertEqual(blocked["blocked_reason"], "insufficient_independent_source_groups")

    def test_two_alias_members_coexisting_in_native_option_domain_are_blocked(self):
        dictionary = self.train(dossiers=2, au_extra=["navy"])
        self.assertFalse(any(e["kind"] == "value" and set(e["members"]) == {"navy", "紺"}
                             for e in dictionary["accepted_alias_groups"]))
        blocked = next(e for e in dictionary["blocked_candidates"]
                       if e["kind"] == "value" and set(e["members"]) == {"navy", "紺"})
        self.assertEqual(blocked["blocked_reason"], "same_native_axis_value_collision")

    def test_all_allowed_gold_rows_must_agree(self):
        products, cases, requests, docs, raw, refs, groups = self.make_population()
        products[0]["au_rows"].append({"row_key": "d0:alternate", "axes": [{"axis_name": "サイズ", "value": "別色"}]})
        refs["c0"]["au_row_keys"].append("d0:alternate")
        raw["別色"] = "別色"
        dictionary = build_dictionary(products, cases, requests, docs, raw, refs, groups, 1)
        self.assertFalse(any(e["kind"] == "value" and set(e["members"]) == {"navy", "紺"}
                             for e in dictionary["accepted_alias_groups"]))

    def test_foreign_case_and_document_inputs_fail_closed(self):
        products, cases, requests, docs, raw, refs, groups = self.make_population()
        with self.assertRaisesRegex(ValueError, "non-TRAIN case"):
            build_dictionary(products, cases + [{"case_id": "eval", "dossier_id": "eval-d"}],
                             requests, docs, raw, {**refs, "eval": {"label": "unmatched"}}, groups, 1)
        with self.assertRaisesRegex(ValueError, "non-TRAIN document"):
            build_dictionary(products, cases, requests,
                             docs + [{"document_id": "eval-doc", "dossier_id": "eval-d", "side": "au"}],
                             raw, refs, groups, 1)

    def test_runtime_preserves_unknowns_and_never_mutates_request_or_catalogs(self):
        dictionary = self.train(dossiers=2)
        dossier = "d0"
        docs = {f"{dossier}:au": {"dossier_id": dossier, "side": "au"},
                f"{dossier}:rk": {"dossier_id": dossier, "side": "rakuten"}}
        catalogs = {(dossier, "rakuten", "楽天サイズ"): {("common-a", "common-b", "navy")},
                    (dossier, "au", "サイズ"): {("common-a", "common-b", "紺")}}
        request = {"request_id": "runtime", "direction": "rakuten_to_au", "target_document_id": f"{dossier}:au",
                   "axis_name": "楽天サイズ", "option_values": ["common-a", "common-b", "navy"],
                   "selected_value": "navy", "target_selected_axes": [{"axis_name": "サイズ", "value": "紺"}]}
        original = copy.deepcopy((request, catalogs))
        state, _, trace = dictionary_request_state(request, docs, catalogs, dictionary, {v: v for values in catalogs.values() for vec in values for v in vec})
        self.assertEqual(state, "support")
        self.assertEqual((request, catalogs), original)
        self.assertEqual(trace["selected_value_raw"], "navy")

        # A newly seen catalog collision makes the axis ambiguous and stays unknown.
        collision_docs = dict(docs)
        collision_catalogs = dict(catalogs)
        collision_catalogs[(dossier, "au", "楽天サイズ")] = {("a", "b")}
        state, reason, _ = dictionary_request_state(request, collision_docs, collision_catalogs, dictionary,
                                                    self.runtime_view(collision_catalogs, request))
        self.assertEqual(state, "unknown")
        self.assertEqual(reason, "dictionary_axis_catalog_collision")

    def test_runtime_missing_counterpart_is_unknown_and_reverse_direction_works(self):
        dictionary = self.train(dossiers=2)
        dossier = "d0"
        docs = {"target": {"dossier_id": dossier, "side": "rakuten"}}
        catalogs = {(dossier, "au", "サイズ"): {("common-a", "common-b", "紺")},
                    (dossier, "rakuten", "楽天サイズ"): {("common-a", "common-b", "navy")}}
        request = {"direction": "au_to_rakuten", "target_document_id": "target", "axis_name": "サイズ",
                   "option_values": ["common-a", "common-b", "紺"], "selected_value": "紺",
                   "target_selected_axes": [{"axis_name": "楽天サイズ", "value": "navy"}]}
        state, _, _ = dictionary_request_state(request, docs, catalogs, dictionary,
                                              self.runtime_view(catalogs, request))
        self.assertEqual(state, "support")
        missing = dict(catalogs)
        missing.pop((dossier, "au", "サイズ"))
        state, reason, _ = dictionary_request_state(request, docs, missing, dictionary, {})
        self.assertEqual(state, "unknown")
        self.assertEqual(reason, "dictionary_axis_counterpart_missing_or_ambiguous")

    def test_distinct_native_choices_with_shared_counterpart_are_conflict(self):
        dictionary = self.train(dossiers=2)
        dossier = "d0"
        docs = {"target": {"dossier_id": dossier, "side": "au"}}
        catalogs = {(dossier, "rakuten", "楽天サイズ"): {("common-a", "common-b", "navy", "black")},
                    (dossier, "au", "サイズ"): {("common-a", "common-b", "紺", "黒")}}
        request = {"direction": "rakuten_to_au", "target_document_id": "target", "axis_name": "楽天サイズ",
                   "option_values": ["common-a", "common-b", "navy", "black"], "selected_value": "navy",
                   "target_selected_axes": [{"axis_name": "サイズ", "value": "黒"}]}
        state, reason, _ = dictionary_request_state(request, docs, catalogs, dictionary,
                                                    self.runtime_view(catalogs, request))
        self.assertEqual(state, "conflict")
        self.assertEqual(reason, "different_native_options_under_shared_identifier")

    def test_unmapped_source_axis_family_is_retained_as_unknown(self):
        dictionary = self.train(dossiers=2)
        dossier = "d0"
        docs = {"target": {"dossier_id": dossier, "side": "au"}}
        catalogs = {(dossier, "rakuten", "unmapped"): {("x", "y")},
                    (dossier, "au", "別軸"): {("x", "y")}}
        request = {"direction": "rakuten_to_au", "target_document_id": "target", "axis_name": "unmapped",
                   "option_values": ["x", "y"], "selected_value": "x",
                   "target_selected_axes": [{"axis_name": "別軸", "value": "x"}]}
        state, reason, _ = dictionary_request_state(request, docs, catalogs, dictionary,
                                                    self.runtime_view(catalogs, request))
        self.assertEqual(state, "unknown")
        self.assertEqual(reason, "dictionary_axis_counterpart_missing_or_ambiguous")


if __name__ == "__main__":
    unittest.main()
