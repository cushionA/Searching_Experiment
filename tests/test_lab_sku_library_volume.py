"""Synthetic end-to-end coverage for the library-volume population runner."""
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
    import run_library_volume_v1 as volume
except ModuleNotFoundError as error:
    if error.name in {"run_library_volume_v1", "spacy", "networkx"}:
        volume = None
    else:
        raise


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                                for row in rows), encoding="utf-8")


def make_population(root: Path) -> tuple[Path, Path, Path, dict[str, list[dict]]]:
    source, original, output = root / "population", root / "original", root / "volume-output"
    source.mkdir()
    original.mkdir()
    requests: list[dict] = []
    documents: list[dict] = []
    cases: list[dict] = []
    rows: list[dict] = []
    tasks: list[dict] = []
    products: list[dict] = []
    native_axes_by_product = {
        "p1": {"au": {"Color": ["red", "blue"], "Material": ["cotton", "linen"]},
               "rakuten": {"Color": ["red", "blue"], "Fabric": ["cotton", "linen"]}},
        # These values overlap product p1, but this product has only one shared
        # value per candidate edge. It must not borrow p1's matching evidence.
        "p2": {"au": {"Color": ["red", "blue"], "Material": ["wool", "silk"]},
               "rakuten": {"Tone": ["red", "green"], "Weave": ["wool", "fiber"]}},
    }
    source_vectors = {
        "p1": {"au": {"Color": ["red", "blue"], "Material": ["cotton", "linen"]},
               "rakuten": {"Color": ["red", "blue"], "Fabric": ["cotton", "linen"]}},
        "p2": {"au": {"Color": ["red", "blue"], "Material": ["wool", "silk"]},
               "rakuten": {"Tone": ["red", "green"], "Weave": ["wool", "fiber"]}},
    }

    for dossier, sides in native_axes_by_product.items():
        sha_values = {"au": f"synthetic-au-{dossier}",
                      "rakuten": f"synthetic-rakuten-{dossier}"}
        products.append({
            "dossier_id": dossier,
            "au_product": {"sha256": sha_values["au"]},
            "rakuten_product": {"sha256": sha_values["rakuten"],
                "selector_families": [{"label": axis, "values": values}
                                      for axis, values in sides["rakuten"].items()]},
            "au_rows": [
                {"row_key": f"{dossier}-row-1", "axes": [
                    {"axis_name": "Color", "value": "red"},
                    {"axis_name": "Material", "value": ("cotton" if dossier == "p1" else "wool")}]},
                {"row_key": f"{dossier}-row-2", "axes": [
                    {"axis_name": "Color", "value": "blue"},
                    {"axis_name": "Material", "value": ("linen" if dossier == "p1" else "silk")}]},
            ],
        })
        for side in ("au", "rakuten"):
            documents.append({"document_id": f"{dossier}-{side}", "dossier_id": dossier,
                              "side": side, "raw_sha256": sha_values[side]})
        case_id = f"case-{dossier}"
        case_rows = [f"{dossier}-row-1", f"{dossier}-row-2"]
        cases.append({"case_id": case_id, "dossier_id": dossier, "row_keys": case_rows})

        # Four complete source-family requests cover both products' native
        # source axes in both directions. The target selections retain all
        # opposite-side selector values, including names that do not correspond.
        request_specs = [
            ("au_to_rakuten", "Color", sides["au"]["Color"], sides["au"]["Color"][0],
             [{"axis_name": axis, "value": values[0]} for axis, values in sides["rakuten"].items()], "rakuten"),
            ("au_to_rakuten", "Material", sides["au"]["Material"], sides["au"]["Material"][0],
             [{"axis_name": axis, "value": values[0]} for axis, values in sides["rakuten"].items()], "rakuten"),
            ("rakuten_to_au", next(axis for axis in sides["rakuten"] if axis in {"Color", "Tone"}),
             sides["rakuten"][next(axis for axis in sides["rakuten"] if axis in {"Color", "Tone"})],
             sides["rakuten"][next(axis for axis in sides["rakuten"] if axis in {"Color", "Tone"})][0],
             [{"axis_name": axis, "value": values[0]} for axis, values in sides["au"].items()], "au"),
            ("rakuten_to_au", next(axis for axis in sides["rakuten"] if axis in {"Fabric", "Weave"}),
             sides["rakuten"][next(axis for axis in sides["rakuten"] if axis in {"Fabric", "Weave"})],
             sides["rakuten"][next(axis for axis in sides["rakuten"] if axis in {"Fabric", "Weave"})][0],
             [{"axis_name": axis, "value": values[0]} for axis, values in sides["au"].items()], "au"),
        ]
        product_requests = []
        for index, (direction, axis, values, selected, target_axes, target_side) in enumerate(request_specs):
            request_id = f"{dossier}-request-{index + 1}"
            request = {"request_id": request_id, "target_document_id": f"{dossier}-{target_side}",
                       "direction": direction, "axis_name": axis, "option_values": list(values),
                       "selected_value": selected, "source_family_status": "complete",
                       "target_selected_axes": target_axes}
            requests.append(request)
            product_requests.append(request)

        for row_index, row_key in enumerate(case_rows):
            bindings = []
            for request in product_requests:
                task_id = f"task-{row_key}-{request['request_id']}"
                task = {"task_id": task_id, "case_id": case_id, "au_row_key": row_key,
                        "request_id": request["request_id"], "direction": request["direction"],
                        "axis_key": request["axis_name"],
                        "raw_condition_ref": {"axis_name": request["axis_name"],
                                              "value": request["selected_value"]}}
                tasks.append(task)
                bindings.append({"task_id": task_id, "request_id": request["request_id"],
                                 "axis_key": request["axis_name"]})
            rows.append({"case_id": case_id, "dossier_id": dossier,
                         "au_row_index": row_index,
                         "au_row_key": row_key, "bindings": bindings})

    original_cases_path, original_products_path = original / "cases.jsonl", original / "products.jsonl"
    write_jsonl(original_cases_path, [{"case_id": row["case_id"], "dossier_id": row["dossier_id"]}
                                     for row in cases])
    write_jsonl(original_products_path, products)
    source_rows = {"requests.jsonl": requests, "documents.jsonl": documents,
                   "row-plans.jsonl": rows, "case-plans.jsonl": cases, "tasks.jsonl": tasks}
    output_sha256 = {}
    for name, values in source_rows.items():
        write_jsonl(source / name, values)
        output_sha256[name] = sha(source / name)
    manifest = {"schema_version": "synthetic-test", "split": "synthetic", 
                "unique_request_count": len(requests), "document_count": len(documents),
                "row_plan_count": len(rows), "case_count": len(cases),
                "task_binding_count": len(tasks), "product_count": len(products),
                "output_sha256": output_sha256,
                "source_inputs": {"cases.jsonl": sha(original_cases_path),
                                  "products.jsonl": sha(original_products_path)}}
    (source / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    raw_values = {value for request in requests for value in request["option_values"]}
    raw_values.update(axis["value"] for request in requests for axis in request["target_selected_axes"])
    raw_view = {value: value for value in sorted(raw_values)}
    code_pins = {
        "installed_library_views_v1.py": sha(volume.HERE / "installed_library_views_v1.py"),
        "prepare_library_price_free_views_v1.py": sha(volume.HERE / "prepare_library_price_free_views_v1.py"),
    }
    view_metadata = {
        "schema": "installed-library-price-free-views-v1",
        "trained_components": [],
        "neural_model_loaded": False,
        "classifier_use_clf_forced_false": True,
        "classifier_flag_restored": True,
        "all_nonmasked_original_characters_preserved": True,
        "currency_span_map_failures": 0,
        "input_code_sha256": code_pins,
        "input_sha256": "synthetic-input-sha256",
        "versions": {"fixture": "synthetic"},
        "input_count": len(raw_view),
        "unique_input_count": len(raw_view),
        "currency_entity_hits": 0,
        "japanese_yen_spans_masked": 0,
    }
    views = {**view_metadata, "views": {"raw": raw_view}}
    views_path = root / "views.json"
    views_path.write_text(json.dumps(views, sort_keys=True), encoding="utf-8")
    audit = {**view_metadata, "per_original_raw_key": {
        value: {"original_raw": value, "price_free_raw": value, "mask_source_spans": []}
        for value in raw_view}}
    views_path.with_name("price-exclusion-audit.json").write_text(
        json.dumps(audit, sort_keys=True), encoding="utf-8")
    return source, original, views_path, {"rows": rows, "requests": requests,
                                         "documents": documents, "cases": cases,
                                         "tasks": tasks, "products": products,
                                         "output": output}


@unittest.skipIf(volume is None, "install the native libraries in the dedicated test venv")
class LibraryVolumePopulationTests(unittest.TestCase):
    def test_small_dynamic_two_product_run_retains_candidates_conditions_catalogs_and_pins(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, original, views_path, fixture = make_population(root)
            output = fixture["output"]
            volume.run(source, original, views_path, output)

            freeze = json.loads((output / "RUN_FREEZE.json").read_text(encoding="utf-8"))
            counts = freeze["counts"]
            self.assertEqual(counts["requests"], len(fixture["requests"]))
            self.assertEqual(counts["documents"], len(fixture["documents"]))
            self.assertEqual(counts["products"], len(fixture["products"]))
            self.assertEqual(counts["rows"], len(fixture["rows"]))
            self.assertEqual(counts["cases"], len(fixture["cases"]))
            self.assertEqual(counts["tasks"], len(fixture["tasks"]))
            self.assertEqual(counts["requests"], 8)
            self.assertEqual(counts["documents"], 4)
            self.assertEqual(counts["products"], 2)
            self.assertEqual(counts["rows"], 4)
            self.assertEqual(counts["cases"], 2)
            self.assertEqual(counts["tasks"], 16)
            self.assertEqual(counts["native_families_by_side"], {"au": 4, "rakuten": 4})

            native_catalogs = [json.loads(line) for line in
                               (output / "native-catalogs.jsonl").read_text(encoding="utf-8").splitlines()]
            actual_native = {(row["dossier_id"], row["side"], row["axis_name"])
                             for row in native_catalogs}
            expected_native = {
                ("p1", "au", "Color"), ("p1", "au", "Material"),
                ("p1", "rakuten", "Color"), ("p1", "rakuten", "Fabric"),
                ("p2", "au", "Color"), ("p2", "au", "Material"),
                ("p2", "rakuten", "Tone"), ("p2", "rakuten", "Weave"),
            }
            self.assertEqual(actual_native, expected_native)
            self.assertTrue(all(row["all_native_axes_retained"] for row in native_catalogs))
            expected_domains = {
                ("p1", "au", "Color"): ["red", "blue"],
                ("p1", "au", "Material"): ["cotton", "linen"],
                ("p1", "rakuten", "Color"): ["red", "blue"],
                ("p1", "rakuten", "Fabric"): ["cotton", "linen"],
                ("p2", "au", "Color"): ["red", "blue"],
                ("p2", "au", "Material"): ["wool", "silk"],
                ("p2", "rakuten", "Tone"): ["red", "green"],
                ("p2", "rakuten", "Weave"): ["wool", "fiber"],
            }
            for row in native_catalogs:
                key = (row["dossier_id"], row["side"], row["axis_name"])
                self.assertEqual(row["request_option_values"], expected_domains[key])
                self.assertEqual(row["native_catalog_values"], expected_domains[key])
                self.assertTrue(row["valid"])

            graph_records = [json.loads(line) for line in
                             (output / "graph-evidence.jsonl").read_text(encoding="utf-8").splitlines()]
            graph_by_product = {row["dossier_id"]: row for row in graph_records}
            self.assertEqual(set(graph_by_product), {"p1", "p2"})
            self.assertEqual({(edge["source_axis_name"], edge["target_axis_name"])
                              for edge in graph_by_product["p1"]["edges"]},
                             {("Color", "Color"), ("Material", "Fabric")})
            self.assertEqual(graph_by_product["p2"]["edges"], [])

            observations = [json.loads(line) for line in
                            (output / "request-observations.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["request_id"] for row in observations],
                             [request["request_id"] for request in fixture["requests"]])
            self.assertTrue(all(row["all_original_conditions_retained"] for row in observations))
            self.assertTrue(all(len(row["target_selected_axes"]) == 2 for row in observations))

            for method in volume.METHODS:
                method_requests = [json.loads(line) for line in
                                   (output / f"{method}-requests.jsonl").read_text(encoding="utf-8").splitlines()]
                method_rows = [json.loads(line) for line in
                               (output / f"{method}-rows.jsonl").read_text(encoding="utf-8").splitlines()]
                method_cases = [json.loads(line) for line in
                                (output / f"{method}-cases.jsonl").read_text(encoding="utf-8").splitlines()]
                self.assertEqual(len(method_requests), len(fixture["requests"]))
                self.assertEqual(len(method_rows), len(fixture["rows"]))
                self.assertEqual(len(method_cases), len(fixture["cases"]))
                self.assertTrue(all(row["condition_count"] == 4 for row in method_rows))
                self.assertTrue(all(row["raw_known_preserved"]
                                    for row in [counts["methods"][method]]))
                for request in method_requests:
                    baseline = request["raw_baseline_state"]
                    if baseline != "unknown":
                        self.assertEqual(request["state"], baseline)

            output_pins = freeze["output_pins"]
            output_files = freeze["output_files"]
            self.assertEqual(set(output_pins), set(output_files))
            self.assertNotIn("RUN_FREEZE.json", output_pins)
            for name, digest in output_pins.items():
                path = output / name
                self.assertTrue(path.is_file())
                self.assertEqual(sha(path), digest)
                self.assertEqual(path.stat().st_size, output_files[name]["bytes"])
            audit_path = views_path.with_name("price-exclusion-audit.json")
            self.assertEqual(freeze["input_pins"][audit_path.resolve().as_posix()], sha(audit_path))

    def test_unknown_competitor_never_makes_a_case_accept(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            requests = [{"request_id": "candidate-support"},
                        {"request_id": "candidate-unknown"}]
            rows = [{"case_id": "one-case", "au_row_key": "one-candidate",
                     "bindings": [{"request_id": "candidate-support"}]},
                    {"case_id": "one-case", "au_row_key": "unknown-competitor",
                     "bindings": [{"request_id": "candidate-unknown"}]}]
            records = [{"request_id": "candidate-support", "state": "support",
                        "raw_baseline_state": "unknown"},
                       {"request_id": "candidate-unknown", "state": "unknown",
                        "raw_baseline_state": "unknown"}]
            counts = volume.emit_method(output, "synthetic", requests, rows,
                                        {"candidate-support": "support",
                                         "candidate-unknown": "unknown"}, records)
            decisions = [json.loads(line) for line in
                         (output / "synthetic-cases.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(counts["cases"], {"review": 1})
            self.assertEqual(decisions[0]["decision"], "drop")
            self.assertEqual(decisions[0]["disposition"], "review")
            self.assertIsNone(decisions[0]["au_row_key"])

    def test_known_raw_state_is_immutable_and_hypothesis_disagreement_holds(self):
        # The volume runner imports this exact frozen hypothesis combiner.
        self.assertEqual(volume.combine_hypotheses(
            "conflict", {"state": "support"}, {"state": "support"}),
            ("conflict", "immutable_raw_baseline_known_state"))
        self.assertEqual(volume.combine_hypotheses(
            "unknown", {"state": "support"}, {"state": "conflict"}),
            ("unknown", "eligible_domain_and_component_hypotheses_disagree"))

    def test_preflight_rejects_corrupted_mask_raw_value_and_adapter_code_pin(self):
        corruptions = [
            ("bad-mask", "differs outside audited mask spans",
             lambda views, audit: audit["per_original_raw_key"]["red"].update(
                 {"mask_source_spans": [[0, 1]]})),
            ("bad-raw", "does not reconstruct the frozen raw view",
             lambda views, audit: audit["per_original_raw_key"]["red"].update(
                 {"price_free_raw": "not-red"})),
            ("bad-code-pin", "code pins differ",
             lambda views, audit: (
                 views["input_code_sha256"].update({"installed_library_views_v1.py": "wrong"}),
                 audit["input_code_sha256"].update({"installed_library_views_v1.py": "wrong"}))),
        ]
        for name, reason, corrupt in corruptions:
            with self.subTest(corruption=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, original, views_path, fixture = make_population(root)
                views = json.loads(views_path.read_text(encoding="utf-8"))
                audit_path = views_path.with_name("price-exclusion-audit.json")
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
                corrupt(views, audit)
                views_path.write_text(json.dumps(views, sort_keys=True), encoding="utf-8")
                audit_path.write_text(json.dumps(audit, sort_keys=True), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, reason):
                    volume.run(source, original, views_path, fixture["output"], preflight_only=True)
                self.assertFalse(fixture["output"].exists())


if __name__ == "__main__":
    unittest.main()
