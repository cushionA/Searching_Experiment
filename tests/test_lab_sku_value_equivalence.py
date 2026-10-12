"""Value equivalence proofs over axis names and option lists, with adversarial pairs that must stay unproven."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/sku-matching"))
import prove_value_equivalence as pv  # noqa: E402


def case_variant(r, a, r_options, a_options):
    return pv.ascii_case_variant(r, a, r_options, a_options)


def failed(attempt):
    return [c["name"] for c in attempt["checks"] if not c["passed"]]


class CaseVariantTests(unittest.TestCase):
    def test_letters_away_from_digits_are_proven(self):
        x = case_variant("Black", "BLACK", ["Black", "White"], ["BLACK", "WHITE"])
        self.assertTrue(x["proven"])
        self.assertEqual([c["rakuten"] for c in x["changed_characters"]], ["l", "a", "c", "k"])

    def test_letters_touching_digits_stay_unproven(self):
        for r, a in [("ゆで250g×2P", "ゆで250g×2p"), ("5g", "5G"), ("1GB", "1Gb"), ("10mW", "10MW"), ("1m", "1M")]:
            x = case_variant(r, a, [r], [a])
            self.assertEqual(failed(x), ["no_changed_letter_touches_a_digit"], (r, a))

    def test_case_that_separates_options_stays_unproven(self):
        x = case_variant("S", "s", ["S", "s", "M"], ["s", "m"])
        self.assertIn("case_separates_nothing_on_rakuten_options", failed(x))
        x = case_variant("Ab", "AB", ["Ab", "Cd"], ["AB", "cd"])
        self.assertTrue(x["proven"])
        # The changed letter b is written both ways on the Rakuten list, so its case may mean something there.
        x = case_variant("Ab", "AB", ["Ab", "xB"], ["AB", "XB"])
        self.assertIn("case_separates_nothing_on_rakuten_options", failed(x))

    def test_other_differences_are_not_case_variants(self):
        for r, a in [("15℃", "-15℃"), ("100cm", "最大100cm"), ("あぶり250g", "炙り250g"), ("S", "XS")]:
            self.assertEqual(failed(case_variant(r, a, [r], [a])), ["differs_only_in_ascii_letter_case"], (r, a))


class AxisNameAffixTests(unittest.TestCase):
    def affix(self, r, a, r_axis, a_axis, r_options, a_options):
        return pv.own_axis_name_affix(r, a, r_axis, a_axis, r_options, a_options)

    def test_size_suffix_that_repeats_the_axis_name_is_proven(self):
        x = self.affix("L", "Lサイズ", "サイズ", "サイズ", ["M", "L", "LL"], ["Mサイズ", "Lサイズ", "LLサイズ"])
        self.assertTrue(x["proven"])
        self.assertEqual((x["removed_text"], x["full_option_list_bijection"]), ("サイズ", True))

    def test_adversarial_affixes_stay_unproven(self):
        cases = [
            (("1", "1号", "号数", "号数", ["1", "2"], ["1号", "2号"]), "one_core_is_the_other_plus_the_axis_name"),
            (("500", "500ml", "容量", "容量", ["500"], ["500ml"]), "one_core_is_the_other_plus_the_axis_name"),
            (("L", "Lサイズ", "サイズ", "サイズ表記", ["L"], ["Lサイズ"]), "same_axis_name"),
            (("L", "Lサイズ", "サイズ", "サイズ", ["L", "M"], ["Lサイズ", "M"]), "axis_name_on_every_au_option"),
            (("L", "Lサイズ", "サイズ", "サイズ", ["L", "Mサイズ"], ["Lサイズ", "Mサイズ"]),
             "axis_name_on_no_option_of_the_other_side"),
            (("", "サイズ", "サイズ", "サイズ", [""], ["サイズ"]), "one_core_is_the_other_plus_the_axis_name"),
        ]
        for args, first in cases:
            self.assertEqual(failed(self.affix(*args))[0], first, args)


class SidecarTests(unittest.TestCase):
    def test_proof_covers_cores_and_keeps_residuals(self):
        cond = {"kind": "rakuten", "axis_key": "s", "value": "L（24.0〜24.5）", "au_axis": "サイズ",
                "au_value": "Lサイズ(目安：24.0〜24.5m)", "status": "model_candidate",
                "core": {"identical": False, "rakuten": ["L"], "au": ["Lサイズ"]},
                "residual": [{"side": "rakuten", "text": "24.0〜24.5"}, {"side": "au", "text": "目安：24.0〜24.5m"}]}
        row = {"case_id": "c", "dossier_id": "d", "au_row_key": "r1",
               "au_axis_options": {"サイズ": ["Mサイズ(目安：23.0〜23.5cm)", "Lサイズ(目安：24.0〜24.5m)"]}}
        case = {"rakuten_selected": {"axes": [{"axis_key": "s", "axis_label": "サイズ", "value": "L（24.0〜24.5）",
                                               "family_values": ["M（23.0〜23.5）", "L（24.0〜24.5）"]}]}}
        product = {"au_rows": [{"axes": [{"axis_name": "サイズ", "value": v}]} for v in row["au_axis_options"]["サイズ"]]}
        x = pv.prove(cond, row, case, product)
        self.assertEqual((x["verdict"], x["proof_type"], x["residual_unconfirmed"]),
                         ("proven_equivalent", "own_axis_name_affix", True))
        self.assertEqual(x["preserved"]["residual"], cond["residual"])


if __name__ == "__main__":
    unittest.main()
