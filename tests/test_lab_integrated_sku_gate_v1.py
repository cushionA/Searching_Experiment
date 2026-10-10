import importlib
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, "experiments/sku-matching")
import sku_integrated_gate_v1 as adapter


class IntegratedSkuGateV1Tests(unittest.TestCase):
    def test_vendor_import_is_isolated_from_existing_gate_modules(self):
        marker = object()
        previous = sys.modules.get("sku_gates")
        sys.modules["sku_gates"] = marker
        try:
            gates = adapter.load_gate()
            self.assertIs(sys.modules["sku_gates"], marker)
            self.assertTrue(gates.__name__.endswith("claude_v9_gate.sku_gates"))
            self.assertTrue(gates.atoms_mod.__name__.endswith("claude_v9_gate.sku_gate_atoms"))
            self.assertIs(gates.atoms_mod, importlib.import_module("claude_v9_gate.sku_gate_atoms"))
        finally:
            if previous is None:
                sys.modules.pop("sku_gates", None)
            else:
                sys.modules["sku_gates"] = previous

    def test_labeled_slash_dimensions_keep_full_quote_and_compare_alternatives(self):
        gates = adapter.load_gate()
        atoms = gates.atoms_mod
        raw = "幅68×奥行28.5×高さ10/15cm"
        parsed = atoms.atomize(raw)
        dim = next(a for a in parsed["atoms"] if a["type"] == "dimension")
        self.assertEqual(dim["quote"], raw)
        self.assertEqual(raw[slice(*dim["offset"])], raw)
        self.assertEqual(dim["labels"], ["width", "depth", "height"])
        self.assertEqual(dim["value"], [68, 28.5, 10])
        self.assertEqual(dim["alternatives"]["height"], [10, 15])
        req15 = {**dim, "value": [68, 28.5, 15]}
        req20 = {**dim, "value": [68, 28.5, 20], "alternatives": {}}
        self.assertEqual(gates.compare_atoms(req15, dim, lambda *_: False), "support")
        self.assertEqual(gates.compare_atoms(req20, dim, lambda *_: False), "conflict")
        # Page-spec differences are notices under the selected source config.
        self.assertFalse(gates.SOURCE_CONFIGS["full_spec_notice"].get("derived_decides", True))

    def test_selected_attribute_fabric_pile_is_supported(self):
        gates = adapter.load_gate()
        req = {"type": "fabric", "value": "パイル"}
        attribute = gates.attribute_facts([{"title": "生地", "value": "メッシュ・パイル"}])
        attr = next(f["atom"] for f in attribute if f["atom"]["value"] == "パイル")
        self.assertTrue(gates.comparable(req, attr))
        self.assertEqual(gates.compare_atoms(req, attr, lambda *_: False), "support")

    def test_make_pair_fabric_and_curtain_examples_accept_only_unique_full_row(self):
        gates = adapter.load_gate()
        def make_pair(rows, selected, families, attributes=(), rak_lines=()):
            selectors = [{"key": k, "label": k, "values": list(v)} for k, v in families.items()]
            vocab, fams, tokens = gates.derive_vocabulary([a for row in rows for a in row], selectors)
            def span(text, path):
                return {"raw_file": "fixture", "sha256": "0" * 64,
                        "locator": {"kind": "json_leaf", "json_path": path},
                        "start": 0, "end": len(text), "quote": text} if text else None
            lines = lambda values: [{"text": t, "span": span(t, "$.line"), "scope_tag": "product_page"}
                                    for t in values]
            context = {"dossier_id": "fixture", "pair_ref": "fixture",
                "au_product": {"product_id": "1", "raw_file": "au.json", "sha256": "0" * 64,
                               "title": "", "title_span": None, "axis_names": {}},
                "au_rows": [{"row_key": f"au:1:9:{i}:0", "row_index": i, "column_index": 0,
                             "sku_id": 9, "axes": [{"axis_name": k, "value": v, "value_span": span(v, "$.row"),
                                                     "axis_name_span": None} for k, v in row],
                             "array_source": {}} for i, row in enumerate(rows)],
                "rakuten_product": {"url": "fixture", "raw_file": "r.html", "sha256": "0" * 64,
                    "encoding": "utf-8", "title": "", "title_span": None, "selector_families": fams},
                "color_vocab": sorted(vocab), "variant_tokens": list(tokens),
                "au_description_lines": lines([]), "rakuten_description_lines": lines(rak_lines),
                "au_purchase_option_lines": [], "size_code_crosswalks": [],
                "excluded_non_identity_fields": list(gates.NON_IDENTITY_FIELDS)}
            case = {"case_id": "fixture-case", "dossier_id": "fixture", "au_product_id": "1",
                "rakuten_selected": {"url": "fixture", "raw_file": "r.html", "sha256": "0" * 64,
                    "variant_id": "v", "source_row_key": "fixture", "source_sku_key": "fixture",
                    "sku_record_key": "k", "source_row_index": 0,
                    "variant_attributes": list(attributes),
                    "axes": [{"axis_index": i, "axis_key": k, "axis_label": k, "axis_label_span": None,
                              "value": v, "value_span": span(v, "$.sku"), "family_values": list(families[k])}
                             for i, (k, v) in enumerate(selected)]}}
            return case, gates.PairFacts(context)

        cases = [
            make_pair([[('カラー', '青（パイル）')], [('カラー', '赤（メッシュ）')]],
                      [('カラー', '青')], {'カラー': ('青', '赤')},
                      [{'title': '生地', 'value': 'パイル'}]),
            make_pair([[('カラー', '青（パイル）')], [('カラー', '赤（メッシュ）')]],
                      [('カラー', '青')], {'カラー': ('青', '赤')}),
            make_pair([[('カラー', '青'), ('レースカーテン', 'なし')],
                       [('カラー', '青'), ('レースカーテン', 'あり')]],
                      [('カラー', '青'), ('レースカーテン', 'なし')],
                      {'カラー': ('青',), 'レースカーテン': ('なし', 'あり')}),
            make_pair([[('カラー', '青'), ('サイズ', '幅200cm')],
                       [('カラー', '赤'), ('サイズ', '幅100cm')]],
                      [('カラー', '青'), ('サイズ', '幅100cm')],
                      {'カラー': ('青', '赤'), 'サイズ': ('幅100cm', '幅200cm')}),
            make_pair([[('カラー', '青'), ('枚数', '2枚')],
                       [('カラー', '赤'), ('枚数', '1枚')]],
                      [('カラー', '青')], {'カラー': ('青', '赤')},
                      rak_lines=['商品詳細', '内容', '・遮光カーテン 1枚', '・レースカーテン 1枚']),
        ]
        for index, (case, facts) in enumerate(cases):
            with self.subTest(case=index):
                prediction = adapter.predict_case(case, facts)
                if index in (1, 3):
                    self.assertEqual(prediction['decision'], 'drop', prediction)
                    continue
                self.assertEqual(prediction['decision'], 'accept', prediction)
                self.assertEqual(prediction['gate_decision'], 'matched')
                if index == 0:
                    with patch.object(gates.src, "verify_span", return_value=False):
                        rejected = adapter.predict_case(case, facts, store=object())
                    self.assertEqual(rejected["decision"], "drop")
                if index == 4:
                    supported = next(row for row in prediction['rows'] if row['status'] == 'full')
                    sum_evidence = next(e for atom in supported['au_only_atoms']
                                        for e in atom.get('evidence', [])
                                        if e.get('scope') == 'closed_contents_sum')
                    self.assertIsNone(sum_evidence.get('span'))
                    self.assertEqual(len(sum_evidence.get('spans', [])), 2)

    def test_unproven_competitor_drops_and_all_conditions_must_share_adopted_row(self):
        class FakeGates:
            NON_IDENTITY_FIELDS = ("price", "stock")

            @staticmethod
            def run_method(method, case, facts, config, evaluator=None):
                full = "row-a" if method == "A" else "row-a"
                row_status = "full"
                if method == "A" and getattr(facts, "unproven_competitor", False):
                    competitor = "partial"
                else:
                    competitor = "conflict"
                rows = [{"row_key": full, "status": row_status},
                        {"row_key": "row-b", "status": competitor}]
                return {"decision": "matched", "top_row_key": full,
                        "binary": {"rows": rows}, "rows": rows, "requirements": []}

        original_load = adapter.load_gate
        adapter.load_gate = lambda: FakeGates
        try:
            class Facts:
                rows = [{"row_key": "row-a"}, {"row_key": "row-b"}]
                unproven_competitor = True
            facts = Facts()
            self.assertEqual(adapter.predict_case({}, facts)["decision"], "drop")
            facts.unproven_competitor = False
            self.assertEqual(adapter.predict_case({}, facts)["decision"], "accept")
        finally:
            adapter.load_gate = original_load

    def test_distinct_method_rows_drop_even_if_each_method_finds_a_full_row(self):
        class FakeGates:
            NON_IDENTITY_FIELDS = ()

            @staticmethod
            def run_method(method, case, facts, config, evaluator=None):
                rows = [{"row_key": "row-a", "status": "full"},
                        {"row_key": "row-b", "status": "full"}]
                return {"decision": "matched", "top_row_key": "row-a",
                        "binary": {"rows": rows}, "rows": rows, "requirements": []}

        original_load = adapter.load_gate
        adapter.load_gate = lambda: FakeGates
        try:
            class Facts:
                rows = [{"row_key": "row-a"}, {"row_key": "row-b"}]
            facts = Facts()
            self.assertEqual(adapter.predict_case({}, facts)["decision"], "drop")
        finally:
            adapter.load_gate = original_load

    def test_conditions_split_across_rows_never_form_a_full_row(self):
        class FakeGates:
            NON_IDENTITY_FIELDS = ()

            @staticmethod
            def run_method(method, case, facts, config, evaluator=None):
                rows = [{"row_key": "row-a", "status": "partial", "supported": 1, "requirements": 2},
                        {"row_key": "row-b", "status": "partial", "supported": 1, "requirements": 2}]
                return {"decision": "review", "top_row_key": None, "binary": {"rows": rows},
                        "rows": rows, "requirements": [{"requirement_id": "color"},
                                                          {"requirement_id": "fabric"}]}

        original_load = adapter.load_gate
        adapter.load_gate = lambda: FakeGates
        try:
            class Facts:
                rows = [{"row_key": "row-a"}, {"row_key": "row-b"}]
            result = adapter.predict_case({}, Facts())
            self.assertEqual(result["decision"], "drop")
            self.assertIsNone(result["au_row_key"])
        finally:
            adapter.load_gate = original_load

    def test_invalid_competitor_quote_cannot_exclude_an_au_row(self):
        from types import SimpleNamespace

        span = lambda quote: {"raw_file": "fixture", "sha256": "0" * 64,
                              "locator": {"kind": "json_leaf", "json_path": "$.value"},
                              "start": 0, "end": len(quote), "quote": quote}

        class FakeGates:
            NON_IDENTITY_FIELDS = ()
            src = SimpleNamespace(verify_span=staticmethod(lambda store, source_span: True))

            @staticmethod
            def run_method(method, case, facts, config, evaluator=None):
                rows = [
                    {"row_key": "row-a", "status": "full",
                     "atom_results": [{"status": "support", "evidence": [
                         {"relation": "support", "quote": "blue", "span": span("blue")}]}],
                     "au_only_atoms": []},
                    {"row_key": "row-b", "status": "conflict",
                     "atom_results": [{"status": "conflict", "evidence": [
                         {"relation": "conflict", "quote": "incorrect", "span": span("red")}]}],
                     "au_only_atoms": []},
                ]
                return {"decision": "matched", "top_row_key": "row-a", "rows": rows,
                        "requirements": [{"quote": "blue", "span": span("blue")}], "binary": {}}

        original_load = adapter.load_gate
        adapter.load_gate = lambda: FakeGates
        try:
            class Facts:
                rows = [{"row_key": "row-a"}, {"row_key": "row-b"}]
            result = adapter.predict_case({}, Facts(), store=object())
            self.assertEqual(result["decision"], "drop")
            self.assertEqual(result["reason"], "unquoted_competing_evidence")
        finally:
            adapter.load_gate = original_load


if __name__ == "__main__":
    unittest.main()
