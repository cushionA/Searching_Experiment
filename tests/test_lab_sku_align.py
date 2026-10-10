"""The table steps of the SKU condition alignment, without a model."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/sku-matching"))
import align_sku_conditions as al  # noqa: E402


def entry(rakuten, au, forward, reverse):
    return {"rakuten": rakuten, "au": au, "forward": forward, "reverse": reverse}


RAKUTEN = [["size", "サイズ", "100×80cm"], ["size", "サイズ", "100×200cm"], ["colour", "カラー", "グレー"],
           ["lace", "レースカーテン", "あり"], ["lace", "レースカーテン", "なし"]]
AU = [["サイズ", "幅100×丈80cm(2枚)"], ["カラー", "ダークグレー"], ["カラー", "グレー"]]
# forward: Rakuten x AU, reverse: AU x Rakuten. The lace values lean on the size option but lose the
# reverse match to the size value; 100×200cm has no AU option.
FORWARD = [[9, 0, 0], [8, 0, 0], [0, 5, 4], [3, 0, 0], [3, 0, 0]]
REVERSE = [[9, 8, 0, 3, 3], [0, 0, 6, 0, 0], [0, 0, 5, 0, 0]]


class TableTests(unittest.TestCase):
    def setUp(self):
        self.table = al.value_pairs(entry(RAKUTEN, AU, FORWARD, REVERSE))

    def test_identical_strings_rank_first_and_pairs_are_mutual(self):
        got = {(r.value, r.au_value): r.paired for r in self.table.itertuples()}
        # グレー scores lower than ダークグレー but is identical, so it ranks first both ways.
        self.assertTrue(got[("グレー", "グレー")])
        self.assertTrue(got[("100×80cm", "幅100×丈80cm(2枚)")])
        self.assertFalse(got[("100×200cm", "幅100×丈80cm(2枚)")])
        self.assertFalse(got[("あり", "幅100×丈80cm(2枚)")])

    def test_contained_strings_are_marked_extra(self):
        t = al.value_pairs(entry([["t", "タイプ", "スリム / グレー"]], [["カラー", "グレー"]], [[1]], [[1]]))
        self.assertEqual((bool(t.paired[0]), bool(t.extra[0])), (True, True))

    def test_axes_pair_one_to_one_and_the_rest_is_one_sided(self):
        symmetric, au_only = al.axis_pairs(self.table, AU)
        self.assertEqual(symmetric, {"size": "サイズ", "colour": "カラー"})
        self.assertEqual(au_only, [])
        # Two Rakuten axes reaching one AU axis are not paired.
        t = al.value_pairs(entry([["a", "カラー", "グレー"], ["b", "タイプ", "ダークグレー"]], AU[1:],
                                 [[0, 0], [0, 0]], [[0, 0], [0, 0]]))
        self.assertEqual(al.axis_pairs(t, AU[1:])[0], {})

    def test_rows_and_decision(self):
        context = {"au_rows": [{"row_key": "r0", "axes": [{"axis_name": "サイズ", "value": "幅100×丈80cm(2枚)"},
                                                          {"axis_name": "カラー", "value": "グレー"}]},
                               {"row_key": "r1", "axes": [{"axis_name": "サイズ", "value": "幅100×丈80cm(2枚)"},
                                                          {"axis_name": "カラー", "value": "ダークグレー"}]}]}
        symmetric, au_only = al.axis_pairs(self.table, AU)

        def case(*values):
            return {"rakuten_selected": {"axes": [{"axis_key": k, "axis_label": label, "value": v}
                                                  for k, label, v in values]}}

        conds = al.conditions(case(("size", "サイズ", "100×80cm"), ("colour", "カラー", "グレー")),
                              context, self.table, symmetric, au_only)
        self.assertEqual(al.decide(al.row_states(conds)), ("matched", "r0", "all_conditions_aligned_on_one_row"))
        # Whether the pair came from identical strings or from the model is kept on every aligned condition.
        aligned = conds[(conds["row_key"] == "r0")].set_index("axis_key")["matched_by"].to_dict()
        self.assertEqual(aligned, {"size": "model", "colour": "identical"})
        # A one-sided condition sends the surviving row to the description check.
        conds = al.conditions(case(("size", "サイズ", "100×80cm"), ("colour", "カラー", "グレー"), ("lace", "レースカーテン", "あり")),
                              context, self.table, symmetric, au_only)
        self.assertEqual(al.row_states(conds).to_dict(), {"r0": "pending_description_check", "r1": "contradiction"})
        # A value of a symmetric axis with no AU counterpart is excluded, not handed off.
        conds = al.conditions(case(("size", "サイズ", "100×200cm"), ("colour", "カラー", "グレー")),
                              context, self.table, symmetric, au_only)
        self.assertEqual(al.decide(al.row_states(conds))[2], "symmetric_condition_unresolved")


if __name__ == "__main__":
    unittest.main()
