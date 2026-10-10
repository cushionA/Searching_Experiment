from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest


PATH = Path(__file__).resolve().parents[1] / "experiments/sku-matching/recover_claude_unresolved_rows_v1.py"
SPEC = importlib.util.spec_from_file_location("claude_unresolved_recovery", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def span(text):
    return {"quote": text, "start": 0, "end": len(text), "raw_file": "synthetic-fixture-only.json", "sha256": "fixture"}


def rak_axis(number, name, value, options):
    return {"axis_index": number, "axis_key": name, "axis_label": name, "value": value,
            "family_values": options, "axis_label_span": span(name), "value_span": span(value)}


def au_axis(name, value):
    return {"axis_name": name, "value": value, "axis_name_span": span(name), "value_span": span(value)}


def fixture(axes, rows):
    case = {"case_id": "fixture-case", "dossier_id": "fixture-pair", "au_product_id": "fixture-au",
            "rakuten": {"source_sku_key": "fixture-sku", "axes": axes}}
    product = {"dossier_id": "fixture-pair", "au": {"product_id": "fixture-au", "rows": rows}}
    return case, product


class RecoveryTests(unittest.TestCase):
    def test_symmetric_unknown_is_handoff(self):
        case, product = fixture([rak_axis(0, "形式", "選択A", ["選択A", "選択B"])],
                                [{"row_key": "rowA", "axes": [au_axis("構成", "形式A")]}])
        maps = {("fixture-pair", "形式", "選択A"): {"status": "not_mutual_best"}}
        axes = {("fixture-pair", "形式"): {"kind": "symmetric", "au_axis": "構成"}}
        rows, skipped = MODULE.recover_case(case, product, maps, axes)
        self.assertEqual(skipped, 0)
        self.assertEqual(rows[0]["upstream_row_state"], "symmetric_unresolved")
        self.assertEqual(rows[0]["row_state"], "pending_description_check")
        self.assertEqual(MODULE.cards_from_row(rows[0], {})[0]["upstream_condition_status"], "symmetric_unresolved")

    def test_other_axis_contradiction_excludes_whole_row(self):
        selected = [rak_axis(0, "形式", "選択A", ["選択A", "選択B"]), rak_axis(1, "色", "青", ["青", "赤"])]
        case, product = fixture(selected, [{"row_key": "rowA", "axes": [au_axis("構成", "形式A"), au_axis("色", "赤")]}])
        maps = {("fixture-pair", "形式", "選択A"): {"status": "not_mutual_best"},
                ("fixture-pair", "色", "青"): {"status": "mapped", "option": {"axis_name": "色", "value": "青"}}}
        axes = {("fixture-pair", "形式"): {"kind": "symmetric", "au_axis": "構成"},
                ("fixture-pair", "色"): {"kind": "symmetric", "au_axis": "色"}}
        rows, skipped = MODULE.recover_case(case, product, maps, axes)
        self.assertEqual((rows, skipped), ([], 1))
        row = MODULE.derive_row(case, product["au"]["rows"][0], maps, axes, set())
        self.assertEqual(MODULE.cards_from_row(row, {}), [])

    def test_composite_value_is_not_partly_consumed(self):
        selected = rak_axis(0, "タイプ", "本棚付き / 天然木", ["本棚付き / 天然木", "本棚なし / 天然木"])
        case, product = fixture([selected], [{"row_key": "rowA", "axes": [au_axis("材質", "天然木")]}])
        maps = {("fixture-pair", "タイプ", selected["value"]): {"status": "one_side_has_more", "option": {"axis_name": "材質", "value": "天然木"}}}
        axes = {("fixture-pair", "タイプ"): {"kind": "symmetric", "au_axis": "材質"}}
        row = MODULE.derive_row(case, product["au"]["rows"][0], maps, axes, set())
        card = MODULE.cards_from_row(row, {})[0]
        self.assertEqual(card["selected_value"], "本棚付き / 天然木")
        self.assertEqual(card["raw_condition"], selected)
        self.assertEqual(card["option_values"], selected["family_values"])
        self.assertTrue(row["reverse_check_pending"])
        reverse = MODULE.reverse_conditions_from_row(row, product)[0]
        self.assertEqual(reverse["selected_value"], "天然木")
        self.assertEqual(reverse["rakuten_whole_condition"], selected)

    def test_all_noncontradictory_candidates_survive(self):
        case, product = fixture([rak_axis(0, "形式", "選択A", ["選択A", "選択B"])],
                                [{"row_key": key, "axes": [au_axis("構成", value)]} for key, value in [("a", "形式A"), ("b", "形式B")]])
        maps = {("fixture-pair", "形式", "選択A"): {"status": "not_mutual_best"}}
        axes = {("fixture-pair", "形式"): {"kind": "symmetric", "au_axis": "構成"}}
        frozen = copy.deepcopy((case, product, maps, axes))
        rows, skipped = MODULE.recover_case(case, product, maps, axes)
        self.assertEqual([row["au_row_key"] for row in rows], ["a", "b"])
        self.assertEqual(skipped, 0)
        self.assertTrue(all(not row["row_adopted"] and not row["automatic_adoption_allowed"] for row in rows))
        self.assertEqual((case, product, maps, axes), frozen)

    def test_longer_au_value_is_saved_for_reverse_check(self):
        selected = rak_axis(0, "色", "ホワイトグレージュ", ["ホワイトグレージュ", "青"])
        case, product = fixture([selected], [{"row_key": "a", "axes": [au_axis("カラー", "ホワイトグレージュ（パイル）")]},
                                             {"row_key": "b", "axes": [au_axis("カラー", "青（パイル）")]}])
        maps = {("fixture-pair", "色", selected["value"]): {"status": "one_side_has_more", "option": {"axis_name": "カラー", "value": "ホワイトグレージュ（パイル）"}}}
        axes = {("fixture-pair", "色"): {"kind": "symmetric", "au_axis": "カラー"}}
        row = MODULE.derive_row(case, product["au"]["rows"][0], maps, axes, set())
        reverse = MODULE.reverse_conditions_from_row(row, product)[0]
        self.assertEqual(reverse["direction"], "au_to_rakuten")
        self.assertEqual(reverse["selected_value"], "ホワイトグレージュ（パイル）")
        self.assertEqual(reverse["option_values"], ["ホワイトグレージュ（パイル）", "青（パイル）"])
        self.assertEqual(reverse["raw_condition"], product["au"]["rows"][0]["axes"][0])
        self.assertFalse(reverse["automatic_adoption_allowed"])

    def test_au_only_varying_is_not_forward_card(self):
        case, product = fixture([rak_axis(0, "色", "青", ["青", "赤"])],
                                [{"row_key": "a", "axes": [au_axis("色", "青"), au_axis("加工", "加工A")]},
                                 {"row_key": "b", "axes": [au_axis("色", "青"), au_axis("加工", "加工B")]}])
        maps = {("fixture-pair", "色", "青"): {"status": "exact_string", "option": {"axis_name": "色", "value": "青"}}}
        axes = {("fixture-pair", "色"): {"kind": "symmetric", "au_axis": "色"}}
        rows, _ = MODULE.recover_case(case, product, maps, axes)
        self.assertEqual(MODULE.cards_from_row(rows[0], {}), [])
        reverse = MODULE.reverse_conditions_from_row(rows[0], product)[0]
        self.assertEqual(reverse["reason"], "au_only_varying")
        self.assertEqual(reverse["option_values"], ["加工A", "加工B"])
        self.assertTrue(rows[0]["reverse_check_pending"])

    def test_missing_mapping_and_duplicate_axes_fail(self):
        case, product = fixture([rak_axis(0, "色", "青", ["青"])], [{"row_key": "a", "axes": [au_axis("色", "青")]}])
        with self.assertRaises(KeyError):
            MODULE.derive_row(case, product["au"]["rows"][0], {}, {}, set())
        with self.assertRaises(ValueError):
            MODULE.index([{"axis_name": "色"}, {"axis_name": "色"}], "axis_name")

    def test_answer_validation_rejects_missing_duplicate_and_invalid_choices(self):
        question = {"question_id": "q1", "dossier_id": "fixture", "axis_key": "k", "value": "v", "au_options": [{"axis_name": "k", "value": "v"}]}
        answers = [{"question_id": "q1", "order": order, "choice": 0} for order in ("forward", "reverse")]
        MODULE.validate_questions([question], answers)
        for bad in (answers[:1], answers + [answers[0]], [{**answers[0], "choice": 1}, answers[1]], [{**answers[0], "choice": True}, answers[1]]):
            with self.assertRaises(ValueError):
                MODULE.validate_questions([question], bad)


if __name__ == "__main__":
    unittest.main()
