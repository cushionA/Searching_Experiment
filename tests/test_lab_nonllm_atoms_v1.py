from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


MODULE = Path(__file__).resolve().parents[1] / "experiments/sku-matching/sku_nonllm_atoms_v1.py"
SPEC = importlib.util.spec_from_file_location("sku_nonllm_atoms_v1", MODULE)
atoms = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(atoms)


def base_atomize(raw, axis_label=None, color_vocab=frozenset(), family=()):
    out = {"raw": raw, "atoms": [], "residue": raw, "decomposition": "partial"}
    if "枚セット" in raw:
        start = raw.index(next(c for c in raw if c.isdigit()))
        end = start + 3
        out["atoms"].append({"type": "piece_total", "value": 64, "quote": raw[start:end],
                             "offset": [start, end]})
    return out


class NonLLMAtomTests(unittest.TestCase):
    def test_base_is_injected_and_its_residue_is_not_consumed(self):
        seen = []

        def injected(*args):
            seen.append(args)
            return {"raw": args[0], "atoms": [{"type": "variant", "value": "base"}],
                    "residue": "keep this residue", "decomposition": "partial"}

        result = atoms.atomize("100本", "本数", base_atomize=injected)
        self.assertEqual(seen[0][0:2], ("100本", "本数"))
        self.assertEqual(result["residue"], "keep this residue")
        quantity, = [a for a in result["atoms"] if a["type"] == "quantity"]
        self.assertEqual((quantity["value"], quantity["unit"], quantity["quantity_role"]),
                         (100, "本", "package_total"))
        self.assertEqual(quantity["quote"], "100本")
        self.assertEqual("100本"[slice(*quantity["offset"])], quantity["quote"])

    def test_quantities_keep_unit_and_semantic_role_separate(self):
        parsed = atoms.atomize("64枚セット(12畳用)", "セット", base_atomize=base_atomize)
        self.assertEqual(sum(a["type"] == "piece_total" for a in parsed["atoms"]), 1)
        area, = [a for a in parsed["atoms"] if a.get("unit") == "畳"]
        self.assertEqual((area["value"], area["quantity_role"]), (12, "area_capacity"))
        self.assertNotEqual(atoms.fact_family(area), atoms.fact_family({"type": "piece_total", "value": 64}))
        self.assertEqual(atoms.fact_family({"type": "tier_count", "value": 2},
                                           base_fact_family=lambda _: "legacy-tier"), "legacy-tier")

    def test_title_top_board_requires_explicit_polarity_and_negative_wins(self):
        base = lambda *_: {"component_presence:top_board": {
            "atoms": [{"type": "component_presence", "component": "top_board", "value": True}],
            "values": {True: [{}]}, "single_valued": True}}
        negative = atoms.title_facts("天板なしタイプ", base_title_facts=base)
        self.assertEqual(list(negative["component_presence:top_board"]["values"]), [False])
        self.assertEqual(negative["component_presence:top_board"]["atoms"][0]["quote"], "天板なし")
        positive = atoms.title_facts("【木製天板&小物入れ付き】", base_title_facts=lambda *_: {})
        self.assertTrue(positive["component_presence:top_board"]["atoms"][0]["value"])
        absent = atoms.title_facts("天板サイズを選べます", base_title_facts=lambda *_: {})
        self.assertNotIn("component_presence:top_board", absent)

    def test_missing_word_is_not_absence_and_ambiguous_dimension_array_is_not_count(self):
        parsed = atoms.atomize("100本", "本数", base_atomize=base_atomize)
        self.assertFalse(any(a.get("type") == "component_presence" and a.get("value") is False
                             for a in parsed["atoms"]))
        self.assertEqual(atoms.extract_quantity_facts("幅68×奥行28.5×高さ10/15cm"), [])
        self.assertEqual(atoms.extract_quantity_facts("説明文に数量なし", scope_tag="search_keywords"), [])

    def test_sibling_components_with_equal_counts_keep_distinct_facts(self):
        facts = atoms.extract_quantity_facts("サイドパーツ（角付き64本、角なし64本）")
        typed = [x["atom"] for x in facts]
        self.assertEqual([(x["value"], x["unit"], x["component"]) for x in typed],
                         [(64, "本", "corner_side_piece"), (64, "本", "noncorner_side_piece")])
        self.assertNotEqual(atoms.fact_family(typed[0]), atoms.fact_family(typed[1]))

    def test_width_axis_is_labeled_and_quotes_original_text(self):
        atom, = [a for a in atoms.atomize("８０ｃｍ", "サイズ(幅)")["atoms"]
                 if a["type"] == "dimension"]
        self.assertEqual((atom["labels"], atom["value"], atom["quote"]), (["width"], [80.0], "８０ｃｍ"))
        self.assertEqual("８０ｃｍ"[slice(*atom["offset"])], atom["quote"])

    def test_typed_quantity_replaces_only_fully_covered_variant_and_consumes_area_qualifier(self):
        def base(raw, *args):
            return {"raw": raw, "atoms": [{"type": "variant", "value": raw, "quote": raw,
                    "offset": [0, len(raw)]}], "residue": raw if "説明は不明" in raw else raw + "説明は不明",
                    "decomposition": "partial"}
        ten = atoms.atomize("10本", "本数", base_atomize=base)
        self.assertFalse(any(a["type"] == "variant" for a in ten["atoms"]))
        self.assertEqual(ten["residue"], "説明は不明")
        area = atoms.atomize("12畳用説明は不明", base_atomize=base)
        fact, = [a for a in area["atoms"] if a["type"] == "quantity"]
        self.assertEqual((fact["quote"], fact["quantity_role"]), ("12畳用", "area_capacity"))
        self.assertEqual(area["residue"], "説明は不明")
        self.assertTrue(any(a["type"] == "variant" for a in area["atoms"]))

    def test_existing_count_atoms_containing_quantity_literal_suppress_duplicate(self):
        def base(raw, *args):
            if "64枚セット" in raw:
                return {"raw": raw, "atoms": [{"type": "piece_total", "value": 64,
                        "quote": "64枚セット", "offset": [0, 6]}], "residue": "", "decomposition": "complete"}
            return {"raw": raw, "atoms": [{"type": "component_count", "component": "side_part",
                    "value": 64, "quote": "角付き64本", "offset": [0, 6]}],
                    "residue": "", "decomposition": "complete"}
        for raw in ("64枚セット", "角付き64本"):
            parsed = atoms.atomize(raw, base_atomize=base)
            self.assertFalse(any(a["type"] == "quantity" for a in parsed["atoms"]))

    def test_component_counts_inside_package_total_do_not_get_readded_as_quantities(self):
        raw = "13枚セット / パネル12枚 / ドア1枚"
        def base(text, *args):
            return {"raw": text, "atoms": [
                {"type": "piece_total", "value": 13, "quote": "13枚セット", "offset": [0, 6]},
                {"type": "component_count", "component": "panel", "value": 12,
                 "quote": "パネル12枚", "offset": [9, 15]},
                {"type": "component_count", "component": "door", "value": 1,
                 "quote": "ドア1枚", "offset": [18, 22]},
            ], "residue": "", "decomposition": "complete"}
        parsed = atoms.atomize(raw, base_atomize=base)
        self.assertEqual([(a["type"], a.get("component"), a["value"]) for a in parsed["atoms"]], [
            ("piece_total", None, 13), ("component_count", "panel", 12), ("component_count", "door", 1)])

    def test_area_role_is_same_for_title_capacity_and_conditional_copy(self):
        title = atoms.atomize("マット12畳", base_atomize=base_atomize)
        conditional = atoms.atomize("12畳用", base_atomize=base_atomize)
        a, = [x for x in title["atoms"] if x.get("unit") == "畳"]
        b, = [x for x in conditional["atoms"] if x.get("unit") == "畳"]
        self.assertEqual((a["quantity_role"], b["quantity_role"]), ("area_capacity", "area_capacity"))
        self.assertEqual(atoms.fact_family(a), atoms.fact_family(b))

    def test_generic_width_dimension_is_refined_in_place(self):
        def base(raw, *args):
            return {"raw": raw, "atoms": [{"type": "dimension", "role": "generic", "value": [68.0],
                    "quote": "68cm", "offset": [0, 4]}], "residue": "", "decomposition": "complete"}
        parsed = atoms.atomize("68cm", "サイズ(幅)", base_atomize=base)
        dimensions = [a for a in parsed["atoms"] if a["type"] == "dimension"]
        self.assertEqual(len(dimensions), 1)
        self.assertEqual((dimensions[0]["role"], dimensions[0]["labels"]), ("labeled", ["width"]))

    def test_title_quantity_dedup_and_contradictory_top_board_stays_ambiguous(self):
        def base_title(*_):
            atom = {"type": "quantity", "value": 10, "unit": "本", "component": None,
                    "quantity_role": "package_total", "quote": "10本", "offset": [0, 3]}
            return {atoms.fact_family(atom): {"atoms": [atom], "values": {10: [atom]}, "single_valued": True},
                    "variant": {"atoms": [{"type": "variant", "value": "10本", "offset": [0, 3]}],
                                "values": {"10本": [{}]}, "single_valued": True}}
        facts = atoms.title_facts("10本セット", base_title_facts=base_title, base_atomize=base_atomize)
        qfamily = atoms.fact_family({"type": "quantity", "value": 10, "unit": "本",
                                     "component": None, "quantity_role": "package_total"})
        self.assertEqual(len(facts[qfamily]["atoms"]), 1)
        self.assertNotIn("variant", facts)
        mixed = atoms.title_facts("天板なしタイプ / 天板ありタイプ", base_title_facts=lambda *_: {})
        self.assertEqual(set(mixed["component_presence:top_board"]["values"]), {False, True})
        self.assertFalse(mixed["component_presence:top_board"]["single_valued"])

    def test_balanced_qualifier_is_replaced_only_when_inner_text_is_exact(self):
        def base(raw, *args):
            if raw.startswith("(") and raw.endswith(")"):
                return {"raw": raw, "atoms": [{"type": "qualifier", "value": "12畳用",
                        "quote": raw, "offset": [0, len(raw)]}], "residue": "", "decomposition": "complete"}
            return {"raw": raw, "atoms": [], "residue": raw, "decomposition": "partial"}
        exact = atoms.atomize("(12畳用)", base_atomize=base)
        self.assertEqual([a["type"] for a in exact["atoms"]], ["quantity"])
        self.assertEqual(exact["atoms"][0]["quote"], "12畳用")
        unknown = atoms.atomize("(12畳用対象)", base_atomize=base)
        self.assertIn("qualifier", [a["type"] for a in unknown["atoms"]])


if __name__ == "__main__":
    unittest.main()
