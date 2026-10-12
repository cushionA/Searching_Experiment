"""Synthetic source and sidecar checks for the fail-closed typed proof consumer."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
sys.path.insert(0, str(ROOT / "experiments/sku-matching/claude_v9_gate"))
import sku_gate_sources as sources  # noqa: E402
from verify_typed_value_equivalence_v1 import validate_sidecar  # noqa: E402


class TypedProofConsumerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = sources.RawStore(self.root)
        self.rak_path = "rak.html"
        self.au_path = "au.json"
        self.rak_bytes = b""
        self.au_obj = {"itemInfo": {"skuInfo": {
            "skuId": "sku1", "optionName": {"row": "サイズ", "column": "カラー"},
            "rowNames": ["Mサイズ", "Lサイズ", "LLサイズ"], "columnNames": ["黒"],
        }}}
        (self.root / self.rak_path).write_bytes(self.rak_bytes)
        (self.root / self.au_path).write_text(json.dumps(self.au_obj, ensure_ascii=False), encoding="utf-8")
        self.rak_sha = sources.sha256_file(self.root / self.rak_path)
        self.au_sha = sources.sha256_file(self.root / self.au_path)
        self.build_inputs()

    def tearDown(self):
        self.tmp.cleanup()

    def build_inputs(self, r_family=None, au_values=None, r_value="L", au_value="Lサイズ",
                     raw_r_family=None, au_kind="row"):
        r_family = r_family or ["M", "L", "LL"]
        raw_r_family = raw_r_family or r_family
        au_values = au_values or ["Mサイズ", "Lサイズ", "LLサイズ"]
        selectors = [{"key": "サイズ", "label": "サイズ", "values": [{"value": v} for v in raw_r_family]}]
        self.rak_bytes = ('<script>{"variantSelectors":' + json.dumps(selectors, ensure_ascii=False, separators=(",", ":")) +
                          ',"sku":[{"variantId":"v1","selectorValues":["L"]}]}</script>').encode("euc_jp")
        (self.root / self.rak_path).write_bytes(self.rak_bytes)
        self.rak_sha = sources.sha256_file(self.root / self.rak_path)
        sku = self.au_obj["itemInfo"]["skuInfo"]
        if au_kind == "row":
            sku["optionName"] = {"row": "サイズ", "column": "カラー"}
            sku["rowNames"], sku["columnNames"] = list(au_values), ["黒"]
        else:
            sku["optionName"] = {"row": "行", "column": "サイズ"}
            sku["rowNames"], sku["columnNames"] = ["行1"], list(au_values)
        (self.root / self.au_path).write_text(json.dumps(self.au_obj, ensure_ascii=False), encoding="utf-8")
        self.au_sha = sources.sha256_file(self.root / self.au_path)
        self.store = sources.RawStore(self.root)
        row_axis_name, col_axis_name = sku["optionName"]["row"], sku["optionName"]["column"]
        row_name_span = sources.json_leaf_span(self.store, self.au_path, "$.itemInfo.skuInfo.optionName.row")
        col_name_span = sources.json_leaf_span(self.store, self.au_path, "$.itemInfo.skuInfo.optionName.column")
        au_value_spans = [sources.json_leaf_span(self.store, self.au_path,
                           f"$.itemInfo.skuInfo.{au_kind}Names[{i}]") for i in range(len(au_values))]
        other_kind = "column" if au_kind == "row" else "row"
        other_span = sources.json_leaf_span(self.store, self.au_path,
                         f"$.itemInfo.skuInfo.{other_kind}Names[0]")
        a_name_span = row_name_span if au_kind == "row" else col_name_span
        c_name_span = col_name_span if au_kind == "row" else row_name_span
        selectors = sources.rakuten_variant_selectors(self.store, self.rak_path, "EUC-JP")
        label_span = selectors[0]["label_span"]
        r_value_span = sources.rakuten_selected_values(self.store, self.rak_path, "EUC-JP", "v1")[0]
        r_family_spans = [sources.rakuten_selector_value_span(self.store, self.rak_path, "EUC-JP", 0,
                            "サイズ", value) for value in r_family]
        self.source_case = {
            "case_id": "case-demo", "dossier_id": "dossier-demo", "au_product_id": "p1",
            "rakuten_selected": {"raw_file": self.rak_path, "sha256": self.rak_sha,
                "variant_id": "v1", "source_sku_key": "https://shop/item/#v1",
                "axes": [{"axis_index": 0, "axis_key": "サイズ", "axis_label": "サイズ",
                          "axis_label_span": label_span, "family_values": list(r_family),
                          "value": r_value, "value_span": r_value_span}]},
        }
        rows = []
        for i, value in enumerate(au_values):
            if au_kind == "row":
                row_axes = [{"axis_name": "サイズ", "value": value, "axis_name_span": a_name_span,
                             "value_span": au_value_spans[i]},
                            {"axis_name": "カラー", "value": "黒", "axis_name_span": c_name_span,
                             "value_span": other_span}]
                row_index, column_index = i, 0
            else:
                row_axes = [{"axis_name": "行", "value": "行1", "axis_name_span": c_name_span,
                             "value_span": other_span},
                            {"axis_name": "サイズ", "value": value, "axis_name_span": a_name_span,
                             "value_span": au_value_spans[i]}]
                row_index, column_index = 0, i
            rows.append({"row_key": f"au:p1:sku1:{row_index}:{column_index}", "row_index": row_index, "column_index": column_index,
                         "sku_id": "sku1", "axes": row_axes})
        selected_row = rows[au_values.index(au_value)]
        selected_row_key = selected_row["row_key"]
        selected_handoff_axes = [{"axis_name": x["axis_name"], "value": x["value"],
                                  "value_span": x["value_span"]} for x in selected_row["axes"]]
        self.source_product = {
            "dossier_id": "dossier-demo", "au_product": {"product_id": "p1", "raw_file": self.au_path,
                "sha256": self.au_sha, "axis_names": dict(sku["optionName"])},
            "au_rows": rows,
            "rakuten_product": {"raw_file": self.rak_path, "sha256": self.rak_sha,
                "selector_families": [{"key": "サイズ", "label": "サイズ", "values": list(r_family)}]},
        }
        producer = {"status": "model_candidate", "kind": "rakuten", "axis_key": "サイズ",
                "axis_label": "サイズ", "value": r_value, "au_axis": "サイズ", "au_value": au_value,
                "core": {"rakuten": [r_value], "au": [au_value], "identical": False}, "residual": []}
        self.task = {"case_id": "case-demo", "dossier_id": "dossier-demo", "au_product_id": "p1",
            "au_row_key": selected_row_key, "axis_key": "サイズ", "source_sku_key": "https://shop/item/#v1",
            "direction": "rakuten_to_au", "request_id": "req-demo", "option_values": list(r_family),
            "producer_condition": producer,
            "raw_condition": {"axis_key": "サイズ", "axis_label": "サイズ", "family_values": list(r_family),
                "value": r_value, "axis_label_span": label_span, "value_span": r_value_span},
            "source_handoff": {"case_id": "case-demo", "dossier_id": "dossier-demo",
                "au_row_key": selected_row_key, "au_axis_options": {"サイズ": list(au_values)},
                "rakuten_axes": {"サイズ": {"axis_label": "サイズ", "value": r_value,
                    "family_values": list(r_family)}},
                "conditions": [producer],
                "au_row_axes": selected_handoff_axes}}
        self.request = {"request_id": "req-demo", "direction": "rakuten_to_au", "axis_name": "サイズ",
            "selected_value": r_value, "selected_value_is_binding_only": True,
            "option_values": list(r_family), "target_selected_axes": [
                {"axis_name": x["axis_name"], "value": x["value"]} for x in selected_row["axes"]]}

        au_selected_span = au_value_spans[au_values.index(au_value)]
        normalized = [{"step": "nfkc_remove_whitespace", "rakuten": r_value, "au": au_value},
            {"step": "set_aside_bracketed_and_slash_parts", "rakuten_core": r_value,
             "au_core": au_value, "residual_kept": []}]
        side = "au" if au_value in {r_value + "サイズ", "サイズ" + r_value} else "rakuten"
        position = "suffix" if (au_value == r_value + "サイズ" or r_value == au_value + "サイズ") else "prefix"
        result = au_value[:-len("サイズ")] if side == "au" and position == "suffix" else (
            au_value[len("サイズ"):] if side == "au" else
            r_value[:-len("サイズ")] if position == "suffix" else r_value[len("サイズ"):])
        checks = ["same_axis_name", "one_core_is_the_other_plus_the_axis_name", "axis_name_on_every_au_option",
                  "axis_name_on_no_option_of_the_other_side", "removal_keeps_options_distinct_and_non_empty",
                  "unique_counterpart"]
        self.sidecar = {"case_id": "case-demo", "dossier_id": "dossier-demo", "au_row_key": selected_row_key,
            "axis_key": "サイズ", "verdict": "proven_equivalent", "proof_type": "own_axis_name_affix",
            "residual_unconfirmed": False,
            "align_v7": {"status": "model_candidate", "core": {"rakuten": [r_value], "au": [au_value], "identical": False}, "residual": []},
            "preserved": {"residual": [], "only_change": [{"step": "remove_axis_name_affix", "side": side,
                "position": position, "removed_text": "サイズ", "result": result}]},
            "normalization_before_types": normalized,
            "rakuten": {"axis_label": "サイズ", "axis_label_span": label_span, "value": r_value,
                "value_span": r_value_span, "family_values": list(r_family)},
            "au": {"axis_name": "サイズ", "axis_name_span": a_name_span, "value": au_value,
                "value_span": au_selected_span,
                "options": [{"value": v, "value_span": au_value_spans[i]} for i, v in enumerate(au_values)]},
            "attempts": [{"type": "own_axis_name_affix", "proven": True, "removed_text": "サイズ",
                "full_option_list_bijection": True,
                "normalization": [{"step": "remove_axis_name_affix", "side": side, "position": position,
                                   "removed_text": "サイズ", "result": result}],
                "checks": [{"name": name, "passed": True} for name in checks]}]}

    def test_complete_raw_family_affix_is_accepted(self):
        receipt = validate_sidecar(self.sidecar, self.task, self.request, self.source_case,
                                   self.source_product, self.store)
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["rakuten"]["family"], ["M", "L", "LL"])
        self.assertEqual(receipt["au"]["family"], ["Mサイズ", "Lサイズ", "LLサイズ"])
        self.assertEqual([s["quote"] for s in receipt["rakuten"]["family_spans"]], ["M", "L", "LL"])

    def test_windows_raw_file_alias_is_accepted_for_identity(self):
        class AliasStore:
            def __init__(self, base): self.base = base
            def raw(self, path, digest=None): return self.base.raw(path.replace("\\", "/"), digest)
            def sha(self, path): return self.base.sha(path.replace("\\", "/"))
            def text(self, path, encoding): return self.base.text(path.replace("\\", "/"), encoding)
            def json(self, path): return self.base.json(path.replace("\\", "/"))
        side = copy.deepcopy(self.sidecar)
        for value in (side["rakuten"]["axis_label_span"], side["rakuten"]["value_span"]):
            value["raw_file"] = value["raw_file"].replace("/", "\\")
        receipt = validate_sidecar(side, self.task, self.request, self.source_case,
                                   self.source_product, AliasStore(self.store))
        self.assertTrue(receipt["verified"])

    def test_raw_axis_label_mismatch_is_rejected(self):
        case = copy.deepcopy(self.source_case)
        case["rakuten_selected"]["axes"][0]["axis_label"] = "色"
        with self.assertRaises(ValueError):
            validate_sidecar(self.sidecar, self.task, self.request, case, self.source_product, self.store)

    def test_task_producer_residual_cannot_be_hidden(self):
        task = copy.deepcopy(self.task)
        task["producer_condition"]["residual"] = [{"text": "+"}]
        task["source_handoff"]["conditions"] = [copy.deepcopy(task["producer_condition"])]
        with self.assertRaises(ValueError):
            validate_sidecar(self.sidecar, task, self.request, self.source_case, self.source_product, self.store)

    def test_preserved_operation_must_match_recomputed_affix(self):
        side = copy.deepcopy(self.sidecar)
        side["preserved"]["only_change"][0]["result"] = "XL"
        with self.assertRaises(ValueError):
            validate_sidecar(side, self.task, self.request, self.source_case, self.source_product, self.store)

    def test_negative_au_matrix_indices_are_rejected(self):
        product = copy.deepcopy(self.source_product)
        product["au_rows"][1]["row_index"] = -1
        with self.assertRaises(ValueError):
            validate_sidecar(self.sidecar, self.task, self.request, self.source_case, product, self.store)

    def test_column_axis_uses_column_index_when_row_and_column_differ(self):
        self.build_inputs(au_kind="column")
        receipt = validate_sidecar(self.sidecar, self.task, self.request, self.source_case,
                                   self.source_product, self.store)
        self.assertTrue(receipt["verified"])
        self.assertEqual(self.task["au_row_key"], "au:p1:sku1:0:1")

    def test_reverse_direction_is_accepted_only_with_reversed_raw_bindings(self):
        task = copy.deepcopy(self.task)
        request = copy.deepcopy(self.request)
        task["direction"] = request["direction"] = "au_to_rakuten"
        task["option_values"] = ["Mサイズ", "Lサイズ", "LLサイズ"]
        task["raw_condition"] = {"axis_name": "サイズ", "value": "Lサイズ",
            "axis_name_span": sources.json_leaf_span(self.store, self.au_path,
                                                       "$.itemInfo.skuInfo.optionName.row"),
            "value_span": sources.json_leaf_span(self.store, self.au_path,
                                                  "$.itemInfo.skuInfo.rowNames[1]")}
        request["selected_value"] = "Lサイズ"
        request["option_values"] = ["Mサイズ", "Lサイズ", "LLサイズ"]
        request["target_selected_axes"] = [{"axis_name": "サイズ", "value": "L"}]
        receipt = validate_sidecar(self.sidecar, task, request, self.source_case, self.source_product, self.store)
        self.assertEqual(receipt["direction"], "au_to_rakuten")
        request["selected_value"] = "Mサイズ"
        with self.assertRaises(ValueError):
            validate_sidecar(self.sidecar, task, request, self.source_case, self.source_product, self.store)

    def test_bad_raw_sha_is_rejected(self):
        (self.root / self.rak_path).write_bytes(self.rak_bytes + b" ")
        with self.assertRaises(ValueError):
            validate_sidecar(self.sidecar, self.task, self.request, self.source_case,
                             self.source_product, sources.RawStore(self.root))

    def test_missing_raw_family_member_is_rejected(self):
        self.build_inputs(r_family=["M", "L", "LL"], raw_r_family=["M", "L"])
        with self.assertRaises(ValueError):
            validate_sidecar(self.sidecar, self.task, self.request, self.source_case,
                             self.source_product, self.store)

    def test_incomplete_sidecar_family_and_residual_are_rejected(self):
        bad = copy.deepcopy(self.sidecar)
        bad["rakuten"]["family_values"] = ["L"]
        with self.assertRaises(ValueError):
            validate_sidecar(bad, self.task, self.request, self.source_case, self.source_product, self.store)
        bad = copy.deepcopy(self.sidecar)
        bad["align_v7"]["residual"] = [{"side": "au", "text": "+"}]
        with self.assertRaises(ValueError):
            validate_sidecar(bad, self.task, self.request, self.source_case, self.source_product, self.store)

    def test_quantity_symbol_sign_and_modifier_differences_are_rejected(self):
        for value in ("Lサイズ+", "2Lサイズ", "-Lサイズ", "最大Lサイズ"):
            with self.subTest(value=value):
                self.build_inputs(au_values=["Mサイズ", value, "LLサイズ"], au_value=value)
                with self.assertRaises(ValueError):
                    validate_sidecar(self.sidecar, self.task, self.request, self.source_case,
                                     self.source_product, self.store)

    def test_forged_sidecar_core_or_binding_is_rejected(self):
        bad = copy.deepcopy(self.sidecar)
        bad["align_v7"]["core"]["rakuten"] = ["Lサイズ"]
        with self.assertRaises(ValueError):
            validate_sidecar(bad, self.task, self.request, self.source_case, self.source_product, self.store)
        bad = copy.deepcopy(self.sidecar)
        bad["au_row_key"] = "au:p1:sku1:0:0"
        with self.assertRaises(ValueError):
            validate_sidecar(bad, self.task, self.request, self.source_case, self.source_product, self.store)

    def test_tampered_span_is_rejected(self):
        bad = copy.deepcopy(self.sidecar)
        bad["rakuten"]["value_span"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            validate_sidecar(bad, self.task, self.request, self.source_case, self.source_product, self.store)

    def test_nfkced_symbol_cannot_bridge_raw_value_difference(self):
        self.build_inputs(au_values=["Mサイズ", "Lサイズ＋", "LLサイズ"], au_value="Lサイズ＋")
        with self.assertRaises(ValueError):
            validate_sidecar(self.sidecar, self.task, self.request, self.source_case,
                             self.source_product, self.store)


if __name__ == "__main__":
    unittest.main()
