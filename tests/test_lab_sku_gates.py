"""Source-preservation and decision-safety tests for the fixed-pair SKU gates.

Synthetic fixtures check the rules; the real-data tests (skipped when the
checkpoint is not extracted) check that every requirement quote resolves to
its original file and that the representative handoff cases keep their
required decisions. Labels are never read.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import jsonschema  # noqa: E402
import sku_gate_sources as src  # noqa: E402
import sku_gates as gates  # noqa: E402
from sku_gate_atoms import atomize  # noqa: E402

ANNOTATION = ROOT / ".lab-output/sku-real-luna-annotation-inputs-20261010-v3"
SCHEMAS = ROOT / "experiments/sku-matching/schemas"


def validator(name):
    return jsonschema.Draft202012Validator(json.loads((SCHEMAS / name).read_text(encoding="utf-8")))


def make_pair(rows, selected, families, au_title="", au_lines=(), rak_lines=(), rak_title=""):
    """Synthetic fixed pair. rows: list of [(axis, value)], selected: [(axis, value)]."""
    selectors = [{"key": k, "label": k, "values": list(v)} for k, v in families.items()]
    vocab, fams, tokens = gates.derive_vocabulary([a for r in rows for a in r], selectors)

    def fake_span(text, path):
        # Gate logic only; literal resolution is covered by SpanTests and RealSourceTests.
        return {"raw_file": "fixture", "sha256": "0" * 64, "locator": {"kind": "json_leaf", "json_path": path},
                "start": 0, "end": len(text), "quote": text} if text else None

    line = lambda t: {"text": t, "span": fake_span(t, "$.line"), "scope_tag": "product_page"}  # noqa: E731
    context = {
        "dossier_id": "pair-test", "pair_ref": "au:1|raku:test",
        "au_product": {"product_id": "1", "raw_file": "au.json", "sha256": "0" * 64, "title": au_title,
                       "title_span": fake_span(au_title, "$.itemTitle"), "axis_names": {}},
        "au_rows": [{"row_key": f"au:1:9:{i}:0", "row_index": i, "column_index": 0, "sku_id": 9,
                     "axes": [{"axis_name": n, "value": v, "axis_name_span": None, "value_span": fake_span(v, "$.row")}
                              for n, v in r],
                     "array_source": {}} for i, r in enumerate(rows)],
        "rakuten_product": {"url": "u", "raw_file": "r.html", "sha256": "0" * 64, "encoding": "EUC-JP",
                            "title": rak_title, "title_span": fake_span(rak_title, "$.title"), "selector_families": fams},
        "color_vocab": sorted(vocab), "variant_tokens": list(tokens),
        "au_description_lines": [line(t) for t in au_lines], "rakuten_description_lines": [line(t) for t in rak_lines],
        "au_purchase_option_lines": [], "size_code_crosswalks": [],
        "excluded_non_identity_fields": list(gates.NON_IDENTITY_FIELDS)}
    case = {"case_id": "case-" + "0" * 20, "dossier_id": "pair-test", "au_product_id": "1",
            "rakuten_selected": {"url": "u", "raw_file": "r.html", "sha256": "0" * 64, "variant_id": "v",
                                 "source_row_key": "u#row", "source_sku_key": "u#v:0", "sku_record_key": "k",
                                 "source_row_index": 0,
                                 "axes": [{"axis_index": i, "axis_key": k, "axis_label": k, "axis_label_span": None,
                                           "value": v, "value_span": fake_span(v, "$.sku"), "family_values": list(families[k])}
                                          for i, (k, v) in enumerate(selected)]}}
    return gates.PairFacts(context), case


def run(method, facts, case, config="full"):
    return gates.run_method(method, case, facts, config)


class AtomTests(unittest.TestCase):
    def test_quotes_are_literal_substrings_with_offsets(self):
        for label, value in [("枚数", "21枚セット(パネル20枚＋ドア1枚)"), ("サイズ", "幅100×丈220cm(2枚)"),
                             ("タイプ", "スリム / グレー"), ("カラー", "グレー（レザー調）"), ("サイズ", "シングル(140×200cm)")]:
            parsed = atomize(value, label, frozenset({"グレー"}))
            self.assertEqual(parsed["decomposition"], "complete", value)
            for atom in parsed["atoms"]:
                start, end = atom["offset"]
                self.assertEqual(value[start:end], atom["quote"])

    def test_compound_values_keep_every_element(self):
        atoms = atomize("21枚セット(パネル20枚＋ドア1枚)", "枚数")["atoms"]
        got = {(a["type"], a.get("component"), a["value"]) for a in atoms}
        self.assertEqual(got, {("piece_total", None, 21), ("component_count", "panel", 20), ("component_count", "door", 1)})
        slim = atomize("スリム / グレー", "タイプ", frozenset({"グレー"}))["atoms"]
        self.assertEqual({(a["type"], a["value"]) for a in slim}, {("variant", "スリム"), ("color", "グレー")})
        blanket = atomize("毛布セット", "オプション")["atoms"]
        self.assertEqual([(a["type"], a["component"], a["value"]) for a in blanket], [("component_presence", "blanket", True)])
        none = atomize("なし", "オプション", family=("なし", "毛布セット"))["atoms"]
        self.assertEqual([(a["component"], a["value"]) for a in none], [("blanket", False)])

    def test_named_size_is_a_word(self):
        from sku_gate_atoms import title_facts
        self.assertNotIn("named_size", title_facts("＼ランキング1位／ マットレス", frozenset(), ()))
        self.assertIn("named_size", title_facts("マットレス キング 三つ折り", frozenset(), ()))


class GateTests(unittest.TestCase):
    families = {"サイズ": ("S", "M"), "カラー": ("赤", "青")}

    def test_partial_compound_support_never_accepts(self):
        facts, case = make_pair([[("カラー", "半透明")]], [("枚数", "21枚セット(パネル20枚＋ドア1枚)"), ("タイプ", "半透明")],
                                {"枚数": ("21枚セット(パネル20枚＋ドア1枚)",), "タイプ": ("透明", "半透明")},
                                au_lines=["商 品 詳 細", "内容", "ドアパーツ×1枚"])
        for method in ("A", "B"):
            out = run(method, facts, case)
            self.assertNotEqual(out["decision"], "matched")
            row = out["rows"][0]
            statuses = {r["requirement_id"]: r["status"] for r in row["atom_results"]}
            self.assertEqual(statuses["r0.2"], "support")  # door 1 is supported ...
            self.assertEqual(statuses["r0.0"], "unknown")  # ... but total 21 is not.

    def test_axes_are_never_combined_across_au_rows(self):
        facts, case = make_pair([[("カラー", "赤"), ("サイズ", "S")], [("カラー", "青"), ("サイズ", "M")]],
                                [("カラー", "赤"), ("サイズ", "M")], self.families)
        for method in ("A", "B"):
            out = run(method, facts, case)
            self.assertEqual(out["decision"], "unmatched")
            self.assertEqual(out["candidate_row_keys"], [])

    def test_missing_axis_is_unknown_but_explicit_contrary_value_is_conflict(self):
        fam = {"サイズ": ("100×220cm",), "レースカーテン": ("あり", "なし")}
        rows = [[("サイズ", "幅100×丈220cm(2枚)")]]
        facts, case = make_pair(rows, [("サイズ", "100×220cm"), ("レースカーテン", "なし")], fam)
        for method in ("A", "B"):
            out = run(method, facts, case)
            self.assertEqual(out["decision"], "review", method)
            self.assertEqual(out["rows"][0]["atom_results"][1]["status"], "unknown")
        facts, case = make_pair(rows, [("サイズ", "100×220cm"), ("レースカーテン", "なし")], fam,
                                au_title="カーテン 4枚セット レースカーテンセット")
        for method in ("A", "B"):
            out = run(method, facts, case)
            self.assertEqual(out["decision"], "unmatched", method)
            self.assertEqual(out["rows"][0]["atom_results"][1]["status"], "conflict")

    def test_series_title_values_are_not_applied_to_the_selected_sku(self):
        fam = {"段数": ("2段", "3段"), "カラー": ("赤",)}
        facts, case = make_pair([[("カラー", "赤")]], [("段数", "2段"), ("カラー", "赤")], fam, au_title="脚立 2段 3段")
        out = run("A", facts, case)
        self.assertEqual(out["decision"], "review")
        facts, case = make_pair([[("カラー", "赤")]], [("段数", "2段"), ("カラー", "赤")], fam, au_title="脚立 3段")
        self.assertEqual(run("A", facts, case)["decision"], "unmatched")

    def test_closed_contents_list_is_structural_in_a_but_not_a_quote_in_b(self):
        fam = {"サイズ": ("100×220cm",), "レースカーテン": ("あり", "なし")}
        lines = ["商 品 詳 細", "内容", "【幅100cm】", "遮光カーテン 2枚", "タッセル 2枚"]
        facts, case = make_pair([[("サイズ", "幅100×丈220cm(2枚)")]], [("サイズ", "100×220cm"), ("レースカーテン", "なし")],
                                fam, au_lines=lines)
        self.assertEqual(run("A", facts, case)["decision"], "matched")
        self.assertEqual(run("B", facts, case)["decision"], "review")
        self.assertEqual(run("A", facts, case, "full_no_closed_list")["decision"], "review")

    def test_conditions_are_evaluated_on_the_same_candidate_row(self):
        fam = {"サイズ": ("100×220cm", "150×200cm"), "レースカーテン": ("あり", "なし")}
        lines = ["商 品 詳 細", "内容", "【幅100cm】", "遮光カーテン 2枚", "【幅150cm】", "遮光カーテン 1枚", "レースカーテン 1枚"]
        rows = [[("サイズ", "幅100×丈220cm(2枚)")], [("サイズ", "幅150×丈200cm(2枚組)")]]
        facts, case = make_pair(rows, [("サイズ", "150×200cm"), ("レースカーテン", "あり")], fam, au_lines=lines)
        out = run("A", facts, case)
        self.assertEqual((out["decision"], out["top_row_key"]), ("matched", "au:1:9:1:0"))
        facts, case = make_pair(rows, [("サイズ", "100×220cm"), ("レースカーテン", "あり")], fam, au_lines=lines)
        self.assertEqual(run("A", facts, case)["decision"], "unmatched")

    def test_b_requires_competing_rows_to_be_excluded(self):
        # The second row lacks the size axis, so it is unknown rather than excluded.
        fam = {"カラー": ("赤", "青"), "サイズ": ("S", "M")}
        facts, case = make_pair([[("カラー", "赤"), ("サイズ", "S")], [("カラー", "赤")]],
                                [("カラー", "赤"), ("サイズ", "S")], fam)
        self.assertEqual(run("A", facts, case)["decision"], "matched")
        b = run("B", facts, case)
        self.assertEqual((b["decision"], b["reason"]), ("review", "competing_row_not_excluded"))

    def test_derived_page_conflict_blocks_accept_without_unmatched(self):
        fam = {"高さ": ("50cm",), "カラー": ("シルバー",)}
        facts, case = make_pair([[("カラー", "シルバー")]], [("高さ", "50cm"), ("カラー", "シルバー")], fam,
                                au_lines=["こちらのページは幅78×高さ50cmです", "商 品 詳 細", "材質", "持ち手：プラスチックメッキ"],
                                rak_lines=["商 品 詳 細", "注意事項", "・天板高さ50cmのシルバーには持ち手がありません。"])
        for method in ("A", "B"):
            out = run(method, facts, case)
            self.assertEqual(out["decision"], "review")
            self.assertEqual(out["reason"], "page_specification_conflict_on_candidate_row")
        self.assertEqual(run("A", facts, case, "full_no_derived_conflicts")["decision"], "matched")

    def test_same_scope_contents_count_disagreement_blocks_accept(self):
        fam = {"サイズ": ("150×200cm",), "レースカーテン": ("あり", "なし")}
        au = ["商 品 詳 細", "内容", "【幅150cm】", "遮光カーテン 1枚", "レースカーテン 1枚", "カーテンフック 7個"]
        rak = ["商 品 詳 細", "内容", "【幅150cm】", "カーテン 1枚", "フック 9個", "レースカーテン 1個 ※レースカーテン付きを選択の場合"]
        facts, case = make_pair([[("サイズ", "幅150×丈200cm(2枚組)")]], [("サイズ", "150×200cm"), ("レースカーテン", "あり")],
                                fam, au_lines=au, rak_lines=rak)
        for method in ("A", "B"):
            out = run(method, facts, case)
            self.assertEqual((out["decision"], out["reason"]), ("review", "page_specification_conflict_on_candidate_row"))
            conflict = out["rows"][0]["derived_conflicts"][0]
            self.assertEqual((conflict["family"], conflict["au"]["value"], conflict["rakuten"]["value"]),
                             ("component_count:hook", 7, 9))

    def test_body_dimension_disagreement_needs_one_value_per_side(self):
        fam = {"カラー": ("ラテ",)}
        au = ["商 品 詳 細", "サイズ", "（約）幅66x奥行57x高さ70cm"]
        facts, case = make_pair([[("カラー", "ラテ")]], [("カラー", "ラテ")], fam, au_lines=au,
                                rak_lines=["商 品 詳 細", "サイズ", "（約）幅50x奥行57x高さ70cm"])
        self.assertEqual(run("A", facts, case)["decision"], "review")
        # A side stating two different values is internally inconsistent: no conflict is claimed.
        fam = {"サイズ": ("ダブル",), "カラー": ("赤",)}
        facts, case = make_pair([[("カラー", "赤")]], [("サイズ", "ダブル"), ("カラー", "赤")], fam,
                                au_lines=["こちらのページはダブルサイズです", "商 品 詳 細", "サイズ", "（約）幅140cm×長さ205cm（ダブルサイズ）"],
                                rak_lines=["商 品 詳 細", "サイズ", "【シングル】（約）幅100×長さ205cm", "【ダブル】（約）幅140×長さ205cm"])
        case["rakuten_selected"]["variant_attributes"] = [{"title": "本体縦幅", "value": "200", "unit": "cm", "value_span": None}]
        out = run("A", facts, case)
        self.assertEqual(out["decision"], "matched")
        case["rakuten_selected"]["variant_attributes"][0]["value"] = "205"
        self.assertEqual(run("A", facts, case)["decision"], "matched")
        facts, case = make_pair([[("カラー", "赤")]], [("サイズ", "ダブル"), ("カラー", "赤")], fam,
                                au_lines=["こちらのページはダブルサイズです", "商 品 詳 細", "サイズ", "（約）幅140cm×長さ205cm"])
        case["rakuten_selected"]["variant_attributes"] = [{"title": "本体縦幅", "value": "200", "unit": "cm", "value_span": None}]
        self.assertEqual(run("A", facts, case)["decision"], "review")

    def test_price_and_stock_fields_do_not_change_decisions(self):
        facts, case = make_pair([[("カラー", "赤")], [("カラー", "青")]], [("カラー", "赤")], {"カラー": ("赤", "青")})
        before = run("A", facts, case)
        case["rakuten_selected"]["price_jpy"] = 1
        facts.context["au_rows"][0]["stock"] = {"isSoldOut": True}
        after = run("A", facts, case)
        self.assertEqual(before["decision"], after["decision"])
        self.assertEqual(before["top_row_key"], after["top_row_key"])

    def test_unquoted_requirement_never_decides(self):
        fam = {"カラー": ("赤", "青", "緑")}
        for selected, quoted in (("赤", "matched"), ("緑", "unmatched")):
            facts, case = make_pair([[("カラー", "赤")], [("カラー", "青")]], [("カラー", selected)], fam)
            for method in ("A", "B"):
                self.assertEqual(run(method, facts, case)["decision"], quoted, method)
            case["rakuten_selected"]["axes"][0]["value_span"] = None
            for method in ("A", "B"):
                out = run(method, facts, case)
                validator(f"sku_gate_{method.lower()}_output.schema.json").validate(out)
                self.assertEqual((out["decision"], out["reason"]), ("review", "unquoted_requirement"), method)

    def test_outputs_keep_row_keys_and_provenance(self):
        facts, case = make_pair([[("カラー", "赤")], [("カラー", "青")]], [("カラー", "赤")], {"カラー": ("赤", "青")})
        out = run("A", facts, case)
        self.assertEqual([r["row_key"] for r in out["rows"]], ["au:1:9:0:0", "au:1:9:1:0"])
        self.assertEqual(out["top_row_key"], "au:1:9:0:0")
        self.assertEqual(out["rakuten_provenance"]["source_row_key"], "u#row")
        self.assertEqual(out["rakuten_provenance"]["sku_record_key"], "k")
        self.assertEqual(out["au_provenance"]["sha256"], "0" * 64)


class SpanTests(unittest.TestCase):
    def test_spans_resolve_and_detect_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = '<title>テスト商品</title>{"variantId":"V1","selectorValues":["100×220cm","なし"]}'
            (root / "p.html").write_bytes(page.encode("euc-jp"))
            (root / "a.json").write_text(json.dumps({"itemInfo": {"itemTitle": "カーテン 2枚セット",
                                                                   "skuInfo": {"rowNames": ["スモークグレー"]}}},
                                                    ensure_ascii=False), encoding="utf-8")
            store = src.RawStore(root)
            spans = src.rakuten_selected_values(store, "p.html", "EUC-JP", "V1")
            self.assertEqual([s["quote"] for s in spans], ["100×220cm", "なし"])
            leaf = src.json_leaf_span(store, "a.json", "$.itemInfo.skuInfo.rowNames[0]")
            title = src.json_leaf_span(store, "a.json", "$.itemInfo.itemTitle", "2枚セット")
            for span in spans + [leaf, title]:
                self.assertTrue(src.verify_span(store, span))
                self.assertEqual(span["sha256"], src.sha256_file(root / span["raw_file"]))
            narrow = src.sub_span(title, 0, 2)
            self.assertEqual(narrow["quote"], "2枚")
            self.assertTrue(src.verify_span(store, narrow))
            self.assertFalse(src.verify_span(store, {**leaf, "quote": "グレー"}))
            self.assertFalse(src.verify_span(store, {**leaf, "start": 1}))
            self.assertFalse(src.verify_span(store, {**leaf, "sha256": "0" * 64}))
            fresh = src.RawStore(root)
            (root / "a.json").write_text("{}", encoding="utf-8")
            self.assertFalse(src.verify_span(fresh, leaf))


@unittest.skipUnless((ANNOTATION / "cases.jsonl").exists(), "checkpoint not extracted")
class RealSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = src.RawStore(ROOT)
        cases = src.read_jsonl(ANNOTATION / "cases.jsonl")
        arrays = ".lab-output/sku-observed-product-pairs-20261010-final-v4/au_product_sku_arrays.jsonl"
        index = {}
        for i, line in enumerate((ROOT / arrays).read_text(encoding="utf-8").splitlines()):
            index.setdefault(json.loads(line)["au_product_id"], i + 1)
        cls.contexts, cls.facts, cls.inputs = {}, {}, {}
        for case in cases:
            if case["dossier_id"] not in cls.contexts:
                dossier = json.loads((ANNOTATION / "dossiers" / f"{case['dossier_id']}.json").read_text(encoding="utf-8"))
                cls.contexts[case["dossier_id"]] = gates.build_product_context(cls.store, dossier, case, arrays, index)
                cls.facts[case["dossier_id"]] = gates.PairFacts(cls.contexts[case["dossier_id"]])
            cls.inputs[case["case_id"]] = (case, gates.build_case_input(cls.store, case, cls.contexts[case["dossier_id"]]))

    def test_every_selected_value_and_au_row_resolves_to_its_original_file(self):
        self.assertEqual(len(self.inputs), 1383)
        case_schema = validator("sku_gate_case_input.schema.json")
        for case, ci in self.inputs.values():
            case_schema.validate(ci)
            sel = ci["rakuten_selected"]
            self.assertEqual(sel["source_row_key"], case["rakuten"]["source"]["source_row_key"])
            self.assertEqual(sel["sku_record_key"], case["rakuten"]["source"]["sku_record_key"])
            self.assertEqual(sel["sha256"], self.store.sha(sel["raw_file"]))
            for axis in ci["rakuten_selected"]["axes"]:
                self.assertTrue(src.verify_span(self.store, axis["value_span"]))
                self.assertEqual(axis["value_span"]["quote"], axis["value"])
            for attr in ci["rakuten_selected"]["variant_attributes"]:
                self.assertNotRegex(attr["title"], "価格|送料|在庫|ポイント")
                if attr["value_span"]:
                    self.assertTrue(src.verify_span(self.store, attr["value_span"]))
                    self.assertEqual(attr["value_span"]["quote"], attr["value"])
        ctx_schema = validator("sku_gate_product_context.schema.json")
        for ctx in self.contexts.values():
            ctx_schema.validate(ctx)
            self.assertTrue(src.verify_span(self.store, ctx["au_product"]["title_span"]))
            for row in ctx["au_rows"]:
                for axis in row["axes"]:
                    self.assertTrue(src.verify_span(self.store, axis["value_span"]))
            for line in ctx["au_description_lines"] + ctx["rakuten_description_lines"]:
                if line["span"]:
                    self.assertTrue(src.verify_span(self.store, line["span"]))
                    self.assertEqual(line["span"]["quote"], line["text"])

    def _run(self, case_id, method):
        case, ci = self.inputs[case_id]
        out = gates.run_method(method, ci, self.facts[case["dossier_id"]], "full")
        validator(f"sku_gate_{method.lower()}_output.schema.json").validate(out)
        for card in out["requirements"]:
            self.assertTrue(src.verify_span(self.store, card["span"]))
        for row in out["rows"]:
            for res in row.get("atom_results", []):
                for ev in res["evidence"]:
                    for span in [ev.get("span")] + list(ev.get("spans") or []):
                        if span:
                            self.assertTrue(src.verify_span(self.store, span), ev)
        return out

    def test_representative_cases(self):
        a = self._run("case-001a5707d96903cfe7da", "A")
        self.assertEqual((a["decision"], a["top_row_key"]), ("matched", "au:704500131:331814079:3:12"))
        self.assertEqual(self._run("case-001a5707d96903cfe7da", "B")["decision"], "review")
        for method in ("A", "B"):
            out = self._run("case-003b9c60664f6b5059a8", method)
            self.assertNotEqual(out["decision"], "matched")
            self.assertEqual(out["au_product_id"], "704500131")
            self.assertNotIn("704502086", json.dumps(out["rows"], ensure_ascii=False))
            pet = self._run("case-03eb5704d3bb159f757b", method)
            types = sorted((r["type"], r.get("component"), r["value"]) for r in pet["requirements"] if r["axis_index"] == 0)
            self.assertEqual(types, [("component_count", "door", 1), ("component_count", "panel", 20), ("piece_total", None, 21)])
            self.assertNotEqual(pet["decision"], "matched")
            slim = self._run("case-0722ef51b816b9837580", method)
            self.assertEqual(slim["decision"], "review")
            status = {r["requirement_id"]: r["status"] for r in slim["rows"][0]["atom_results"]}
            self.assertEqual(status, {"r0.0": "unknown", "r0.1": "support"})
            blanket = self._run("case-03df33141d94ac3c09fc", method)
            self.assertNotEqual(blanket["decision"], "matched")
            # Hook counts differ in the same width-150 contents scope of the fixed lace-set pair.
            hooks = self._run("case-180a9baafb740879a120", method)
            self.assertEqual(hooks["decision"], "review")
            self.assertIn("component_count:hook", json.dumps(hooks["rows"], ensure_ascii=False))

    def test_sibling_swap_never_reaccepts_the_same_row(self):
        # Label-free metamorphic check: replace one selected value with a sibling option quoted
        # from the page's own option list; the row accepted for the original SKU must not be
        # accepted again. Two accepted cases per fixed pair keep the test fast.
        checked, per_pair = 0, {}
        for case_id in sorted(self.inputs):
            case, ci = self.inputs[case_id]
            key = case["dossier_id"]
            if per_pair.get(key, 0) >= 2:
                continue
            original = gates.run_method("A", ci, self.facts[key], "full")
            if original["decision"] != "matched":
                continue
            per_pair[key] = per_pair.get(key, 0) + 1
            for swap, swapped in gates.sibling_swaps(self.store, ci):
                for method in ("A", "B"):
                    out = gates.run_method(method, swapped, self.facts[key], "full")
                    self.assertFalse(out["decision"] == "matched" and out["top_row_key"] == original["top_row_key"],
                                     (method, case_id, swap))
                    checked += 1
        self.assertGreater(len(per_pair), 20)
        self.assertGreater(checked, 500)


if __name__ == "__main__":
    unittest.main()
