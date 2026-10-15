"""Synthetic boundary tests for product-local NetworkX axis alignment."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = next(parent for parent in (HERE, *HERE.parents)
            if (parent / "experiments/sku-matching").is_dir())
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))

try:
    from library_axis_domain_alignment_v1 import build_product_graph, apply_policy_edges
except ModuleNotFoundError as error:
    if error.name in {"networkx", "library_axis_domain_alignment_v1"}:
        build_product_graph = None
        apply_policy_edges = None
    else:
        raise

try:
    import run_library_axis_domains_v1 as axis_runner
except ModuleNotFoundError as error:
    if error.name in {"run_library_axis_domains_v1", "networkx"}:
        axis_runner = None
    else:
        raise


def family(axis, values, *, valid=True, reason=None, vectors=None):
    return {
        "axis_name": axis,
        "valid": valid,
        "raw_values": list(values),
        "request_ids": [f"request-{axis}"],
        "invalid_reason": reason,
        "vectors": vectors if vectors is not None else [list(values)],
    }


def graph(au, rakuten, *, raw_view=None, norm_view=None, mode="raw"):
    all_values = {value for side in (au, rakuten) for item in side.values()
                  for value in item["raw_values"]}
    raw_view = raw_view or {value: value for value in all_values}
    norm_view = norm_view or dict(raw_view)
    return build_product_graph("synthetic-product", {"au": au, "rakuten": rakuten},
                               raw_view, norm_view, mode)


@unittest.skipIf(build_product_graph is None, "install NetworkX in the test environment")
class AxisDomainAlignmentAdapterTests(unittest.TestCase):
    def test_one_edge_requires_two_distinct_shared_values_and_is_forced(self):
        result = graph({"Color": family("Color", ["red", "blue"])},
                       {"Hue": family("Hue", ["red", "blue"])})
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["edges"]), 1)
        edge = result["edges"][0]
        self.assertEqual(edge["shared_keys"], ["blue", "red"])
        self.assertEqual(len(edge["shared_pairs"]), 2)
        self.assertEqual(apply_policy_edges(result, "mutual_unique"), {"Color": "Hue"})
        self.assertEqual(apply_policy_edges(result, "forced_maximum"), {"Color": "Hue"})

    def test_one_shared_value_never_creates_an_edge_and_isolated_nodes_remain(self):
        result = graph(
            {"Color": family("Color", ["red", "blue"]),
             "Material": family("Material", ["cotton", "linen"])},
            {"Hue": family("Hue", ["red", "green"]),
             "Extra": family("Extra", ["nylon", "wool"])})
        self.assertEqual(result["edges"], [])
        self.assertEqual(len(result["nodes"]), 4)
        self.assertEqual(result["maximum_cardinality"], 0)

    def test_k22_has_no_mutually_unique_or_forced_edges(self):
        au = {f"Source{i}": family(f"Source{i}", ["a", "b"])
              for i in (1, 2)}
        rakuten = {f"Target{i}": family(f"Target{i}", ["a", "b"])
                   for i in (1, 2)}
        result = graph(au, rakuten)
        self.assertEqual(len(result["edges"]), 4)
        self.assertEqual(result["maximum_cardinality"], 2)
        self.assertEqual(result["mutual_unique_edges"], [])
        self.assertEqual(result["forced_maximum_edges"], [])

    def test_augmenting_path_forces_only_the_globally_required_pairings(self):
        # S1 can map to either target; S2 can map only to T1. A local-degree
        # heuristic misses that both edges are forced by maximum cardinality.
        au = {"S1": family("S1", ["a1", "a2", "b1", "b2"]),
              "S2": family("S2", ["a1", "a2"])}
        rakuten = {"T1": family("T1", ["a1", "a2"]),
                   "T2": family("T2", ["b1", "b2"])}
        result = graph(au, rakuten)
        self.assertEqual(result["maximum_cardinality"], 2)
        self.assertEqual(len(result["edges"]), 3)
        self.assertEqual(apply_policy_edges(result, "forced_maximum"),
                         {"S1": "T2", "S2": "T1"})
        self.assertEqual(apply_policy_edges(result, "mutual_unique"), {})

    def test_any_invalid_or_incomplete_domain_holds_the_whole_graph(self):
        cases = [
            (family("Bad", ["x", "y"], valid=False,
                    reason="incomplete_family"), ["x", "y"]),
            (family("Bad", ["x", "y"], valid=False,
                    reason="multiple_native_vectors", vectors=[["x", "y"], ["x", "z"]]),
             ["x", "y"]),
            (family("Bad", ["x", "x"]), ["x"]),
        ]
        for bad, expected_reason in cases:
            with self.subTest(reason=bad["invalid_reason"], values=bad["raw_values"]):
                result = graph({"Good": family("Good", ["x", "y"]), "Bad": bad},
                               {"Target": family("Target", ["x", "y"])})
                self.assertEqual(result["status"], "held")
                self.assertEqual(result["edges"], [])
                self.assertEqual(len(result["nodes"]), 3)
                self.assertEqual(apply_policy_edges(result, "forced_maximum"), {})
                if bad["invalid_reason"]:
                    self.assertTrue(any(bad["invalid_reason"] in item
                                        for item in result["hold_reasons"]))

    def test_within_axis_normalization_collision_holds_and_keeps_the_competitor(self):
        au = {"Source": family("Source", ["ab", "ＡＢ"])}
        rk = {"Target": family("Target", ["ab", "ＡＢ"])}
        result = graph(au, rk, norm_view={"ab": "ab", "ＡＢ": "ab"}, mode="spacy_norm")
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["edges"], [])
        self.assertEqual(len(result["nodes"]), 2)
        self.assertTrue(any("within_axis_view_collision" in reason
                            for reason in result["hold_reasons"]))

    def test_raw_and_spacy_norm_are_separate_fixed_views(self):
        au = {"Source": family("Source", ["red", "ｂｌｕｅ"])}
        rk = {"Target": family("Target", ["red", "blue"])}
        raw = {"red": "red", "ｂｌｕｅ": "ｂｌｕｅ", "blue": "blue"}
        normalized = {"red": "red", "ｂｌｕｅ": "blue", "blue": "blue"}
        raw_result = graph(au, rk, raw_view=raw, norm_view=normalized, mode="raw")
        norm_result = graph(au, rk, raw_view=raw, norm_view=normalized, mode="spacy_norm")
        self.assertEqual(raw_result["edges"], [])
        self.assertEqual(len(norm_result["edges"]), 1)
        self.assertEqual(norm_result["edges"][0]["shared_keys"], ["blue", "red"])
        self.assertEqual(raw_result["view"], "raw")
        self.assertEqual(norm_result["view"], "spacy_norm")
        blue = next(pair for pair in norm_result["edges"][0]["shared_pairs"]
                    if pair["key"] == "blue")
        self.assertEqual(blue["au_raw_value"], "ｂｌｕｅ")
        self.assertFalse(norm_result["axis_correspondence_is_semantic_proof"])


@unittest.skipIf(axis_runner is None, "axis-domain runner is not available")
class AxisDomainRunnerIntegrationTests(unittest.TestCase):
    def make_request(self, direction="au_to_rakuten", axis="Color", selected="red",
                     options=None, target_axes=None, status="complete"):
        return {"request_id": "synthetic-request", "direction": direction,
                "axis_name": axis, "selected_value": selected,
                "option_values": options or ["red", "blue"],
                "source_family_status": status,
                "target_selected_axes": target_axes or [{"axis_name": "Hue", "value": "red"}]}

    def state(self, request, au, rakuten, *, policy="forced_maximum", mode="raw",
              raw_view=None, norm_view=None):
        values = {value for side in (au, rakuten) for item in side.values()
                  for value in item["raw_values"]}
        raw_view = raw_view or {value: value for value in values}
        norm_view = norm_view or dict(raw_view)
        families = {"au": au, "rakuten": rakuten}
        record = build_product_graph("synthetic-product", families, raw_view, norm_view, mode)
        result = axis_runner.graph_state(request, "synthetic-product", record, policy,
                                         mode, families, raw_view, norm_view)
        return result, record, families, raw_view, norm_view

    def test_graph_state_supports_a_unique_cross_name_selected_counterpart(self):
        au = {"Color": family("Color", ["red", "blue"])}
        rk = {"Hue": family("Hue", ["red", "blue"])}
        req = self.make_request(target_axes=[{"axis_name": "Hue", "value": "red"}])
        result, _, _, _, _ = self.state(req, au, rk)
        self.assertEqual(result["state"], "support")
        self.assertEqual((result["source_axis_name"], result["target_axis_name"]),
                         ("Color", "Hue"))

    def test_current_value_absent_from_target_is_unknown_not_conflict(self):
        au = {"Color": family("Color", ["red", "blue", "green"])}
        rk = {"Hue": family("Hue", ["red", "blue", "black"])}
        req = self.make_request(selected="green", options=["red", "blue", "green"],
                                target_axes=[{"axis_name": "Hue", "value": "green"}])
        result, _, _, _, _ = self.state(req, au, rk)
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["reason"], "selected_source_key_not_unique_target_domain_member")

    def test_incomplete_source_family_and_nonmember_source_selection_hold(self):
        au = {"Color": family("Color", ["red", "blue"])}
        rk = {"Hue": family("Hue", ["red", "blue"])}
        incomplete = self.make_request(status="partial")
        result, _, _, _, _ = self.state(incomplete, au, rk)
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["reason"], "source_family_status_not_complete")
        missing_selected = self.make_request(selected="green")
        result, _, _, _, _ = self.state(missing_selected, au, rk)
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["reason"], "selected_source_value_not_unique_native_member")

    def test_target_selected_axis_must_be_unique(self):
        au = {"Color": family("Color", ["red", "blue"])}
        rk = {"Hue": family("Hue", ["red", "blue"])}
        req = self.make_request(target_axes=[{"axis_name": "Hue", "value": "red"},
                                             {"axis_name": "Hue", "value": "red"}])
        result, _, _, _, _ = self.state(req, au, rk)
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["reason"], "mapped_target_selected_axis_missing_or_ambiguous")

    def test_inconsistent_native_vectors_from_requests_hold_product_graph(self):
        requests = [
            {"request_id": "r1", "target_document_id": "doc-au", "direction": "au_to_rakuten",
             "axis_name": "Color", "option_values": ["red", "blue"],
             "source_family_status": "complete"},
            {"request_id": "r2", "target_document_id": "doc-au", "direction": "au_to_rakuten",
             "axis_name": "Color", "option_values": ["red", "blue", "green"],
             "source_family_status": "complete"},
            {"request_id": "r3", "target_document_id": "doc-rk", "direction": "rakuten_to_au",
             "axis_name": "Hue", "option_values": ["red", "blue"],
             "source_family_status": "complete"},
        ]
        docs = {"doc-au": {"document_id": "doc-au", "dossier_id": "d", "side": "au"},
                "doc-rk": {"document_id": "doc-rk", "dossier_id": "d", "side": "rakuten"}}
        grouped = axis_runner.collect_families(requests, docs)["d"]
        self.assertFalse(grouped["au"]["Color"]["valid"])
        self.assertEqual(grouped["au"]["Color"]["invalid_reason"],
                         "inconsistent_native_vectors_for_family")
        result = build_product_graph("d", grouped,
                                     {"red": "red", "blue": "blue", "green": "green"},
                                     {"red": "red", "blue": "blue", "green": "green"}, "raw")
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["edges"], [])

    def test_collect_families_keeps_literal_vectors_and_their_request_provenance(self):
        doc = {"doc": {"document_id": "doc", "dossier_id": "d", "side": "au"}}
        one = {"request_id": "one", "target_document_id": "doc", "direction": "au_to_rakuten",
               "axis_name": "Color", "option_values": ["red", "blue"],
               "source_family_status": "complete"}
        one_family = axis_runner.collect_families([one], doc)["d"]["au"]["Color"]
        self.assertEqual(one_family["raw_values"], ["red", "blue"])
        self.assertEqual(one_family["request_ids"], ["one"])
        self.assertEqual(one_family["vectors"], [
            {"option_values": ["red", "blue"], "request_ids": ["one"]},
        ])

        same_vector_repeat = {**one, "request_id": "repeat"}
        different_vector = {**one, "request_id": "different",
                            "option_values": ["red", "blue", "green"]}
        mixed = axis_runner.collect_families([one, same_vector_repeat, different_vector], doc)[
            "d"]["au"]["Color"]
        self.assertFalse(mixed["valid"])
        self.assertEqual(mixed["invalid_reason"], "inconsistent_native_vectors_for_family")
        self.assertEqual(mixed["raw_values"], [])
        self.assertEqual(mixed["request_ids"], ["one", "repeat", "different"])
        self.assertEqual(mixed["vectors"], [
            {"option_values": ["red", "blue", "green"], "request_ids": ["different"]},
            {"option_values": ["red", "blue"], "request_ids": ["one", "repeat"]},
        ])

    def test_population_loader_reads_five_pinned_jsonl_inputs_separately_from_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "population"
            original_dir = root / "original"
            source_dir.mkdir()
            original_dir.mkdir()
            file_records = {
                "requests.jsonl": [{"kind": "request", "request_id": "r"}],
                "documents.jsonl": [{"kind": "document", "document_id": "d"}],
                "row-plans.jsonl": [{"kind": "row", "row_key": "row-1"}],
                "case-plans.jsonl": [{"kind": "case", "case_id": "case-1"}],
                "tasks.jsonl": [{"kind": "task", "task_id": "task-1"}],
            }
            output_pins = {}
            for name, records in file_records.items():
                path = source_dir / name
                path.write_text("".join(json.dumps(record) + "\n" for record in records),
                                encoding="utf-8")
                output_pins[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            original = original_dir / "products.jsonl"
            original.write_text('{"dossier_id":"d"}\n', encoding="utf-8")
            manifest = {"unique_request_count": 1, "document_count": 1,
                        "row_plan_count": 1, "case_count": 1, "task_binding_count": 1,
                        "output_sha256": output_pins,
                        "source_inputs": {"products.jsonl": hashlib.sha256(
                            original.read_bytes()).hexdigest()}}
            (source_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            loaded_manifest, requests, documents, rows, cases, tasks = axis_runner.load_population_inputs(
                source_dir, original_dir)
            self.assertEqual(loaded_manifest, manifest)
            self.assertEqual(requests, file_records["requests.jsonl"])
            self.assertEqual(documents, file_records["documents.jsonl"])
            self.assertEqual(rows, file_records["row-plans.jsonl"])
            self.assertEqual(cases, file_records["case-plans.jsonl"])
            self.assertEqual(tasks, file_records["tasks.jsonl"])

    def test_population_loader_rejects_bad_counts_and_changed_source_pins(self):
        with tempfile.TemporaryDirectory() as temporary:
            source_dir = Path(temporary) / "population"
            original_dir = Path(temporary) / "original"
            source_dir.mkdir()
            original_dir.mkdir()
            for name in ("requests.jsonl", "documents.jsonl", "row-plans.jsonl",
                         "case-plans.jsonl", "tasks.jsonl"):
                (source_dir / name).write_text('{"item":"present"}\n', encoding="utf-8")
            manifest = {"unique_request_count": 2, "document_count": 1,
                        "row_plan_count": 1, "case_count": 1, "task_binding_count": 1,
                        "output_sha256": {}, "source_inputs": {}}
            manifest_path = source_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest counts"):
                axis_runner.load_population_inputs(source_dir)

            manifest["unique_request_count"] = 1
            original = original_dir / "products.jsonl"
            original.write_text('{"dossier_id":"d"}\n', encoding="utf-8")
            manifest["source_inputs"] = {"products.jsonl": "wrong-sha"}
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "original-input pin"):
                axis_runner.load_population_inputs(source_dir, original_dir)

    def product_catalog(self):
        return {"dossier_id": "d", "au_rows": [
                    {"axes": [{"axis_name": "Color", "value": "red"},
                              {"axis_name": "Color", "value": "blue"},
                              {"axis_name": "Material", "value": "cotton"},
                              {"axis_name": "Material", "value": "linen"}]}
                ], "rakuten_product": {"sha256": "product-rakuten-sha", "selector_families": [
                    {"label": "Hue", "values": ["red", "blue"]},
                    {"label": "Size", "values": ["S", "M"]},
                ]}}

    def test_native_only_and_request_only_axes_are_retained_as_invalid_nodes(self):
        families = {"d": {"au": {"Color": family("Color", ["red", "blue"]),
                                   "Custom": family("Custom", ["x", "y"])},
                          "rakuten": {"Hue": family("Hue", ["red", "blue"])}}}
        axis_runner.validate_native_catalog_coverage(families, [self.product_catalog()])
        au = families["d"]["au"]
        rk = families["d"]["rakuten"]
        self.assertEqual(au["Material"]["invalid_reason"], "native_axis_missing_from_request_catalog")
        self.assertEqual(au["Custom"]["invalid_reason"], "request_axis_missing_from_native_catalog")
        self.assertEqual(au["Material"]["raw_values"], ["cotton", "linen"])
        self.assertEqual(rk["Size"]["raw_values"], ["S", "M"])
        self.assertEqual(rk["Size"]["invalid_reason"], "native_axis_missing_from_request_catalog")
        all_values = {value for side in (au, rk) for item in side.values()
                      for value in item["raw_values"]}
        result = build_product_graph("d", families["d"],
                                     {value: value for value in all_values},
                                     {value: value for value in all_values}, "raw")
        self.assertEqual(result["status"], "held")
        self.assertEqual(len(result["nodes"]), 5)
        self.assertEqual(result["edges"], [])
        self.assertIn("AU::'Material'", {node["node_id"] for node in result["nodes"]})
        self.assertIn("AU::'Custom'", {node["node_id"] for node in result["nodes"]})

    def test_same_native_axis_with_a_different_domain_invalidates_product_graph(self):
        families = {"d": {"au": {"Color": family("Color", ["red", "green"])},
                          "rakuten": {"Hue": family("Hue", ["red", "blue"])}}}
        axis_runner.validate_native_catalog_coverage(families, [self.product_catalog()])
        self.assertFalse(families["d"]["au"]["Color"]["valid"])
        self.assertEqual(families["d"]["au"]["Color"]["invalid_reason"],
                         "request_option_domain_differs_from_native_catalog")
        self.assertEqual(families["d"]["au"]["Color"]["native_catalog_values"],
                         ["red", "blue"])
        all_values = {value for side in families["d"].values() for item in side.values()
                      for value in item["raw_values"]}
        result = build_product_graph("d", families["d"],
                                     {value: value for value in all_values},
                                     {value: value for value in all_values}, "raw")
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["edges"], [])

    def test_malformed_request_vectors_never_create_valid_graph_nodes(self):
        doc = {"doc": {"document_id": "doc", "dossier_id": "d", "side": "au"}}
        requests = [
            {"request_id": "malformed", "target_document_id": "doc", "direction": "au_to_rakuten",
             "axis_name": "Color", "option_values": "red|blue", "source_family_status": "complete"},
            {"request_id": "duplicate", "target_document_id": "doc", "direction": "au_to_rakuten",
             "axis_name": "Hue", "option_values": ["red", "red"], "source_family_status": "complete"},
        ]
        grouped = axis_runner.collect_families(requests, doc)["d"]["au"]
        self.assertFalse(grouped["Color"]["valid"])
        self.assertFalse(grouped["Hue"]["valid"])
        self.assertEqual(grouped["Color"]["raw_values"], [])
        self.assertEqual(grouped["Hue"]["invalid_reason"], "duplicate_raw_native_option")
        families = {"au": grouped, "rakuten": {"Hue": family("Hue", ["red", "blue"])}}
        result = build_product_graph("d", families,
                                     {"red": "red", "blue": "blue"},
                                     {"red": "red", "blue": "blue"}, "raw")
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["edges"], [])

    def test_duplicate_native_selector_values_are_not_hidden_by_request_deduplication(self):
        product = self.product_catalog()
        product["rakuten_product"]["selector_families"][0]["values"] = ["red", "red", "blue"]
        families = {"d": {"au": {"Color": family("Color", ["red", "blue"])},
                          "rakuten": {"Hue": family("Hue", ["red", "blue"])}}}
        axis_runner.validate_native_catalog_coverage(families, [product])
        self.assertFalse(families["d"]["rakuten"]["Hue"]["valid"])
        self.assertEqual(families["d"]["rakuten"]["Hue"]["invalid_reason"],
                         "duplicate_native_option")
        all_values = {value for side in families["d"].values() for item in side.values()
                      for value in item["raw_values"]}
        result = build_product_graph("d", families["d"],
                                     {value: value for value in all_values},
                                     {value: value for value in all_values}, "raw")
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["edges"], [])

    def test_duplicate_native_selector_axis_label_is_retained_as_local_hold(self):
        product = self.product_catalog()
        product["rakuten_product"]["selector_families"].append(
            {"label": "Hue", "values": ["green", "black"]})
        clean_product = self.product_catalog()
        clean_product["dossier_id"] = "clean"
        families = {
            "d": {"au": {"Color": family("Color", ["red", "blue"])},
                  "rakuten": {"Hue": family("Hue", ["red", "blue"])}},
            "clean": {"au": {"Color": family("Color", ["red", "blue"]),
                               "Material": family("Material", ["cotton", "linen"])},
                      "rakuten": {"Hue": family("Hue", ["red", "blue"]),
                                  "Size": family("Size", ["S", "M"])}}
        }
        axis_runner.validate_native_catalog_coverage(families, [product, clean_product])
        self.assertFalse(families["d"]["rakuten"]["Hue"]["valid"])
        self.assertEqual(families["d"]["rakuten"]["Hue"]["invalid_reason"],
                         "duplicate_native_axis_identifier")
        for dossier, expected_status in (("d", "held"), ("clean", "ready")):
            all_values = {value for side in families[dossier].values() for item in side.values()
                          for value in item["raw_values"]}
            result = build_product_graph(dossier, families[dossier],
                                         {value: value for value in all_values},
                                         {value: value for value in all_values}, "raw")
            self.assertEqual(result["status"], expected_status)
            if dossier == "d":
                self.assertEqual(result["edges"], [])

    def test_document_raw_sha_must_match_its_original_native_product_sha(self):
        product = self.product_catalog()
        products = [product]
        matching_document = [{"document_id": "doc-rk", "dossier_id": "d", "side": "rakuten",
                              "raw_sha256": "product-rakuten-sha"}]
        axis_runner.validate_document_catalog_hashes(matching_document, products)
        documents = [{**matching_document[0], "raw_sha256": "different-document-sha"}]
        with self.assertRaisesRegex(ValueError, "document raw source SHA"):
            axis_runner.validate_document_catalog_hashes(documents, products)

    def test_actual_target_choice_outside_its_native_domain_is_unknown(self):
        au = {"Color": family("Color", ["red", "blue"])}
        rk = {"Hue": family("Hue", ["red", "blue"])}
        req = self.make_request(target_axes=[{"axis_name": "Hue", "value": "green"}])
        result, _, _, _, _ = self.state(req, au, rk)
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["reason"], "actual_target_value_not_unique_native_member")

    def test_different_unique_target_choice_is_conflict(self):
        au = {"Color": family("Color", ["red", "blue"])}
        rk = {"Hue": family("Hue", ["red", "blue"])}
        req = self.make_request(target_axes=[{"axis_name": "Hue", "value": "blue"}])
        result, _, _, _, _ = self.state(req, au, rk)
        self.assertEqual(result["state"], "conflict")

    def test_same_raw_name_does_not_override_a_forced_cross_name_mapping(self):
        au = {"Color": family("Color", ["red", "blue"])}
        rk = {"Color": family("Color", ["green", "black"]),
              "Hue": family("Hue", ["red", "blue"])}
        req = self.make_request(target_axes=[{"axis_name": "Hue", "value": "red"}])
        result, _, _, _, _ = self.state(req, au, rk)
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["reason"], "same_raw_axis_name_exists_but_mapping_points_elsewhere")

    def test_reverse_direction_uses_the_inverse_forced_mapping(self):
        au = {"Color": family("Color", ["red", "blue"])}
        rk = {"Hue": family("Hue", ["red", "blue"])}
        req = self.make_request(direction="rakuten_to_au", axis="Hue",
                                target_axes=[{"axis_name": "Color", "value": "red"}])
        result, _, _, _, _ = self.state(req, au, rk)
        self.assertEqual(result["state"], "support")
        self.assertEqual((result["source_axis_name"], result["target_axis_name"]),
                         ("Hue", "Color"))

    def test_nonunknown_raw_baseline_state_is_preserved_and_disagreement_holds(self):
        supporting_graph = {"state": "support"}
        conflicting_component = {"state": "conflict"}
        self.assertEqual(axis_runner.combine_hypotheses("conflict", supporting_graph, None),
                         ("conflict", "immutable_raw_baseline_known_state"))
        self.assertEqual(axis_runner.combine_hypotheses("unknown", supporting_graph,
                                                         conflicting_component),
                         ("unknown", "eligible_domain_and_component_hypotheses_disagree"))
        self.assertEqual(axis_runner.combine_hypotheses(
            "unknown", {"state": "unknown"}, {"state": "support"})[0], "support")

    def test_unknown_competitor_prevents_row_acceptance(self):
        rows = [{"case_id": "case", "au_row_key": "candidate-support",
                 "bindings": [{"request_id": "support-condition"}]},
                {"case_id": "case", "au_row_key": "candidate-unknown",
                 "bindings": [{"request_id": "unknown-condition"}]}]
        requests = [{"request_id": "support-condition"},
                    {"request_id": "unknown-condition"}]
        row_results, decisions = axis_runner.assemble_mode(
            rows, requests, {"support-condition": "support", "unknown-condition": "unknown"})
        self.assertEqual([row["state"] for row in row_results], ["support", "unknown"])
        self.assertEqual(decisions[0]["decision"], "drop")
        self.assertIsNone(decisions[0]["au_row_key"])


if __name__ == "__main__":
    unittest.main()
