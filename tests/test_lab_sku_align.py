"""The table steps of the SKU condition alignment, without a model."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/sku-matching"))
import align_sku_conditions as al  # noqa: E402


def entry(rakuten, au, forward, reverse):
    return {"rakuten": rakuten, "au": au, "forward": forward, "reverse": reverse}


RAKUTEN = [["size", "サイズ", "100×80cm"], ["colour", "カラー", "グレー"], ["colour", "カラー", "ブラック"],
           ["colour", "カラー", "ネイビー"], ["set", "セット数", "10個"]]
AU = [["サイズ", "幅100×丈80cm(2枚)"], ["カラー", "グレイ"], ["カラー", "ブラック"], ["カラー", "ホワイト"]]
# forward: Rakuten x AU, reverse: AU x Rakuten. ブラック leans on グレイ but is identical to ブラック;
# ネイビー leans on グレイ but loses it to グレー; 10個 and ホワイト are each other's best match.
FORWARD = [[9, 0, 0, 0], [0, 6, 3, 2], [0, 5, 1, 0], [0, 4, 3, 1], [0, 1, 1, 3]]
REVERSE = [[9, 0, 0, 0, 0], [0, 6, 2, 5, 0], [0, 5, 1, 0, 0], [0, 2, 1, 1, 3]]


def rows_of(*values, axes):
    return {"au_rows": [{"row_key": f"r{i}", "axes": [{"axis_name": n, "value": v} for n, v in zip(axes, row)]}
                        for i, row in enumerate(values)]}


def run(table, au, context, *values):
    symmetric, au_only = al.axis_pairs(table, au)
    case = {"rakuten_selected": {"axes": [{"axis_key": k, "axis_label": label, "value": v} for k, label, v in values]}}
    states = al.row_states(al.conditions(case, context, table, symmetric, au_only))
    return states.to_dict(), al.decide(states)


class TableTests(unittest.TestCase):
    def setUp(self):
        self.table = al.value_pairs(entry(RAKUTEN, AU, FORWARD, REVERSE))

    def test_identical_strings_rank_first_and_pairs_are_mutual(self):
        got = {(r.value, r.au_value): (r.paired, r.matched_by) for r in self.table.itertuples()}
        self.assertEqual(got[("ブラック", "ブラック")], (True, "identical"))
        self.assertEqual(got[("グレー", "グレイ")], (True, "model"))
        self.assertEqual(got[("10個", "ホワイト")], (True, "model"))
        self.assertFalse(got[("ネイビー", "グレイ")][0])

    def test_axes_pair_one_to_one(self):
        # Model pairs join axes too: サイズ has no identical value. 10個-ホワイト also reaches カラー,
        # so カラー has two Rakuten axes and pairs with neither.
        self.assertEqual(al.axis_pairs(self.table, AU), ({"size": "サイズ"}, ["カラー"]))
        # Two Rakuten axes reaching one AU axis are not paired.
        t = al.value_pairs(entry([["a", "カラー", "グレー"], ["b", "タイプ", "ブラック"]],
                                 [["カラー", "グレー"], ["カラー", "ブラック"]], [[0, 0], [0, 0]], [[0, 0], [0, 0]]))
        self.assertEqual(al.axis_pairs(t, [["カラー", "グレー"], ["カラー", "ブラック"]])[0], {})

    def test_axis_without_identical_values_pairs_through_model_pairs(self):
        # Curtain sizes: no string is identical, but the model pairs them mutually, so the axes pair.
        rakuten = [["size", "サイズ", "100×80cm"], ["size", "サイズ", "100×105cm"], ["colour", "カラー", "ブラック"]]
        au = [["サイズ", "幅100×丈80cm(2枚)"], ["サイズ", "幅100×丈105cm(2枚)"], ["カラー", "ブラック"]]
        table = al.value_pairs(entry(rakuten, au, [[5, 3, 0], [3, 5, 0], [0, 0, 0]], [[5, 3, 0], [3, 5, 0], [0, 0, 0]]))
        self.assertEqual(set(table.loc[table["axis_key"] == "size", "matched_by"]), {"model"})
        self.assertEqual(al.axis_pairs(table, au), ({"size": "サイズ", "colour": "カラー"}, []))
        # The other size's row is a contradiction; the model pair's row still goes to the description check.
        context = rows_of(("幅100×丈80cm(2枚)", "ブラック"), ("幅100×丈105cm(2枚)", "ブラック"), axes=("サイズ", "カラー"))
        states, decision = run(table, au, context, ("size", "サイズ", "100×80cm"), ("colour", "カラー", "ブラック"))
        self.assertEqual(states, {"r0": "pending_description_check", "r1": "contradiction"})
        self.assertEqual(decision, ("unmatched", None, "pending_description_check"))

    def test_wrong_axis_link_is_neither_aligned_nor_adopted(self):
        # 10個 and ホワイト are each other's best match, so the set axis pairs with the AU colour axis.
        rakuten = [["size", "サイズ", "S"], ["size", "サイズ", "M"], ["set", "セット数", "10個"]]
        au = [["サイズ", "S"], ["サイズ", "M"], ["カラー", "ホワイト"], ["カラー", "ブラック"]]
        table = al.value_pairs(entry(rakuten, au, [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 3, 1]],
                                     [[0, 0, 0], [0, 0, 0], [0, 0, 3], [0, 0, 1]]))
        symmetric, au_only = al.axis_pairs(table, au)
        self.assertEqual((symmetric, au_only), ({"size": "サイズ", "set": "カラー"}, []))
        context = rows_of(("S", "ホワイト"), ("S", "ブラック"), ("M", "ホワイト"), ("M", "ブラック"), axes=("サイズ", "カラー"))
        case = {"rakuten_selected": {"axes": [{"axis_key": "size", "axis_label": "サイズ", "value": "S"},
                                              {"axis_key": "set", "axis_label": "セット数", "value": "10個"}]}}
        conds = al.conditions(case, context, table, symmetric, au_only)
        self.assertEqual(set(conds.loc[conds["axis_key"] == "set", "status"]), {"model_candidate", "contradiction"})
        states = al.row_states(conds)
        self.assertNotIn("aligned", set(states))
        self.assertEqual(al.decide(states), ("unmatched", None, "pending_description_check"))

    def test_rows_and_decision(self):
        # Without 10個 the colour axis pairs; the size axis pairs through its model pair.
        table = al.value_pairs(entry(RAKUTEN[:4], AU, FORWARD[:4], [r[:4] for r in REVERSE]))
        context = rows_of(*[("幅100×丈80cm(2枚)", c) for c in ["ブラック", "グレイ", "ホワイト"]], axes=("サイズ", "カラー"))

        def run_here(*values):
            return run(table, AU, context, *values)

        black, grey = ("colour", "カラー", "ブラック"), ("colour", "カラー", "グレー")
        self.assertEqual(run_here(black)[1], ("matched", "r0", "all_conditions_aligned_on_one_row"))
        # A model pair is only a candidate: its row goes to the description check, the others are excluded.
        self.assertEqual(run_here(grey)[0], {"r0": "contradiction", "r1": "pending_description_check", "r2": "contradiction"})
        self.assertEqual(run_here(black, ("size", "サイズ", "100×80cm"))[0]["r0"], "pending_description_check")
        # Conditions on one-sided axes also go to the description check.
        self.assertEqual(run_here(black, ("set", "セット数", "10個"))[0]["r0"], "pending_description_check")
        # A value of a symmetric axis with no AU counterpart is excluded, not handed off.
        self.assertEqual(run_here(("colour", "カラー", "ネイビー"))[1][2], "symmetric_condition_unresolved")


if __name__ == "__main__":
    unittest.main()
